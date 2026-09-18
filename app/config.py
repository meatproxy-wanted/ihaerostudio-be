import json
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    db_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "data/studio.sqlite3"))
    api_keys: dict[str, str] = field(default_factory=lambda: json.loads(os.getenv("API_KEYS") or '{}'))
    # anonymous: any well-formed bearer token names its own workspace (a public demo without accounts).
    # keys: only tokens registered in API_KEYS are accepted.
    auth_mode: str = field(default_factory=lambda: os.getenv("AUTH_MODE", "anonymous"))
    ai_calls_per_hour: int = field(default_factory=lambda: int(os.getenv("AI_CALLS_PER_HOUR", "0")))
    ai_calls_global_per_hour: int = field(default_factory=lambda: int(os.getenv("AI_CALLS_GLOBAL_PER_HOUR", "0")))
    provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "demo"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""), repr=False)
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", ""))
    openai_max_output_tokens: int = field(default_factory=lambda: int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "16384")))
    comfy_api_key: str = field(default_factory=lambda: os.getenv("COMFY_CLOUD_API_KEY", ""), repr=False)
    studio_character_mode: str = field(default_factory=lambda: os.getenv("STUDIO_CHARACTER_MODE", "library"))
    # Opt-in only; already reserved groups survive a configuration rollback.
    studio_scene_mode: str = field(default_factory=lambda: os.getenv("STUDIO_SCENE_MODE", "single"))
    comfy_asset_allowed_hosts: list[str] = field(default_factory=lambda: os.getenv("COMFY_ASSET_ALLOWED_HOSTS", "cloud.comfy.org,storage.googleapis.com").split(","))
    font_path: str | None = field(default_factory=lambda: os.getenv("PDF_FONT_PATH"))
    # Remote store for hosts without a persistent disk (Vercel). Empty means the SQLite file at db_path.
    turso_url: str = field(default_factory=lambda: os.getenv("TURSO_DATABASE_URL", "").strip())
    turso_token: str = field(default_factory=lambda: os.getenv("TURSO_AUTH_TOKEN", "").strip(), repr=False)
    # Where pictures are served from; stored inside image URLs, so set it before creating data.
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL") or (
        "https://" + os.environ["VERCEL_PROJECT_PRODUCTION_URL"] if os.getenv("VERCEL_PROJECT_PRODUCTION_URL")
        else "http://127.0.0.1:8100"))
    cors_origins: list[str] = field(default_factory=lambda: os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","))
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))

    def __post_init__(self):
        if self.studio_scene_mode not in {"single", "storyboard4"}:
            raise ValueError("STUDIO_SCENE_MODE must be single or storyboard4")
        if self.studio_character_mode not in {"library", "generate"}:
            raise ValueError("STUDIO_CHARACTER_MODE must be library or generate")
        self.public_base_url = self.public_base_url.strip().rstrip("/")
        if self.provider not in {"demo", "openai"}:
            raise ValueError("AI_PROVIDER must be demo or openai")
        if self.provider == "openai":
            if not self.openai_api_key.strip():
                raise ValueError("OPENAI_API_KEY is required for openai")
            if not self.openai_model.strip():
                raise ValueError("OPENAI_MODEL is required for openai")
        if not 1 <= self.openai_max_output_tokens <= 100000:
            raise ValueError("OPENAI_MAX_OUTPUT_TOKENS must be between 1 and 100000")
        if not isinstance(self.api_keys, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in self.api_keys.items()):
            raise ValueError("API_KEYS must map nonempty bearer tokens to maker IDs")
        if self.auth_mode not in {"anonymous", "keys"}:
            raise ValueError("AUTH_MODE must be anonymous or keys")
        if self.auth_mode == "keys" and not self.api_keys:
            raise ValueError("API_KEYS is required in keys mode")
        if self.environment == "production" and any(len(k) < 32 for k in self.api_keys):
            raise ValueError("Production requires API keys of at least 32 characters")
        if "dev-only-change-me" in self.api_keys:
            raise ValueError("API_KEYS must not contain the public development token")
        if self.ai_calls_per_hour < 0 or self.ai_calls_global_per_hour < 0:
            raise ValueError("AI call limits must be nonnegative (0 disables the limit)")
