from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # LLM
    gemini_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
    llm_thinking_budget: int | None = 0
    confidence_threshold: float = 0.7  # below this, the email goes straight to a human

    # Business
    business_name: str = "Brightline Supplies"
    business_tone: str = "Warm, concise and practical. Plain English, no jargon. Sign off as 'The Brightline Supplies team'."

    # Inbox source: mock | gmail
    inbox_mode: str = "mock"
    poll_interval_seconds: float = 5.0
    data_dir: Path = ROOT / "data"

    # Gmail mode
    gmail_credentials_file: Path = ROOT / "secrets" / "gmail_credentials.json"
    gmail_token_file: Path = ROOT / "secrets" / "gmail_token.json"
    gmail_query: str = "is:unread in:inbox -category:promotions"

    # WhatsApp (optional, Twilio sandbox)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""  # e.g. whatsapp:+14155238886
    public_base_url: str = ""       # used to validate Twilio signatures behind a tunnel

    # Tracing (optional) - Langfuse reads LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST itself
    langfuse_public_key: str = ""

    # Auth
    google_client_id: str = ""
    app_jwt_secret: str = "dev-only-change-me"
    app_jwt_ttl_hours: int = 24 * 7
    allowed_emails: str = ""
    allowed_domains: str = ""

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "inboxpilot.sqlite"

    @property
    def erp_path(self) -> Path:
        return self.data_dir / "erp.sqlite"

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def quotes_dir(self) -> Path:
        return self.data_dir / "quotes"


@lru_cache
def get_settings() -> Settings:
    return Settings()
