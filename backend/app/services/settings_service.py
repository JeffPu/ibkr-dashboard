import base64
from dataclasses import dataclass, field, fields
from datetime import datetime
import hashlib
import hmac
import os
from pathlib import Path
import secrets
from typing import Protocol


@dataclass(slots=True)
class AppSettings:
    base_currency: str = "USD"
    timezone: str = "America/New_York"
    finnhub_api_key: str = ""
    flex_token: str = ""
    flex_query_id: str = ""
    pull_frequency_minutes: int = 60
    display_realtime_prices: bool = False
    ai_provider: str = "openai"
    ai_model: str = ""
    openai_api_key: str = ""
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    tavily_api_key: str = ""
    futu_connection_mode: str = "disabled"
    futu_opend_host: str = "127.0.0.1"
    futu_opend_port: int = 11111
    telegram_bot_token: str = ""
    telegram_allowlisted_chat_ids: list[str] = field(default_factory=list)
    telegram_reports_enabled: bool = False
    telegram_daily_report_time: str = "08:30"
    last_successful_sync_at: str | None = None
    last_successful_sync_date: str | None = None


class SettingsRepositoryLike(Protocol):
    def get_settings(self) -> dict | None: ...
    def upsert_settings(self, doc: dict) -> None: ...


class SettingsSecretError(RuntimeError):
    """Raised when encrypted settings secrets cannot be loaded safely."""


class SettingsService:
    def __init__(self, repository: SettingsRepositoryLike | None = None) -> None:
        self._repository = repository
        self._settings = AppSettings()
        if self._repository is not None:
            saved = self._repository.get_settings()
            if saved is not None:
                self._settings = _coerce_settings(saved)

    def get(self) -> AppSettings:
        return self._settings

    def update(
        self,
        *,
        base_currency: str | None = None,
        timezone: str | None = None,
        finnhub_api_key: str | None = None,
        flex_token: str | None = None,
        flex_query_id: str | None = None,
        pull_frequency_minutes: int | None = None,
        display_realtime_prices: bool | None = None,
        ai_provider: str | None = None,
        ai_model: str | None = None,
        openai_api_key: str | None = None,
        minimax_api_key: str | None = None,
        minimax_base_url: str | None = None,
        deepseek_api_key: str | None = None,
        deepseek_base_url: str | None = None,
        tavily_api_key: str | None = None,
        futu_connection_mode: str | None = None,
        futu_opend_host: str | None = None,
        futu_opend_port: int | None = None,
        telegram_bot_token: str | None = None,
        telegram_allowlisted_chat_ids: list[str] | None = None,
        telegram_reports_enabled: bool | None = None,
        telegram_daily_report_time: str | None = None,
        last_successful_sync_at: str | None = None,
        last_successful_sync_date: str | None = None,
    ) -> AppSettings:
        if base_currency is not None:
            self._settings.base_currency = base_currency
        if timezone is not None:
            self._settings.timezone = timezone
        if finnhub_api_key is not None:
            self._settings.finnhub_api_key = finnhub_api_key
        if flex_token is not None:
            self._settings.flex_token = flex_token
        if flex_query_id is not None:
            self._settings.flex_query_id = flex_query_id
        if pull_frequency_minutes is not None:
            self._settings.pull_frequency_minutes = pull_frequency_minutes
        if display_realtime_prices is not None:
            self._settings.display_realtime_prices = display_realtime_prices
        if ai_provider is not None:
            self._settings.ai_provider = ai_provider
        if ai_model is not None:
            self._settings.ai_model = ai_model
        if openai_api_key is not None:
            self._settings.openai_api_key = openai_api_key
        if minimax_api_key is not None:
            self._settings.minimax_api_key = minimax_api_key
        if minimax_base_url is not None:
            self._settings.minimax_base_url = minimax_base_url
        if deepseek_api_key is not None:
            self._settings.deepseek_api_key = deepseek_api_key
        if deepseek_base_url is not None:
            self._settings.deepseek_base_url = deepseek_base_url
        if tavily_api_key is not None:
            self._settings.tavily_api_key = tavily_api_key
        if futu_connection_mode is not None:
            self._settings.futu_connection_mode = futu_connection_mode
        if futu_opend_host is not None:
            self._settings.futu_opend_host = futu_opend_host
        if futu_opend_port is not None:
            self._settings.futu_opend_port = futu_opend_port
        if telegram_bot_token is not None:
            self._settings.telegram_bot_token = telegram_bot_token
        if telegram_allowlisted_chat_ids is not None:
            self._settings.telegram_allowlisted_chat_ids = list(telegram_allowlisted_chat_ids)
        if telegram_reports_enabled is not None:
            self._settings.telegram_reports_enabled = telegram_reports_enabled
        if telegram_daily_report_time is not None:
            self._settings.telegram_daily_report_time = telegram_daily_report_time
        if last_successful_sync_at is not None:
            self._settings.last_successful_sync_at = last_successful_sync_at
        if last_successful_sync_date is not None:
            self._settings.last_successful_sync_date = last_successful_sync_date
        self._persist()
        return self._settings

    def mark_sync_success(self, synced_at: str) -> AppSettings:
        synced_date = datetime.fromisoformat(synced_at).date().isoformat()
        self._settings.last_successful_sync_at = synced_at
        self._settings.last_successful_sync_date = synced_date
        self._persist()
        return self._settings

    def _persist(self) -> None:
        if self._repository is None:
            return
        doc = {
            "base_currency": self._settings.base_currency,
            "timezone": self._settings.timezone,
            "finnhub_api_key": self._settings.finnhub_api_key,
            "flex_token": self._settings.flex_token,
            "flex_query_id": self._settings.flex_query_id,
            "pull_frequency_minutes": self._settings.pull_frequency_minutes,
            "display_realtime_prices": self._settings.display_realtime_prices,
            "ai_provider": self._settings.ai_provider,
            "ai_model": self._settings.ai_model,
            "openai_api_key": self._settings.openai_api_key,
            "minimax_api_key": self._settings.minimax_api_key,
            "minimax_base_url": self._settings.minimax_base_url,
            "deepseek_api_key": self._settings.deepseek_api_key,
            "deepseek_base_url": self._settings.deepseek_base_url,
            "tavily_api_key": self._settings.tavily_api_key,
            "futu_connection_mode": self._settings.futu_connection_mode,
            "futu_opend_host": self._settings.futu_opend_host,
            "futu_opend_port": self._settings.futu_opend_port,
            "telegram_bot_token": self._settings.telegram_bot_token,
            "telegram_allowlisted_chat_ids": list(self._settings.telegram_allowlisted_chat_ids),
            "telegram_reports_enabled": self._settings.telegram_reports_enabled,
            "telegram_daily_report_time": self._settings.telegram_daily_report_time,
            "last_successful_sync_at": self._settings.last_successful_sync_at,
            "last_successful_sync_date": self._settings.last_successful_sync_date,
        }
        self._repository.upsert_settings(_encrypt_secret_fields(doc))


def _coerce_settings(saved: dict) -> AppSettings:
    known_fields = {field.name for field in fields(AppSettings)}
    doc = {key: value for key, value in saved.items() if key in known_fields}
    doc = _decrypt_secret_fields(doc)
    chat_ids = doc.get("telegram_allowlisted_chat_ids")
    if chat_ids is None:
        doc["telegram_allowlisted_chat_ids"] = []
    elif isinstance(chat_ids, list):
        doc["telegram_allowlisted_chat_ids"] = [str(value) for value in chat_ids]
    else:
        doc["telegram_allowlisted_chat_ids"] = [str(chat_ids)]
    return AppSettings(**doc)


SECRET_FIELDS = {
    "finnhub_api_key",
    "flex_token",
    "openai_api_key",
    "minimax_api_key",
    "deepseek_api_key",
    "tavily_api_key",
    "telegram_bot_token",
}
ENCRYPTED_SECRET_PREFIX = "enc:v1:"
KEY_ENV_VAR = "IBKR_DASHBOARD_SETTINGS_KEY"
KEY_FILE_ENV_VAR = "IBKR_DASHBOARD_SETTINGS_KEY_FILE"


def _encrypt_secret_fields(doc: dict) -> dict:
    encrypted = dict(doc)
    for key in SECRET_FIELDS:
        value = encrypted.get(key)
        if isinstance(value, str) and value:
            encrypted[key] = _encrypt_secret(value)
    return encrypted


def _decrypt_secret_fields(doc: dict) -> dict:
    decrypted = dict(doc)
    for key in SECRET_FIELDS:
        value = decrypted.get(key)
        if isinstance(value, str) and value.startswith(ENCRYPTED_SECRET_PREFIX):
            try:
                decrypted[key] = _decrypt_secret(value)
            except SettingsSecretError as exc:
                raise SettingsSecretError(f"failed to decrypt settings field {key}") from exc
    return decrypted


def _encrypt_secret(value: str) -> str:
    if value.startswith(ENCRYPTED_SECRET_PREFIX):
        return value
    key = _load_settings_key(create=True)
    nonce = secrets.token_bytes(16)
    plaintext = value.encode("utf-8")
    ciphertext = _xor_bytes(plaintext, _secret_keystream(key, nonce, len(plaintext)))
    tag = hmac.new(key, b"settings:v1:auth" + nonce + ciphertext, hashlib.sha256).digest()
    payload = base64.urlsafe_b64encode(nonce + tag + ciphertext).decode("ascii")
    return f"{ENCRYPTED_SECRET_PREFIX}{payload}"


def _decrypt_secret(value: str) -> str:
    try:
        payload = base64.urlsafe_b64decode(value.removeprefix(ENCRYPTED_SECRET_PREFIX).encode("ascii"))
    except Exception as exc:
        raise SettingsSecretError("encrypted settings value is not valid base64") from exc
    if len(payload) < 48:
        raise SettingsSecretError("encrypted settings value is too short")
    nonce = payload[:16]
    tag = payload[16:48]
    ciphertext = payload[48:]
    key = _load_settings_key(create=False)
    if key is None:
        raise SettingsSecretError(
            f"settings encryption key is missing; set {KEY_ENV_VAR} or restore the key file"
        )
    expected = hmac.new(key, b"settings:v1:auth" + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise SettingsSecretError("encrypted settings authentication failed")
    plaintext = _xor_bytes(ciphertext, _secret_keystream(key, nonce, len(ciphertext)))
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SettingsSecretError("encrypted settings plaintext is not valid utf-8") from exc


def _load_settings_key(*, create: bool) -> bytes | None:
    env_key = os.getenv(KEY_ENV_VAR)
    if env_key:
        return _normalize_settings_key(env_key)
    path = _settings_key_path()
    if path.exists():
        return _normalize_settings_key(path.read_text(encoding="utf-8").strip())
    if not create:
        return None
    key = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return _normalize_settings_key(key)


def _settings_key_path() -> Path:
    configured = os.getenv(KEY_FILE_ENV_VAR)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "ibkr-dashboard" / "settings.key"


def _normalize_settings_key(value: str) -> bytes:
    text = value.strip()
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(_with_base64_padding(text).encode("ascii"))
        except Exception:
            continue
        if decoded:
            return hashlib.sha256(decoded).digest()
    return hashlib.sha256(text.encode("utf-8")).digest()


def _with_base64_padding(value: str) -> str:
    return value + ("=" * (-len(value) % 4))


def _secret_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks: list[bytes] = []
    counter = 0
    generated = 0
    while generated < length:
        counter_bytes = counter.to_bytes(8, "big")
        block = hmac.new(key, b"settings:v1:stream" + nonce + counter_bytes, hashlib.sha256).digest()
        blocks.append(block)
        generated += len(block)
        counter += 1
    return b"".join(blocks)[:length]


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))
