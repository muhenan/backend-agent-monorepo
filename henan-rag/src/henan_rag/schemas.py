from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    point_id: str
    document_id: str
    source: str
    chunk_index: int
    text: str
    score: float


class DocumentSummary(BaseModel):
    document_id: str
    source: str
    chunk_count: int


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    tenant_id: str = Field(default="default", min_length=1, max_length=200)
    top_k: int = Field(default=5, ge=1, le=12)


class ChatResponse(BaseModel):
    answer: str
    citations: list[RetrievedChunk]
