from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from uuid import uuid4

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.constants import ERROR_MESSAGES
from open_webui.events import EVENTS, publish_event
from open_webui.internal.db import get_async_session
from open_webui.models.access_grants import normalize_access_grants
from open_webui.models.chats import ChatForm, ChatResponse, Chats
from open_webui.models.config import Config
from open_webui.utils.access_control import has_access, has_permission
from open_webui.utils.agentapi import (
    AgentAPIError,
    AgentTurnChannel,
    create_session,
    delete_session,
    event_text,
    get_session,
    send_events,
    start_agent_turn,
    stream_turn_events,
    verify_connection,
)
from open_webui.utils.auth import get_admin_user, get_verified_user

log = logging.getLogger(__name__)
router = APIRouter()


class AgentProfile(BaseModel):
    id: str
    name: str
    description: str = ''
    agent_id: str
    agent_version: int = Field(ge=1)
    environment_id: str
    enabled: bool = True
    access_grants: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator('id', 'name', 'agent_id', 'environment_id')
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('must not be empty')
        return value


class AgentAPIAdminConfig(BaseModel):
    enabled: bool = False
    base_url: str = 'https://agent.qiniuapi.com'
    api_key: str = ''
    profiles: list[AgentProfile] = Field(default_factory=list)

    @field_validator('base_url')
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        value = value.strip().rstrip('/')
        if not value.startswith(('https://', 'http://')):
            raise ValueError('base_url must start with http:// or https://')
        return value


class AgentAPIAdminConfigResponse(BaseModel):
    enabled: bool
    base_url: str
    api_key_configured: bool
    profiles: list[AgentProfile]


class VerifyAgentAPIForm(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


class CreateAgentChatForm(BaseModel):
    profile_id: str
    title: str | None = None


class AgentMessageForm(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    message_id: str | None = None
    response_message_id: str | None = None

    @field_validator('content')
    @classmethod
    def nonempty_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('message must not be empty')
        return value


async def _config() -> tuple[bool, str, str, list[dict[str, Any]]]:
    values = await Config.get_many(
        'agentapi.enabled',
        'agentapi.base_url',
        'agentapi.api_key',
        'agentapi.profiles',
    )
    return (
        bool(values.get('agentapi.enabled', False)),
        str(values.get('agentapi.base_url') or 'https://agent.qiniuapi.com').rstrip('/'),
        str(values.get('agentapi.api_key') or ''),
        values.get('agentapi.profiles') if isinstance(values.get('agentapi.profiles'), list) else [],
    )


async def _require_feature(user, db: AsyncSession | None = None) -> None:
    enabled, _, _, _ = await _config()
    if not enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Agent mode is disabled')
    if user.role != 'admin' and not await has_permission(
        user.id,
        'features.agent_mode',
        await Config.get('user.permissions'),
        db=db,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


async def _profile_for_user(profile_id: str, user, db: AsyncSession | None = None) -> dict[str, Any]:
    await _require_feature(user, db)
    _, _, _, profiles = await _config()
    profile = next((item for item in profiles if isinstance(item, dict) and item.get('id') == profile_id), None)
    if not profile or not profile.get('enabled', True):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Agent profile not found')
    if user.role != 'admin' and not await has_access(user.id, 'read', profile.get('access_grants') or [], db=db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    return profile


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': profile.get('id'),
        'name': profile.get('name'),
        'description': profile.get('description') or '',
    }


def _agent_meta(chat) -> dict[str, Any]:
    meta = chat.meta if isinstance(chat.meta, dict) else {}
    agent_meta = meta.get('agentapi')
    if not isinstance(agent_meta, dict) or not agent_meta.get('session_id'):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Agent session metadata is missing')
    return agent_meta


def _ndjson(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {'type': event.get('type') or 'event'}
    for key in ('id', 'name', 'status', 'stop_reason', 'is_error'):
        if key in event:
            summary[key] = event[key]
    return summary


@router.get('/config', response_model=AgentAPIAdminConfigResponse)
async def get_agentapi_config(user=Depends(get_admin_user)):
    enabled, base_url, api_key, profiles = await _config()
    return {
        'enabled': enabled,
        'base_url': base_url,
        'api_key_configured': bool(api_key),
        'profiles': profiles,
    }


@router.post('/config', response_model=AgentAPIAdminConfigResponse)
async def update_agentapi_config(form_data: AgentAPIAdminConfig, user=Depends(get_admin_user)):
    _, _, current_api_key, _ = await _config()
    profile_ids: set[str] = set()
    profiles: list[dict[str, Any]] = []
    for profile in form_data.profiles:
        if profile.id in profile_ids:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Agent profile ids must be unique')
        profile_ids.add(profile.id)
        item = profile.model_dump()
        item['access_grants'] = normalize_access_grants(item.get('access_grants'))
        profiles.append(item)

    api_key = form_data.api_key.strip() or current_api_key
    await Config.upsert(
        {
            'agentapi.enabled': form_data.enabled,
            'agentapi.base_url': form_data.base_url,
            'agentapi.api_key': api_key,
            'agentapi.profiles': profiles,
        }
    )
    return {
        'enabled': form_data.enabled,
        'base_url': form_data.base_url,
        'api_key_configured': bool(api_key),
        'profiles': profiles,
    }


@router.post('/config/verify')
async def verify_agentapi_config(form_data: VerifyAgentAPIForm, user=Depends(get_admin_user)):
    _, configured_base_url, configured_api_key, _ = await _config()
    base_url = (form_data.base_url or configured_base_url).strip().rstrip('/')
    api_key = (form_data.api_key or configured_api_key).strip()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='AgentAPI key is required')
    try:
        await verify_connection(base_url=base_url, api_key=api_key)
        return {'status': True}
    except AgentAPIError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error))
    except (aiohttp.ClientError, TimeoutError) as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'Unable to reach AgentAPI: {error}')


@router.get('/profiles')
async def get_agent_profiles(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    await _require_feature(user, db)
    _, _, _, profiles = await _config()
    allowed = []
    for profile in profiles:
        if not isinstance(profile, dict) or not profile.get('enabled', True):
            continue
        if user.role == 'admin' or await has_access(user.id, 'read', profile.get('access_grants') or [], db=db):
            allowed.append(_public_profile(profile))
    return allowed


@router.post('/chats', response_model=ChatResponse)
async def create_agent_chat(
    request: Request,
    form_data: CreateAgentChatForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    profile = await _profile_for_user(form_data.profile_id, user, db)
    enabled, base_url, api_key, _ = await _config()
    if not enabled or not api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='AgentAPI is not configured')

    chat_id = str(uuid4())
    requested_title = (form_data.title or '').strip()
    title = (requested_title or profile.get('name') or 'New Agent Chat')[:120]
    try:
        remote = await create_session(
            base_url=base_url,
            api_key=api_key,
            agent_id=str(profile['agent_id']),
            agent_version=int(profile['agent_version']),
            environment_id=str(profile['environment_id']),
            title=title,
            metadata={'open_webui_chat_id': chat_id, 'open_webui_user_id': user.id},
        )
    except AgentAPIError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error))
    except (aiohttp.ClientError, TimeoutError) as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'Unable to reach AgentAPI: {error}')

    chat_data = {
        'id': chat_id,
        'title': title,
        'models': [],
        'params': {},
        'history': {'messages': {}, 'currentId': None},
        'messages': [],
    }
    internal_meta = {
        'agentapi': {
            'profile_id': profile['id'],
            'profile_name': profile['name'],
            'session_id': remote['id'],
            'agent_id': profile['agent_id'],
            'agent_version': profile['agent_version'],
            'environment_id': profile['environment_id'],
            'status': remote.get('status') or 'idle',
        }
    }
    chat = await Chats.insert_new_chat(
        chat_id,
        user.id,
        ChatForm(chat=chat_data),
        db=db,
        mode='agent',
        internal_meta=internal_meta,
    )
    if not chat:
        try:
            await delete_session(base_url=base_url, api_key=api_key, session_id=remote['id'])
        except Exception:
            log.exception('Failed to remove AgentAPI session after local chat creation failed')
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=ERROR_MESSAGES.DEFAULT())

    await publish_event(
        request,
        EVENTS.CHAT_CREATED,
        actor=user,
        subject_id=chat.id,
        data={'title': chat.title, 'mode': 'agent', 'profile_id': profile['id']},
    )
    return ChatResponse.model_validate(chat, from_attributes=True)


@router.get('/chats/{chat_id}/status')
async def get_agent_chat_status(
    chat_id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await Chats.get_chat_by_id_for_user(chat_id, user, db=db)
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    if chat.mode != 'agent':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='This is not an Agent conversation')
    agent_meta = _agent_meta(chat)
    _, base_url, api_key, _ = await _config()
    try:
        remote = await get_session(base_url=base_url, api_key=api_key, session_id=agent_meta['session_id'])
        return {'status': remote.get('status'), 'session_id': agent_meta['session_id']}
    except AgentAPIError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error))


@router.post('/chats/{chat_id}/interrupt')
async def interrupt_agent_chat(
    chat_id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user.id, db=db)
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    if chat.mode != 'agent':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='This is not an Agent conversation')
    agent_meta = _agent_meta(chat)
    _, base_url, api_key, _ = await _config()
    try:
        await send_events(
            base_url=base_url,
            api_key=api_key,
            session_id=agent_meta['session_id'],
            events=[{'type': 'user.interrupt'}],
        )
        return {'status': True}
    except AgentAPIError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error))


@router.post('/chats/{chat_id}/messages')
async def send_agent_message(
    chat_id: str,
    form_data: AgentMessageForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await _require_feature(user, db)
    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user.id, db=db)
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    if chat.mode != 'agent':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='This is not an Agent conversation')
    agent_meta = _agent_meta(chat)
    await _profile_for_user(str(agent_meta.get('profile_id') or ''), user, db)
    _, base_url, api_key, _ = await _config()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='AgentAPI is not configured')

    user_message_id = form_data.message_id or str(uuid4())
    assistant_message_id = form_data.response_message_id or str(uuid4())
    parent_id = chat.current_message_id
    now = int(time.time())

    channel = AgentTurnChannel()

    async def run_turn() -> None:
        user_message = {
            'id': user_message_id,
            'role': 'user',
            'content': form_data.content,
            'parentId': parent_id,
            'childrenIds': [assistant_message_id],
            'timestamp': now,
            'done': True,
        }
        assistant_message = {
            'id': assistant_message_id,
            'role': 'assistant',
            'model': f'agentapi:{agent_meta.get("profile_id")}',
            'content': '',
            'parentId': user_message_id,
            'childrenIds': [],
            'timestamp': now,
            'done': False,
            'meta': {'agentapi': {'events': []}},
        }
        content = ''
        event_summaries: list[dict[str, Any]] = []
        final_status = 'idle'
        try:
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, user_message_id, user_message)
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, assistant_message_id, assistant_message)
            channel.publish(
                _ndjson(
                    {
                        'type': 'message.accepted',
                        'user_message_id': user_message_id,
                        'message_id': assistant_message_id,
                    }
                )
            )

            async for event in stream_turn_events(
                base_url=base_url,
                api_key=api_key,
                session_id=str(agent_meta['session_id']),
                text=form_data.content,
            ):
                text = event_text(event)
                if text:
                    content += text
                event_type = str(event.get('type') or 'event')
                if event_type.startswith(('agent.tool', 'agent.mcp_tool', 'session.', 'error')):
                    event_summaries.append(_event_summary(event))
                if event_type.startswith('session.status_'):
                    final_status = event_type.removeprefix('session.status_')
                channel.publish(_ndjson({'type': 'agent.event', 'event': event, 'content': content}))

            assistant_message.update(
                {
                    'content': content,
                    'done': True,
                    'meta': {'agentapi': {'events': event_summaries[-100:]}},
                }
            )
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, assistant_message_id, assistant_message)
            updated_meta = {
                **(chat.meta or {}),
                'agentapi': {**agent_meta, 'status': final_status},
            }
            await Chats.update_chat_meta_by_id(chat_id, updated_meta)
            channel.publish(_ndjson({'type': 'done', 'message_id': assistant_message_id, 'content': content}))
        except (AgentAPIError, aiohttp.ClientError, TimeoutError) as error:
            log.warning('AgentAPI turn failed for chat %s: %s', chat_id, error)
            assistant_message.update(
                {
                    'content': content,
                    'done': True,
                    'error': {'content': str(error)},
                    'meta': {'agentapi': {'events': event_summaries[-100:]}},
                }
            )
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, assistant_message_id, assistant_message)
            channel.publish(_ndjson({'type': 'error', 'message_id': assistant_message_id, 'detail': str(error)}))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Unexpected AgentAPI turn failure for chat %s', chat_id)
            detail = 'Agent turn failed unexpectedly'
            assistant_message.update(
                {
                    'content': content,
                    'done': True,
                    'error': {'content': detail},
                    'meta': {'agentapi': {'events': event_summaries[-100:]}},
                }
            )
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, assistant_message_id, assistant_message)
            channel.publish(_ndjson({'type': 'error', 'message_id': assistant_message_id, 'detail': detail}))
        finally:
            channel.close()

    start_agent_turn(run_turn())

    return StreamingResponse(
        channel.stream(),
        media_type='application/x-ndjson',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
