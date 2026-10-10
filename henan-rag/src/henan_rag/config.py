from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_mode: str = "responses"
    chat_api_key: str = ""
    chat_base_url: str = "https://api.deepseek.com"
    chat_api_mode: str = "responses"
    chat_model: str = "deepseek-v4-flash"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "henan_rag_chunks"
    max_upload_mb: int = 20
    upload_dir: str = "/app/data/uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()
