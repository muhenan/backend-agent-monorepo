import unicodedata
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KnowledgeBaseStatus = Literal["active", "deleting", "delete_failed"]
DocumentStatus = Literal[
    "importing", "ready", "failed", "cleanup_required", "deleting", "delete_failed"
]


# 清理知识库名称；value 是用户输入，返回去掉首尾空白且不含控制字符的 1 至 80 字符名称。
def clean_knowledge_base_name(value: str) -> str:
    name = value.strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("知识库名称去除首尾空白后必须包含 1 至 80 个字符。")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in name):
        raise ValueError("知识库名称不能包含控制字符。")
    return name


# 清理描述；value 是用户输入，返回去掉首尾空白且不超过 500 字符的描述。
def clean_knowledge_base_description(value: str) -> str:
    description = value.strip()
    if len(description) > 500:
        raise ValueError("知识库描述不能超过 500 个字符。")
    return description


class CreateKnowledgeBaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""

    # 验证创建名称；cls 是模型类、value 为输入值，返回清理名称或交由类型校验处理。
    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> object:
        return clean_knowledge_base_name(value) if isinstance(value, str) else value

    # 验证描述；cls 是模型类、value 为输入值，返回清理描述或交由类型校验处理。
    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, value: object) -> object:
        return clean_knowledge_base_description(value) if isinstance(value, str) else value


class PatchKnowledgeBaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    description: str | None = None

    # 验证修改名称；cls 是模型类、value 为输入值，返回清理名称；未提供字段保持 None。
    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> object:
        return clean_knowledge_base_name(value) if isinstance(value, str) else value

    # 验证描述；cls 是模型类、value 为输入值，空字符串表示清空，未提供字段保持 None。
    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, value: object) -> object:
        return clean_knowledge_base_description(value) if isinstance(value, str) else value

    # 验证修改范围；self 为请求，至少提供一项且不能显式传 null，返回当前请求。
    @model_validator(mode="after")
    def validate_changes(self) -> "PatchKnowledgeBaseRequest":
        if not self.model_fields_set:
            raise ValueError("至少提供 name 或 description 中的一项。")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null；清空描述请使用空字符串。")
        return self


class KnowledgeBaseReference(BaseModel):
    id: str
    name: str


class KnowledgeBaseSummary(KnowledgeBaseReference):
    description: str
    is_default: bool
    status: KnowledgeBaseStatus
    last_error: str | None = None
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    created_at: str
    updated_at: str


class KnowledgeBasePage(BaseModel):
    items: list[KnowledgeBaseSummary]
    total: int
    limit: int
    offset: int
    default_knowledge_base_id: str


class RetrievedChunk(BaseModel):
    point_id: str
    document_id: str
    knowledge_base_id: str
    knowledge_base_name: str
    source: str
    chunk_index: int
    text: str
    score: float


class DocumentSummary(BaseModel):
    document_id: str
    knowledge_base_id: str
    source: str
    status: DocumentStatus
    chunk_count: int = Field(ge=0)
    file_available: bool
    last_error: str | None = None
    created_at: str
    updated_at: str


class DocumentPage(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class StoredDocumentChunk(BaseModel):
    point_id: str
    chunk_index: int = Field(ge=0)
    text: str
    character_count: int = Field(ge=0)
    indexing_state: Literal["ready"]
    vector_dimensions: int = Field(ge=1)
    vector_preview: list[float] = Field(max_length=8)
    payload: dict[str, Any]


class DocumentChunkPage(BaseModel):
    document: DocumentSummary
    knowledge_base: KnowledgeBaseReference
    items: list[StoredDocumentChunk]
    total: int = Field(ge=0)
    limit: int
    offset: int
    next_offset: int | None


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[UUID] = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=12)

    # 清理问题；cls 是模型类、value 为 question 输入，返回去掉首尾空白的文本再校验长度。
    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    # 拒绝重复库；cls 是模型类、value 是 UUID 列表，返回原顺序列表，重复时抛校验错误。
    @field_validator("knowledge_base_ids")
    @classmethod
    def unique_knowledge_bases(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("不能重复选择同一个知识库。")
        return value


class ChatResponse(BaseModel):
    answer: str
    citations: list[RetrievedChunk]
    knowledge_bases: list[KnowledgeBaseReference]
