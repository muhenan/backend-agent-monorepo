from uuid import uuid4

import pytest
from pydantic import ValidationError

from henan_rag.errors import DomainError
from henan_rag.schemas import (
    CreateKnowledgeBaseRequest,
    PatchKnowledgeBaseRequest,
    QueryRequest,
)


# 验证问题清理和标准 UUID；无参数，返回 None。
def test_query_normalizes_question_and_uuid() -> None:
    selected = str(uuid4())
    query = QueryRequest(question="  学习 RAG？\n", knowledge_base_ids=[selected.upper()])
    assert query.question == "学习 RAG？"
    assert str(query.knowledge_base_ids[0]) == selected
    assert query.model_dump(mode="json")["knowledge_base_ids"] == [selected]


# 验证知识库选择边界；payload 是非法请求字段集合，返回 None。
@pytest.mark.parametrize("payload", [
    {}, {"knowledge_base_ids": []}, {"knowledge_base_ids": ["invalid"]},
    {"knowledge_base_ids": [str(uuid4())], "tenant_id": "legacy"},
    {"knowledge_base_ids": [str(uuid4())], "top_k": 0},
    {"knowledge_base_ids": [str(uuid4())], "top_k": 13},
])
def test_query_rejects_invalid_scope(payload: dict) -> None:
    with pytest.raises(ValidationError):
        QueryRequest(question="question", **payload)


# 验证挂载知识库没有业务数量上限；count 为所选库数，返回 None，仍保留输入顺序。
@pytest.mark.parametrize("count", [9, 1205])
def test_query_accepts_large_explicit_scope(count: int) -> None:
    selected = [str(uuid4()) for _ in range(count)]
    query = QueryRequest(question="全部所选资料", knowledge_base_ids=selected)
    assert query.model_dump(mode="json")["knowledge_base_ids"] == selected
    assert query.top_k == 5


# 验证等价 UUID 重复选择；无参数，返回 None。
def test_query_rejects_duplicate_uuid_and_blank_question() -> None:
    selected = str(uuid4())
    with pytest.raises(ValidationError):
        QueryRequest(question="question", knowledge_base_ids=[selected, selected.upper()])
    with pytest.raises(ValidationError):
        QueryRequest(question=" \n ", knowledge_base_ids=[selected])
    with pytest.raises(ValidationError):
        QueryRequest(question="x" * 4001, knowledge_base_ids=[selected])


# 验证创建名称/描述清理与禁用额外字段；无参数，返回 None。
def test_create_knowledge_base_validation() -> None:
    request = CreateKnowledgeBaseRequest(name="  技术资料 ", description="  文档 ")
    assert request.name == "技术资料" and request.description == "文档"
    for name in ("", "  ", "x" * 81, "foo\nbar", "foo\x00bar", "foo\u200bbar"):
        with pytest.raises(ValidationError):
            CreateKnowledgeBaseRequest(name=name)
    with pytest.raises(ValidationError):
        CreateKnowledgeBaseRequest(name="资料", description="x" * 501)
    with pytest.raises(ValidationError):
        CreateKnowledgeBaseRequest(name="资料", tenant_id="legacy")


# 验证 PATCH 缺省值和清空语义；无参数，返回 None。
def test_patch_requires_real_change_and_allows_clearing_description() -> None:
    request = PatchKnowledgeBaseRequest(description=" ")
    assert request.description == "" and request.name is None
    assert request.model_fields_set == {"description"}
    for payload in ({}, {"name": None}, {"description": None}, {"name": " "}, {"status": "active"}):
        with pytest.raises(ValidationError):
            PatchKnowledgeBaseRequest(**payload)


# 验证公开错误结构；无参数，返回 None。
def test_domain_error_detail() -> None:
    assert DomainError(404, "NOT_FOUND", "不存在").to_detail() == {
        "code": "NOT_FOUND", "message": "不存在", "retryable": False,
    }
    assert DomainError(422, "INGEST_FAILED", "失败", document_id="doc").to_detail()["document_id"] == "doc"
