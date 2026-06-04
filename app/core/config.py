from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "FBRBot"
    SECRET_KEY: str
    ADMIN_SECRET_KEY: str = "asktax_super_secret_admin_key_2026"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 43200

    DATABASE_URL: str
    REDIS_URL: str

    QDRANT_HOST: str
    QDRANT_PORT: int
    QDRANT_COLLECTION: str

    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET: str

    OPENAI_API_KEY: str

    # Local Ollama for embeddings (nomic-embed-text — free, 768-dim)
    OLLAMA_BASE_URL: str = "http://172.17.0.2:11434"
    EMBED_MODEL: str = "nomic-embed-text"
    EMBED_DIM: int = 768

    FBR_BASE_URL: str
    FBR_DOWNLOAD_BASE: str

    # Comma-separated IPs or prefixes exempt from guest query limits
    # e.g. "205.164.151.,192.168.1.5" — trailing dot means entire /24 subnet
    WHITELISTED_IPS: str = ""

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()