from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 从环境变量和本地 .env 读取配置，忽略不属于本项目的额外配置项。
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # OpenAI 兼容服务的连接配置；向量化使用这里的密钥和地址。
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_mode: str = "responses"
    # Agent 回答使用的聊天服务配置；未配置聊天密钥时会使用 openai_api_key。
    chat_api_key: str = ""
    chat_base_url: str = "https://api.deepseek.com"
    chat_api_mode: str = "responses"
    chat_model: str = "deepseek-v4-flash"
    # 向量模型和输出维度；维度需要与 Qdrant 集合的向量大小一致。
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    # Qdrant 服务地址，以及本项目存储文档片段的集合名称。
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "henan_rag_chunks"
    # 上传限制按 1024 * 1024 字节计算；upload_dir 保存上传文档的原文件。
    max_upload_mb: int = 20
    upload_dir: str = "/app/data/uploads"


# 用途：读取并缓存项目配置，让同一进程中的调用复用同一个 Settings 实例。
# 参数：无；配置值由环境变量、.env 文件和 Settings 中的默认值提供。
# 返回：Settings 配置对象；首次调用时读取配置，之后从缓存返回。
# 副作用：运行期间修改环境变量或 .env 不会自动刷新缓存，需要清空缓存或重启进程。
@lru_cache
def get_settings() -> Settings:
    return Settings()
