from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from open_webui.internal.db import get_async_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.agent_profiles import AgentProfileForm, AgentProfileModel, AgentProfiles
from open_webui.models.chats import Chat, ChatForm, ChatModel, Chats
from open_webui.models.config import Config
from open_webui.utils.agentapi import AgentAPIClient, AgentAPIError
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.oauth import decrypt_data, encrypt_data
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

DEFAULT_BASE_URL = 'https://agent.qiniuapi.com'


class AgentProfileInput(BaseModel):
    name: str
    description: str | None = None
    agent_id: str
    environment_id: str
    enabled: bool = True
    access_grants: list[dict] = Field(default_factory=list)


class AgentConfigForm(BaseModel):
    enabled: bool
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    profile: AgentProfileInput


class AgentConfigResponse(BaseModel):
    enabled: bool
    base_url: str
    api_key_configured: bool
    profile: dict | None


class CreateAgentChatForm(BaseModel):
    title: str = 'New Agent Chat'
    metadata: dict[str, str] = Field(default_factory=dict)


class AgentMessageForm(BaseModel):
    content: str


def _public_grants(grants) -> list[dict]:
    return [
        {
            'id': grant.id,
            'principal_type': grant.principal_type,
            'principal_id': grant.principal_id,
            'permission': grant.permission,
        }
        for grant in grants
    ]


def _with_local(payload: dict, **local) -> dict:
    return {**payload, '_open_webui': {**payload.get('_open_webui', {}), **local}}


def _raise_upstream(error: AgentAPIError) -> None:
    raise HTTPException(
        status_code=error.status_code if 400 <= error.status_code < 500 else status.HTTP_502_BAD_GATEWAY,
        detail={'code': 'AGENTAPI_ERROR', 'upstream': error.detail},
    )


async def _settings() -> tuple[bool, str, str, str | None]:
    values = await Config.get_many(
        'agentapi.enabled',
        'agentapi.base_url',
        'agentapi.api_key',
        'agentapi.default_profile_id',
    )
    encrypted_key = values.get('agentapi.api_key')
    api_key = ''
    if encrypted_key:
        api_key = decrypt_data(encrypted_key).get('api_key', '')
    return (
        bool(values.get('agentapi.enabled', False)),
        values.get('agentapi.base_url') or DEFAULT_BASE_URL,
        api_key,
        values.get('agentapi.default_profile_id'),
    )


async def _default_profile() -> AgentProfileModel | None:
    _, _, _, profile_id = await _settings()
    return await AgentProfiles.get_by_id(profile_id) if profile_id else None


async def _client() -> AgentAPIClient:
    _, base_url, api_key, _ = await _settings()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='AgentAPI is not configured')
    return AgentAPIClient(base_url, api_key)


async def _has_profile_read(user, profile_id: str, db: AsyncSession) -> bool:
    return user.role == 'admin' or await AccessGrants.has_access(
        user_id=user.id,
        resource_type='agent_profile',
        resource_id=profile_id,
        permission='read',
        db=db,
    )


async def _get_chat(chat_id: str, user, db: AsyncSession, *, owner_only: bool = False) -> ChatModel:
    chat = await db.get(Chat, chat_id)
    if not chat or chat.mode != 'agent':
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Agent conversation not found')
    if chat.user_id != user.id and (owner_only or user.role != 'admin'):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Access prohibited')
    return ChatModel.model_validate(chat)


def _session_id(chat: ChatModel) -> str:
    return chat.meta['agentapi']['session_id']


@router.get('/config', response_model=AgentConfigResponse)
async def get_config(user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    enabled, base_url, api_key, profile_id = await _settings()
    profile = await AgentProfiles.get_by_id(profile_id, db=db) if profile_id else None
    profile_data = None
    if profile:
        grants = await AccessGrants.get_grants_by_resource('agent_profile', profile.id, db=db)
        profile_data = {**profile.model_dump(), 'access_grants': _public_grants(grants)}
    return {
        'enabled': enabled,
        'base_url': base_url,
        'api_key_configured': bool(api_key),
        'profile': profile_data,
    }


@router.post('/config', response_model=AgentConfigResponse)
async def set_config(
    form: AgentConfigForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    _, _, current_key, profile_id = await _settings()
    api_key = form.api_key.strip() if form.api_key else current_key
    if form.enabled and not api_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='API key is required')

    profile = await AgentProfiles.upsert(
        profile_id,
        AgentProfileForm(**form.profile.model_dump(exclude={'access_grants'})),
        user.id,
        db=db,
    )
    read_grants = [grant for grant in form.profile.access_grants if grant.get('permission') == 'read']
    grants = await AccessGrants.set_access_grants('agent_profile', profile.id, read_grants, db=db)
    updates = {
        'agentapi.enabled': form.enabled,
        'agentapi.base_url': form.base_url.rstrip('/'),
        'agentapi.default_profile_id': profile.id,
    }
    if form.api_key:
        updates['agentapi.api_key'] = encrypt_data({'api_key': api_key})
    await Config.upsert(updates)
    return {
        'enabled': form.enabled,
        'base_url': form.base_url.rstrip('/'),
        'api_key_configured': bool(api_key),
        'profile': {**profile.model_dump(), 'access_grants': _public_grants(grants)},
    }


@router.post('/config/verify')
async def verify_config(
    form: AgentConfigForm,
    user=Depends(get_admin_user),
):
    _, _, current_key, _ = await _settings()
    api_key = form.api_key.strip() if form.api_key else current_key
    if not api_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='API key is required')
    client = AgentAPIClient(form.base_url, api_key)
    try:
        return await client.verify(form.profile.agent_id, form.profile.environment_id)
    except AgentAPIError as error:
        _raise_upstream(error)


@router.get('/profile')
async def get_profile(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    enabled, _, _, profile_id = await _settings()
    profile = await AgentProfiles.get_by_id(profile_id, db=db) if profile_id else None
    if not enabled or not profile or not profile.enabled or not await _has_profile_read(user, profile.id, db):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No Agent is available')
    return {
        'id': profile.id,
        'name': profile.name,
        'description': profile.description,
        'meta': profile.meta,
    }


@router.get('/chats')
async def list_chats(
    user_id: str | None = None,
    include_archived: bool = False,
    skip: int = 0,
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if user_id and user.role != 'admin' and user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Access prohibited')
    owner_id = user_id if user.role == 'admin' and user_id else (None if user.role == 'admin' else user.id)
    stmt = select(Chat).where(Chat.mode == 'agent')
    if owner_id:
        stmt = stmt.where(Chat.user_id == owner_id)
    if not include_archived:
        stmt = stmt.where(Chat.archived.is_(False))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    result = await db.execute(stmt.order_by(Chat.updated_at.desc()).offset(skip).limit(limit))
    return {
        'items': [ChatModel.model_validate(chat).model_dump() for chat in result.scalars().all()],
        'total': total or 0,
    }


@router.post('/chats')
async def create_chat(
    form: CreateAgentChatForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    enabled, _, _, _ = await _settings()
    if not enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Agent mode is disabled')
    profile = await _default_profile()
    if not profile or not profile.enabled or not await _has_profile_read(user, profile.id, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Agent access is not granted')
    client = await _client()
    chat_id = str(uuid.uuid4())
    metadata = {**form.metadata, 'open_webui_chat_id': chat_id, 'open_webui_user_id': user.id}
    try:
        upstream = await client.create_session(
            profile.agent_id,
            profile.environment_id,
            form.title,
            metadata,
        )
    except AgentAPIError as error:
        _raise_upstream(error)

    session_id = upstream.get('id')
    if not session_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='AgentAPI returned no Session ID')
    agent = upstream.get('agent') or {}
    chat = await Chats.insert_new_chat(
        chat_id,
        user.id,
        ChatForm(chat={'title': form.title, 'history': {'messages': {}, 'currentId': None}}),
        db=db,
        mode='agent',
        internal_meta={
            'agentapi': {
                'profile_id': profile.id,
                'profile_name': profile.name,
                'session_id': session_id,
                'agent_id': agent.get('id', profile.agent_id),
                'agent_version': agent.get('version'),
                'environment_id': upstream.get('environment_id', profile.environment_id),
            }
        },
    )
    if not chat:
        try:
            await client.delete_session(session_id)
        except AgentAPIError:
            pass
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Failed to save Agent chat')
    return _with_local(upstream, chat=chat.model_dump(), read_only=False)


@router.get('/chats/{chat_id}')
async def get_chat(
    chat_id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await _get_chat(chat_id, user, db)
    profile_id = chat.meta.get('agentapi', {}).get('profile_id')
    can_send = chat.user_id == user.id and bool(profile_id) and await _has_profile_read(user, profile_id, db)
    return _with_local(chat.model_dump(), read_only=not can_send, is_owner=chat.user_id == user.id)


@router.get('/chats/{chat_id}/status')
async def get_status(
    chat_id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await _get_chat(chat_id, user, db)
    try:
        payload = await (await _client()).get_session(_session_id(chat))
        return _with_local(payload, chat_id=chat.id, owner_id=chat.user_id, read_only=chat.user_id != user.id)
    except AgentAPIError as error:
        _raise_upstream(error)


@router.get('/chats/{chat_id}/events')
async def get_events(
    chat_id: str,
    page: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    order: str = Query('asc', pattern='^(asc|desc)$'),
    created_at_gte: str | None = None,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await _get_chat(chat_id, user, db)
    try:
        payload = await (await _client()).list_events(
            _session_id(chat),
            page=page,
            limit=limit,
            order=order,
            created_at_gte=created_at_gte,
        )
        return _with_local(payload, chat_id=chat.id)
    except AgentAPIError as error:
        _raise_upstream(error)


@router.post('/chats/{chat_id}/messages')
async def send_message(
    chat_id: str,
    form: AgentMessageForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await _get_chat(chat_id, user, db, owner_only=True)
    profile_id = chat.meta.get('agentapi', {}).get('profile_id')
    if not profile_id or not await _has_profile_read(user, profile_id, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Agent access is not granted')
    if not form.content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Message cannot be empty')
    try:
        payload = await (await _client()).send_message(_session_id(chat), form.content.strip())
    except AgentAPIError as error:
        _raise_upstream(error)
    chat_row = await db.get(Chat, chat_id)
    chat_row.updated_at = int(time.time())
    await db.commit()
    return _with_local(payload, chat_id=chat.id)


@router.post('/chats/{chat_id}/interrupt')
async def interrupt_chat(
    chat_id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await _get_chat(chat_id, user, db, owner_only=True)
    try:
        return _with_local(await (await _client()).interrupt(_session_id(chat)), chat_id=chat.id)
    except AgentAPIError as error:
        _raise_upstream(error)


@router.get('/chats/{chat_id}/files')
async def list_files(
    chat_id: str,
    page: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await _get_chat(chat_id, user, db)
    try:
        payload = await (await _client()).list_files(_session_id(chat), page, limit)
        return _with_local(payload, chat_id=chat.id)
    except AgentAPIError as error:
        _raise_upstream(error)


@router.get('/chats/{chat_id}/files/{file_id}/content')
async def download_file(
    chat_id: str,
    file_id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await _get_chat(chat_id, user, db)
    client = await _client()
    try:
        file = await client.get_file(file_id)
        scope = file.get('scope') or {}
        if scope.get('type') != 'session' or scope.get('id') != _session_id(chat):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='File not found')
        response, body = await client.stream_file(file_id)
    except AgentAPIError as error:
        _raise_upstream(error)
    headers = {}
    for name in ('content-disposition', 'content-length', 'accept-ranges'):
        if value := response.headers.get(name):
            headers[name] = value
    return StreamingResponse(body, media_type=response.headers.get('content-type'), headers=headers)


@router.post('/chats/{chat_id}/archive')
async def toggle_archive(
    chat_id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await _get_chat(chat_id, user, db, owner_only=True)
    updated = await Chats.toggle_chat_archive_by_id(chat.id, db=db, mode='agent')
    return updated


@router.delete('/chats/{chat_id}')
async def delete_chat(
    chat_id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    chat = await _get_chat(chat_id, user, db, owner_only=True)
    try:
        await (await _client()).delete_session(_session_id(chat))
    except AgentAPIError as error:
        if error.status_code != status.HTTP_404_NOT_FOUND:
            _raise_upstream(error)
    return await Chats.delete_chat_by_id_and_user_id(chat.id, user.id, db=db, mode='agent')
