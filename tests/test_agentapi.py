from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from open_webui.utils.agentapi import (
    AgentTurnChannel,
    _iter_sse,
    create_session,
    event_text,
    list_session_files,
    start_agent_turn,
    stream_turn_events,
)


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
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        self.calls.append(('get', url))
        if url.endswith('/events'):
            if kwargs.get('params', {}).get('order') == 'desc':
                return FakeResponse(data={'data': [{'id': 'event-before-turn'}]})
            return FakeResponse(
                data={
                    'data': [
                        {'id': 'event-running', 'type': 'session.status_running'},
                        {
                            'id': 'event-message',
                            'type': 'agent.message',
                            'message': {'content': [{'type': 'text', 'text': 'hello'}]},
                        },
                        {'id': 'event-idle', 'type': 'session.status_idle'},
                    ],
                    'next_page': None,
                }
            )
        raise AssertionError(f'Unexpected GET request: {url}')

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        self.calls.append(('post', url))
        if url.endswith('/v1/sessions'):
            return FakeResponse(data={'id': 'session-1', 'status': 'idle'})
        return FakeResponse(data={'data': []})


class FakeFileSession(FakeSession):
    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        self.calls.append(('get', url))
        if not url.endswith('/v1/files'):
            raise AssertionError(f'Unexpected GET request: {url}')
        page = kwargs.get('params', {}).get('page')
        if page is None:
            return FakeResponse(data={'data': [{'id': 'file-1'}], 'next_page': 'cursor-1'})
        if page == 'cursor-1':
            return FakeResponse(data={'data': [{'id': 'file-2'}], 'next_page': None})
        raise AssertionError(f'Unexpected page: {page}')


class AgentAPIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_session_files_follows_cursors_with_session_scope(self):
        fake_session = FakeFileSession()
        with patch('open_webui.utils.agentapi.aiohttp.ClientSession', return_value=fake_session):
            files = await list_session_files(
                base_url='https://agent.qiniuapi.com',
                api_key='secret',
                session_id='session-1',
            )

        self.assertEqual(files, [{'id': 'file-1'}, {'id': 'file-2'}])
        self.assertEqual(len(fake_session.get_calls), 2)
        self.assertEqual(
            fake_session.get_calls[0][1]['params'],
            {'scope_id': 'session-1', 'limit': 1000},
        )
        self.assertEqual(
            fake_session.get_calls[1][1]['params'],
            {'scope_id': 'session-1', 'limit': 1000, 'page': 'cursor-1'},
        )

    async def test_turn_keeps_running_after_response_stream_disconnects(self):
        channel = AgentTurnChannel()
        resume = asyncio.Event()
        persisted = asyncio.Event()

        async def run_turn():
            channel.publish(b'{"type":"message.accepted"}\n')
            await resume.wait()
            persisted.set()
            channel.close()

        task = start_agent_turn(run_turn())
        stream = channel.stream()
        first = await anext(stream)
        self.assertIn(b'message.accepted', first)

        await stream.aclose()
        resume.set()
        await task

        self.assertTrue(persisted.is_set())
        self.assertFalse(task.cancelled())

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

    async def test_turn_posts_then_polls_after_previous_event(self):
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
        self.assertEqual(
            [method for method, _ in fake_session.calls],
            ['get', 'post', 'get'],
        )
        _, poll_request = fake_session.get_calls[1]
        self.assertEqual(
            poll_request['params'],
            {'limit': 100, 'order': 'asc', 'page': 'event-before-turn'},
        )
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
