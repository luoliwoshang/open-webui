from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx


class AgentAPIError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class AgentAPIClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Accept': 'application/json',
            },
            timeout=httpx.Timeout(60, connect=10),
        )

    async def _json(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with self._client() as client:
                response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise AgentAPIError(502, str(error)) from error
        if response.is_error:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise AgentAPIError(response.status_code, detail)
        return response.json() if response.content else {}

    async def verify(self, agent_id: str, environment_id: str) -> dict:
        agent = await self._json('GET', f'/v1/agents/{agent_id}', params={'beta': 'true'})
        environment = await self._json('GET', f'/v1/environments/{environment_id}', params={'beta': 'true'})
        return {'agent': agent, 'environment': environment}

    async def create_session(
        self,
        agent_id: str,
        environment_id: str,
        title: str,
        metadata: dict,
    ) -> dict:
        return await self._json(
            'POST',
            '/v1/sessions',
            params={'beta': 'true'},
            json={
                'agent': {'type': 'agent', 'id': agent_id},
                'environment_id': environment_id,
                'title': title,
                'metadata': metadata,
            },
        )

    async def get_session(self, session_id: str) -> dict:
        return await self._json('GET', f'/v1/sessions/{session_id}', params={'beta': 'true'})

    async def delete_session(self, session_id: str) -> None:
        await self._json('DELETE', f'/v1/sessions/{session_id}', params={'beta': 'true'})

    async def send_message(self, session_id: str, text: str) -> dict:
        return await self._json(
            'POST',
            f'/v1/sessions/{session_id}/events',
            params={'beta': 'true'},
            json={
                'events': [
                    {
                        'type': 'user.message',
                        'input': {'parts': [{'type': 'text', 'text': text}]},
                    }
                ]
            },
        )

    async def list_events(
        self,
        session_id: str,
        *,
        page: str | None,
        limit: int,
        order: str,
        created_at_gte: str | None,
    ) -> dict:
        params: dict[str, str | int] = {'beta': 'true', 'limit': limit, 'order': order}
        if page:
            params['page'] = page
        if created_at_gte:
            params['created_at[gte]'] = created_at_gte
        return await self._json('GET', f'/v1/sessions/{session_id}/events', params=params)

    async def interrupt(self, session_id: str) -> dict:
        return await self._json(
            'POST',
            f'/v1/sessions/{session_id}/events',
            params={'beta': 'true'},
            json={'events': [{'type': 'user.interrupt'}]},
        )

    async def list_files(self, session_id: str, page: str | None, limit: int) -> dict:
        params: dict[str, str | int] = {'beta': 'true', 'scope_id': session_id, 'limit': limit}
        if page:
            params['page'] = page
        return await self._json('GET', '/v1/files', params=params)

    async def get_file(self, file_id: str) -> dict:
        return await self._json('GET', f'/v1/files/{file_id}', params={'beta': 'true'})

    async def stream_file(self, file_id: str) -> tuple[httpx.Response, AsyncIterator[bytes]]:
        client = self._client()
        request = client.build_request('GET', f'/v1/files/{file_id}/content', params={'beta': 'true'})
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as error:
            await client.aclose()
            raise AgentAPIError(502, str(error)) from error
        if response.is_error:
            body = await response.aread()
            await response.aclose()
            await client.aclose()
            raise AgentAPIError(response.status_code, body.decode(errors='replace'))

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return response, body()
