from __future__ import annotations

import time
import uuid

from open_webui.internal.db import Base, JSONField, get_async_db_context
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Boolean, Column, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession


class AgentProfile(Base):
    __tablename__ = 'agent_profile'

    id = Column(String, primary_key=True, unique=True)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    agent_id = Column(Text, nullable=False)
    environment_id = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_by = Column(Text, nullable=False)
    meta = Column(JSONField, nullable=False, default=dict)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class AgentProfileModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    agent_id: str
    environment_id: str
    enabled: bool = True
    created_by: str
    meta: dict = Field(default_factory=dict)
    created_at: int
    updated_at: int


class AgentProfileForm(BaseModel):
    name: str
    description: str | None = None
    agent_id: str
    environment_id: str
    enabled: bool = True
    meta: dict = Field(default_factory=dict)


class AgentProfileTable:
    async def get_by_id(self, profile_id: str, db: AsyncSession | None = None) -> AgentProfileModel | None:
        async with get_async_db_context(db) as session:
            profile = await session.get(AgentProfile, profile_id)
            return AgentProfileModel.model_validate(profile) if profile else None

    async def upsert(
        self,
        profile_id: str | None,
        form: AgentProfileForm,
        user_id: str,
        db: AsyncSession | None = None,
    ) -> AgentProfileModel:
        async with get_async_db_context(db) as session:
            now = int(time.time())
            profile = await session.get(AgentProfile, profile_id) if profile_id else None
            if profile is None:
                profile = AgentProfile(
                    id=profile_id or str(uuid.uuid4()),
                    created_by=user_id,
                    created_at=now,
                    **form.model_dump(),
                )
                session.add(profile)
            else:
                for key, value in form.model_dump().items():
                    setattr(profile, key, value)
            profile.updated_at = now
            await session.commit()
            await session.refresh(profile)
            return AgentProfileModel.model_validate(profile)

    async def list(self, db: AsyncSession | None = None) -> list[AgentProfileModel]:
        async with get_async_db_context(db) as session:
            result = await session.execute(select(AgentProfile).order_by(AgentProfile.created_at.asc()))
            return [AgentProfileModel.model_validate(item) for item in result.scalars().all()]


AgentProfiles = AgentProfileTable()
