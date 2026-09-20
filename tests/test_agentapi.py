from __future__ import annotations

import unittest
from unittest.mock import patch

from open_webui.utils.agentapi import _iter_sse, create_session, event_text, stream_turn_events


class FakeContent:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    async def iter_any(self):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, *, data=None, chunks=None, status: int = 200):
        self.data = data
        self.status = status
        self.content = FakeContent(chunks or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self.data

    async def read(self):
        return b''

    async def text(self):
        return ''


class FakeSession:
    def __init__(self):
        self.get_calls = []
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if url.endswith('/events'):
            return FakeResponse(data={'data': [{'id': 'event-before-turn'}]})
        return FakeResponse(
            chunks=[
                b'data: {"type":"agent.message","content":[{"type":"text","text":"old"}]}\n\n',
                b'data: {"type":"session.status_idle"}\n\n',
                b'data: {"type":"session.status_running"}\n\n',
                b'data: {"type":"agent.message","message":{"content":[',
                b'{"type":"text","text":"hello"}]}}\n\n',
                b'data: {"type":"session.status_idle"}\n\n',
            ]
        )

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if url.endswith('/v1/sessions'):
            return FakeResponse(data={'id': 'session-1', 'status': 'idle'})
        return FakeResponse(data={'data': []})


class AgentAPIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_session_uses_native_agent_reference(self):
        fake_session = FakeSession()
        with patch('open_webui.utils.agentapi.aiohttp.ClientSession', return_value=fake_session):
            result = await create_session(
                base_url='https://agent.qiniuapi.com',
                api_key='secret',
                agent_id='agent-1',
                agent_version=3,
                environment_id='env-1',
                title='Task',
                metadata={'open_webui_chat_id': 'chat-1'},
            )

        self.assertEqual(result['id'], 'session-1')
        _, request = fake_session.post_calls[0]
        self.assertEqual(request['headers']['x-api-key'], 'secret')
        self.assertEqual(request['json']['agent'], {'type': 'agent', 'id': 'agent-1', 'version': 3})
        self.assertEqual(request['json']['environment_id'], 'env-1')

    async def test_turn_starts_after_latest_event_and_stops_on_idle(self):
        fake_session = FakeSession()
        with patch('open_webui.utils.agentapi.aiohttp.ClientSession', return_value=fake_session):
            events = [
                event
                async for event in stream_turn_events(
                    base_url='https://agent.qiniuapi.com',
                    api_key='secret',
                    session_id='session-1',
                    text='do the task',
                )
            ]

        self.assertEqual(
            [event['type'] for event in events],
            ['session.status_running', 'agent.message', 'session.status_idle'],
        )
        _, stream_request = fake_session.get_calls[1]
        self.assertEqual(stream_request['headers']['Last-Event-ID'], 'event-before-turn')
        _, send_request = fake_session.post_calls[0]
        self.assertEqual(
            send_request['json'],
            {
                'events': [
                    {
                        'type': 'user.message',
                        'input': {'parts': [{'type': 'text', 'text': 'do the task'}]},
                    }
                ]
            },
        )
        self.assertEqual(event_text(events[1]), 'hello')

    async def test_sse_parser_handles_split_frames_and_event_ids(self):
        response = FakeResponse(
            chunks=[
                b'id: event-1\ndata: {"type":"agent.',
                b'message",\ndata: "text":"hello"}\n\n',
            ]
        )
        events = [event async for event in _iter_sse(response)]
        self.assertEqual(events, [{'type': 'agent.message', 'text': 'hello', '_sse_id': 'event-1'}])

    def test_event_text_accepts_durable_and_preview_shapes(self):
        self.assertEqual(
            event_text({'type': 'agent.message', 'content': [{'type': 'text', 'text': 'direct'}]}),
            'direct',
        )
        self.assertEqual(
            event_text(
                {
                    'type': 'agent.message',
                    'message': {'content': [{'type': 'text', 'text': {'value': 'nested'}}]},
                }
            ),
            'nested',
        )
        self.assertEqual(event_text({'type': 'session.status_idle', 'text': 'ignored'}), '')


if __name__ == '__main__':
    unittest.main()
