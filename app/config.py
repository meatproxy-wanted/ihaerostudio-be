import json
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    db_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "data/studio.sqlite3"))
    api_keys: dict[str, str] = field(default_factory=lambda: json.loads(os.getenv("API_KEYS", '{"dev-only-change-me":"maker-local"}')))
    provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "demo"))
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", ""))
    font_path: str | None = field(default_factory=lambda: os.getenv("PDF_FONT_PATH"))
    cors_origins: list[str] = field(default_factory=lambda: os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","))
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))

    def __post_init__(self):
        if self.provider not in {"demo", "ollama"}:
            raise ValueError("AI_PROVIDER must be demo or ollama")
        if self.provider == "ollama" and not self.ollama_model:
            raise ValueError("OLLAMA_MODEL is required for ollama")
        if not isinstance(self.api_keys, dict) or not self.api_keys or any(not k or not isinstance(v, str) or not v for k, v in self.api_keys.items()):
            raise ValueError("API_KEYS must map nonempty bearer tokens to maker IDs")
        if self.environment == "production" and any(len(k) < 32 or k == "dev-only-change-me" for k in self.api_keys):
            raise ValueError("Production requires API keys of at least 32 characters")
