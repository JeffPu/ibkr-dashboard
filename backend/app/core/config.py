import os
from dataclasses import dataclass
import json


@dataclass(slots=True)
class Settings:
    es_backend: str = "http"
    es_base_url: str = "http://localhost:9200"
    es_timeout_seconds: float = 5.0
    es_username: str = ""
    es_password: str = ""
    es_api_key: str = ""
    es_extra_headers: dict[str, str] | None = None


def load_settings() -> Settings:
    timeout_raw = os.getenv("ES_TIMEOUT_SECONDS", "5.0")
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 5.0
    headers_raw = os.getenv("ES_EXTRA_HEADERS_JSON", "{}")
    try:
        parsed_headers = json.loads(headers_raw)
    except json.JSONDecodeError:
        parsed_headers = {}
    if not isinstance(parsed_headers, dict):
        parsed_headers = {}
    normalized_headers = {
        str(key): str(value) for key, value in parsed_headers.items() if value is not None
    }
    es_host = os.getenv("ES_HOST", "")
    if es_host:
        es_backend = "http"
        es_base_url = es_host
    else:
        es_backend = os.getenv("ES_BACKEND", "http").strip().lower()
        es_base_url = os.getenv("ES_BASE_URL", "http://localhost:9200")
    return Settings(
        es_backend=es_backend,
        es_base_url=es_base_url,
        es_timeout_seconds=timeout,
        es_username=os.getenv("ES_USERNAME", ""),
        es_password=os.getenv("ES_PASSWORD", ""),
        es_api_key=os.getenv("ES_API_KEY", ""),
        es_extra_headers=normalized_headers,
    )


def validate_settings(settings: Settings) -> None:
    if settings.es_backend not in ("in_memory", "http"):
        raise ValueError("ES_BACKEND must be one of: in_memory, http")
    if settings.es_timeout_seconds <= 0:
        raise ValueError("ES_TIMEOUT_SECONDS must be greater than 0")
    if settings.es_backend != "http":
        return
    if not settings.es_base_url.strip():
        raise ValueError("ES_BASE_URL is required when ES_BACKEND=http")
    has_user = bool(settings.es_username)
    has_password = bool(settings.es_password)
    if has_user != has_password:
        raise ValueError("ES_USERNAME and ES_PASSWORD must be set together")
