from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterRequest(_Model):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, examples=["Africa/Cairo"])

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value


class LoginRequest(_Model):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()


class RefreshRequest(_Model):
    refresh_token: str = Field(min_length=20, max_length=200)


class UserOut(_Model):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    user_id: uuid.UUID = Field(validation_alias="id")
    email: str
    display_name: str | None
    timezone: str | None
    created_at: datetime


class TokenResponse(_Model):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    user: UserOut


class LogoutResponse(_Model):
    status: Literal["logged_out"] = "logged_out"
