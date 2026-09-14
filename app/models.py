from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core import parse_spotify_url, validate_filename_template


class ResolveRequest(BaseModel):
    url: str = Field(min_length=1, max_length=500)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return parse_spotify_url(value).url


class SettingsPatch(BaseModel):
    filename_template: str | None = None
    bitrate: Literal["auto", "128k", "192k", "256k", "320k"] | None = None
    concurrency: int | None = Field(default=None, ge=1, le=5)
    prefer_official: bool | None = None
    duration_tolerance_seconds: int | None = Field(default=None, ge=2, le=60)
    low_confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    save_cover: bool | None = None
    network_mode: Literal["direct", "http", "socks"] | None = None
    proxy_url: str | None = Field(default=None, max_length=500)
    proxy_username: str | None = Field(default=None, max_length=200)
    proxy_password: str | None = Field(default=None, max_length=500)

    @field_validator("filename_template")
    @classmethod
    def valid_template(cls, value: str | None) -> str | None:
        return validate_filename_template(value) if value is not None else value

    @field_validator("proxy_url")
    @classmethod
    def valid_proxy(cls, value: str | None) -> str | None:
        if not value:
            return value
        if not value.startswith(("http://", "https://", "socks5://", "socks5h://")):
            raise ValueError("Proxy URL must use http, https, socks5, or socks5h.")
        return value

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)

