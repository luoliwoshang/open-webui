"""Minimal native client for Qiniu AgentAPI's session and event protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Coroutine
from typing import Any
from urllib.parse import quote

import aiohttp

log = logging.getLogger(__name__)

_STREAM_END = object()
_agent_turn_tasks: set[asyncio.Task[None]] = set()


class AgentTurnChannel:
    """Best-effort event delivery that never owns the AgentAPI turn lifecycle."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._attached = True

    def publish(self, data: bytes) -> None:
        if self._attached:
            self._queue.put_nowait(data)

    def close(self) -> None:
        if self._attached:
            self._queue.put_nowait(_STREAM_END)

    async def stream(self) -> AsyncIterator[bytes]:
        try:
            while True:
                item = await self._queue.get()
                if item is _STREAM_END:
                    return
                if isinstance(item, bytes):
                    yield item
        finally:
            # StreamingResponse cancels this iterator when the browser leaves.
            # Detach only the delivery channel; the independently held worker
            # must continue polling AgentAPI and persist the completed turn.
            self._attached = False
            while not self._queue.empty():
                self._queue.get_nowait()


def _agent_turn_done(task: asyncio.Task[None]) -> None:
    _agent_turn_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error:
        log.error(
            'Uncaught error in background AgentAPI turn',
            exc_info=(type(error), error, error.__traceback__),
        )


def start_agent_turn(coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    """Keep a strong reference so a disconnected response cannot stop the turn."""
    task = asyncio.create_task(coroutine)
    _agent_turn_tasks.add(task)
    task.add_done_callback(_agent_turn_done)
    return task


class AgentAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _url(base_url: str, path: str) -> str:
    return f'{base_url.rstrip("/")}{path}'


def _headers(api_key: str) -> dict[str, str]:
    return {'x-api-key': api_key, 'accept': 'application/json'}


async def _error_message(response: aiohttp.ClientResponse) -> str:
    try:
        body = await response.json()
        if isinstance(body, dict):
            error = body.get('error')
            if isinstance(error, dict) and error.get('message'):
                return str(error['message'])
            if body.get('detail'):
                return str(body['detail'])
            if body.get('message'):
                return str(body['message'])
        return json.dumps(body, ensure_ascii=False)
    except Exception:
        text = (await response.text()).strip()
        return text or f'AgentAPI returned HTTP {response.status}'


async def _raise_for_status(response: aiohttp.ClientResponse) -> None:
    if response.status < 400:
        return
    message = await _error_message(response)
    raise AgentAPIError(message, response.status if 400 <= response.status < 500 else 502)


async def create_session(
    *,
    base_url: str,
    api_key: str,
    agent_id: str,
    agent_version: int,
    environment_id: str,
    title: str,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = {
        'agent': {'type': 'agent', 'id': agent_id, 'version': agent_version},
        'environment_id': environment_id,
        'title': title,
        'metadata': metadata or {},
    }
    timeout = aiohttp.ClientTimeout(total=60, connect=15)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.post(_url(base_url, '/v1/sessions'), headers=_headers(api_key), json=payload) as response:
            await _raise_for_status(response)
            data = await response.json()
    if not isinstance(data, dict) or not data.get('id'):
        raise AgentAPIError('AgentAPI returned a session without an id')
    return data


async def delete_session(*, base_url: str, api_key: str, session_id: str) -> None:
    timeout = aiohttp.ClientTimeout(total=30, connect=15)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.delete(
            _url(base_url, f'/v1/sessions/{quote(session_id, safe="")}'), headers=_headers(api_key)
        ) as response:
            if response.status != 404:
                await _raise_for_status(response)


async def get_session(*, base_url: str, api_key: str, session_id: str) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=30, connect=15)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.get(
            _url(base_url, f'/v1/sessions/{quote(session_id, safe="")}'), headers=_headers(api_key)
        ) as response:
            await _raise_for_status(response)
            return await response.json()


async def verify_connection(*, base_url: str, api_key: str) -> None:
    timeout = aiohttp.ClientTimeout(total=30, connect=15)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.get(
            _url(base_url, '/v1/sessions'),
            headers=_headers(api_key),
            params={'limit': 1},
        ) as response:
            await _raise_for_status(response)


async def list_session_files(*, base_url: str, api_key: str, session_id: str) -> list[dict[str, Any]]:
    """List every file scoped to a Session, following AgentAPI cursors."""
    timeout = aiohttp.ClientTimeout(total=60, connect=15)
    files: list[dict[str, Any]] = []
    page: str | None = None
    seen_pages: set[str] = set()

    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        while True:
            params: dict[str, Any] = {'scope_id': session_id, 'limit': 1000}
            if page:
                params['page'] = page
            async with session.get(_url(base_url, '/v1/files'), headers=_headers(api_key), params=params) as response:
                await _raise_for_status(response)
                payload = await response.json()

            if not isinstance(payload, dict):
                raise AgentAPIError('AgentAPI returned an invalid file list')
            data = payload.get('data')
            if isinstance(data, list):
                files.extend(item for item in data if isinstance(item, dict))

            next_page = payload.get('next_page')
            if not next_page:
                return files
            page = str(next_page)
            if page in seen_pages:
                raise AgentAPIError('AgentAPI returned a repeated file-list cursor')
            seen_pages.add(page)


async def get_file(*, base_url: str, api_key: str, file_id: str) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=30, connect=15)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.get(
            _url(base_url, f'/v1/files/{quote(file_id, safe="")}'), headers=_headers(api_key)
        ) as response:
            await _raise_for_status(response)
            payload = await response.json()
    if not isinstance(payload, dict):
        raise AgentAPIError('AgentAPI returned invalid file metadata')
    return payload


async def stream_file_content(*, base_url: str, api_key: str, file_id: str) -> AsyncIterator[bytes]:
    """Stream a file through Open WebUI without exposing the AgentAPI key."""
    timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=900)
    session = aiohttp.ClientSession(timeout=timeout, trust_env=True)
    try:
        response = await session.get(
            _url(base_url, f'/v1/files/{quote(file_id, safe="")}/content'), headers=_headers(api_key)
        )
        try:
            await _raise_for_status(response)
            async for chunk in response.content.iter_chunked(64 * 1024):
                yield chunk
        finally:
            response.release()
    finally:
        await session.close()


async def send_events(*, base_url: str, api_key: str, session_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=60, connect=15)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.post(
            _url(base_url, f'/v1/sessions/{quote(session_id, safe="")}/events'),
            headers=_headers(api_key),
            json={'events': events},
        ) as response:
            await _raise_for_status(response)
            return await response.json()


async def _iter_sse(response: aiohttp.ClientResponse) -> AsyncIterator[dict[str, Any]]:
    """Parse SSE frames while preserving the event id used for resuming streams."""
    buffer = ''
    async for chunk in response.content.iter_any():
        buffer += chunk.decode('utf-8', errors='replace').replace('\r\n', '\n')
        while '\n\n' in buffer:
            raw_frame, buffer = buffer.split('\n\n', 1)
            data_lines: list[str] = []
            event_id: str | None = None
            for line in raw_frame.split('\n'):
                if line.startswith('data:'):
                    data_lines.append(line[5:].lstrip())
                elif line.startswith('id:'):
                    event_id = line[3:].strip()
            if not data_lines:
                continue
            try:
                event = json.loads('\n'.join(data_lines))
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                if event_id and not event.get('id'):
                    event['_sse_id'] = event_id
                yield event


async def stream_turn_events(
    *, base_url: str, api_key: str, session_id: str, text: str
) -> AsyncIterator[dict[str, Any]]:
    """Submit a user turn and incrementally poll its durable events until completion."""
    session_path = quote(session_id, safe='')
    headers = _headers(api_key)
    timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=900)

    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        # Resume after the newest committed event so an old status_idle cannot
        # accidentally terminate a later turn.
        async with session.get(
            _url(base_url, f'/v1/sessions/{session_path}/events'),
            headers=headers,
            params={'limit': 1, 'order': 'desc'},
        ) as history_response:
            await _raise_for_status(history_response)
            history = await history_response.json()
            latest = ((history or {}).get('data') or [None])[0]
            latest_id = latest.get('id') if isinstance(latest, dict) else None

        # The public AgentAPI edge can buffer SSE responses, including response
        # headers, indefinitely. Submit the event first and poll the durable
        # event log from the pre-turn cursor. The Open WebUI endpoint still
        # streams each newly observed event to the browser as NDJSON.
        async with session.post(
            _url(base_url, f'/v1/sessions/{session_path}/events'),
            headers=headers,
            json={
                'events': [
                    {
                        'type': 'user.message',
                        'input': {'parts': [{'type': 'text', 'text': text}]},
                    }
                ]
            },
        ) as send_response:
            await _raise_for_status(send_response)
            await send_response.read()

        cursor = str(latest_id) if latest_id else None
        saw_running = False
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            params: dict[str, Any] = {'limit': 100, 'order': 'asc'}
            if cursor:
                params['page'] = cursor
            async with session.get(
                _url(base_url, f'/v1/sessions/{session_path}/events'),
                headers=headers,
                params=params,
            ) as events_response:
                await _raise_for_status(events_response)
                page = await events_response.json()

            events = page.get('data') if isinstance(page, dict) else None
            events = events if isinstance(events, list) else []
            for event in events:
                if not isinstance(event, dict):
                    continue
                event_id = event.get('id')
                if event_id:
                    cursor = str(event_id)
                event_type = str(event.get('type') or '')
                if event_type in {'session.status_running', 'session.status_rescheduling'}:
                    saw_running = True

                # Do not surface any replayed history if an upstream ignores
                # Last-Event-ID. New turn output starts after its running edge.
                if not saw_running:
                    continue

                yield event

                if event_type == 'session.status_idle' and saw_running:
                    return
                if event_type == 'session.status_terminated':
                    return

            if not events:
                await asyncio.sleep(0.5)

        raise AgentAPIError('Timed out waiting for AgentAPI turn events', 504)


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(_extract_text(item) for item in value)
    if not isinstance(value, dict):
        return ''

    text = value.get('text')
    if isinstance(text, str):
        return text
    if isinstance(text, dict) and isinstance(text.get('value'), str):
        return text['value']

    for key in ('content', 'parts'):
        nested = _extract_text(value.get(key))
        if nested:
            return nested
    return ''


def event_text(event: dict[str, Any]) -> str:
    """Extract text from the durable and preview shapes of an agent.message event."""
    if event.get('type') != 'agent.message':
        return ''

    for key in ('content', 'message', 'output', 'delta', 'text'):
        text = _extract_text(event.get(key))
        if text:
            return text
    return ''
