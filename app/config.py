import json
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    db_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "data/studio.sqlite3"))
    api_keys: dict[str, str] = field(default_factory=lambda: json.loads(os.getenv("API_KEYS", '{"dev-only-change-me":"maker-local"}')))
    provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "demo"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""), repr=False)
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", ""))
    openai_max_output_tokens: int = field(default_factory=lambda: int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "16384")))
    comfy_api_key: str = field(default_factory=lambda: os.getenv("COMFY_CLOUD_API_KEY", ""), repr=False)
    comfy_asset_allowed_hosts: list[str] = field(default_factory=lambda: os.getenv("COMFY_ASSET_ALLOWED_HOSTS", "cloud.comfy.org").split(","))
    font_path: str | None = field(default_factory=lambda: os.getenv("PDF_FONT_PATH"))
    cors_origins: list[str] = field(default_factory=lambda: os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","))
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))

    def __post_init__(self):
        if self.provider not in {"demo", "openai"}:
            raise ValueError("AI_PROVIDER must be demo or openai")
        if self.provider == "openai":
            if not self.openai_api_key.strip():
                raise ValueError("OPENAI_API_KEY is required for openai")
            if not self.openai_model.strip():
                raise ValueError("OPENAI_MODEL is required for openai")
        if not 1 <= self.openai_max_output_tokens <= 100000:
            raise ValueError("OPENAI_MAX_OUTPUT_TOKENS must be between 1 and 100000")
        if not isinstance(self.api_keys, dict) or not self.api_keys or any(not k or not isinstance(v, str) or not v for k, v in self.api_keys.items()):
            raise ValueError("API_KEYS must map nonempty bearer tokens to maker IDs")
        if self.environment == "production" and any(len(k) < 32 or k == "dev-only-change-me" for k in self.api_keys):
            raise ValueError("Production requires API keys of at least 32 characters")
