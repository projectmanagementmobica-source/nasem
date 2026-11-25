"""Application settings (loaded from environment / .env)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---
    database_url: str = "postgresql+asyncpg://nasem:nasem@localhost:5432/nasem"
    db_echo: bool = False

    # IMPORTANT: must match the vector dimension in the Alembic migration.
    # Changing it requires a new migration + re-embedding all memories.
    embedding_dim: int = 1536

    # --- LLM (Anthropic) ---
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-5"
    llm_max_tokens: int = 2048
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2  # SDK-level retries for transient API errors (429 / 5xx / network)
    llm_max_parse_attempts: int = 2  # re-asks when the output is not valid JSON / schema

    # --- Privacy ---
    # Dev only: 32 random bytes, base64. Production: a KMS/HSM located in Egypt, never an env var.
    master_key: SecretStr | None = None
    mask_before_llm: bool = True

    # --- Auth ---
    jwt_secret_key: SecretStr | None = None  # >= 32 chars. Generate: python -c "import secrets;print(secrets.token_urlsafe(48))"
    jwt_issuer: str = "nasem-ai"
    jwt_audience: str = "nasem-app"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    # --- Guides (web-grounded answers) ---
    web_search_enabled: bool = True
    web_search_tool_type: str = "web_search_20250305"  # newer: web_search_20260318
    web_search_max_uses: int = 5
    guide_max_tokens: int = 4096
    guide_max_rounds: int = 4
    guide_timeout_seconds: float = 120.0

    # --- AI gateway (the installed app talks ONLY to this; provider keys never leave the server) ---
    gateway_provider_order: list[str] = ["gemini", "groq", "anthropic", "openai"]
    anthropic_model_main: str = "claude-sonnet-5"
    anthropic_model_quick: str = "claude-haiku-4-5-20251001"
    openai_api_key: SecretStr | None = None
    openai_model_main: str = ""
    openai_model_quick: str = ""
    gemini_api_key: SecretStr | None = None
    gemini_model_main: str = "gemini-3.5-flash"
    # Each free Gemini model has its own small daily quota: tried in order when one is used up.
    gemini_fallback_models: list[str] = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
    groq_api_key: SecretStr | None = None   # free tier, no card: console.groq.com/keys
    groq_model_main: str = "openai/gpt-oss-120b"
    groq_model_quick: str = "llama-3.3-70b-versatile"
    gemini_model_quick: str = ""
    gateway_daily_requests_per_user: int = 300
    gateway_timeout_seconds: float = 90.0
    image_provider_order: list[str] = ["google", "openai"]
    gemini_image_model: str = ""   # e.g. Google's current image model id
    openai_image_model: str = ""   # e.g. OpenAI's current image model id
    gateway_daily_images_per_user: int = 20
    azure_speech_key: SecretStr | None = None
    azure_speech_region: str = ""
    cors_origins: list[str] = []

    # --- Access control: the owner approves who may use Nasem ---
    admin_email: str = ""                 # created/updated as admin on startup
    admin_password: SecretStr | None = None
    activation_open: bool = True          # accept new activation requests
    max_devices_per_user: int = 2
    activation_poll_hours: int = 72       # a pending request can be claimed for this long

    # --- Push reminders (server only knows WHEN; the text is encrypted on the phone) ---
    vapid_public_key: str = ""          # base64url, given to the app
    vapid_private_key: SecretStr | None = None
    vapid_subject: str = "mailto:admin@example.com"
    push_poll_seconds: float = 30.0
    # Serverless hosts (Vercel sets VERCEL=1) have no always-on process: no background loop;
    # an outside scheduler calls /v1/internal/tick every minute with CRON_SECRET instead.
    serverless: bool = False
    cron_secret: SecretStr | None = None
    migrate_on_start: bool = False   # run "alembic upgrade head" on cold start (on by default when serverless)
    qstash_token: SecretStr | None = None          # Upstash QStash: exact-time wake-ups (free: 1,000 messages/day)
    qstash_url: str = "https://qstash.upstash.io"
    public_url: str = ""                           # this server's public https URL (Vercel fills it in by itself)
    push_max_scheduled_per_user: int = 300
    # --- Retention (data minimisation): the server forgets on its own ---
    retention_sent_push_days: int = 30
    retention_usage_days: int = 90

    # --- Orchestrator ---
    memory_min_confidence: float = 0.6  # extracted facts below this are discarded
    orchestrator_context_limit: int = 50  # known memories sent to the LLM as context
    max_input_chars: int = 4000
    default_timezone: str = "Africa/Cairo"


def _asyncpg_url(url: str) -> str:
    """Accept the URLs hosting providers hand out (postgres://...?sslmode=require&channel_binding=...)
    and turn them into what SQLAlchemy+asyncpg expects."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query))
    if "sslmode" in q:
        mode = q.pop("sslmode")
        if mode in ("require", "verify-full", "verify-ca"):
            q["ssl"] = "require"
    q.pop("channel_binding", None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


settings = Settings()
import os as _os  # noqa: E402
if _os.environ.get("VERCEL") and not _os.environ.get("SERVERLESS"):
    settings.serverless = True
if settings.serverless and not _os.environ.get("MIGRATE_ON_START"):
    settings.migrate_on_start = True
settings.database_url = _asyncpg_url(str(settings.database_url))
