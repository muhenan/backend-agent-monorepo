import asyncio
from math import sqrt
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient, models

from henan_rag import app as app_module
from henan_rag.errors import DomainError
from henan_rag.metadata_repository import MetadataRepository
from henan_rag.rag_service import RagService


# 用途：准备真实 SQLite 和本地 Qdrant 测试服务，禁止解析和模型调用。
# 参数：tmp_path 为隔离目录。返回：初始化服务；调用者负责关闭 Qdrant。
async def make_inspection_service(tmp_path: Path) -> RagService:
    service = object.__new__(RagService)
    service.settings = SimpleNamespace(
        qdrant_collection="inspection-chunks", embedding_dimensions=10
    )
    service.metadata = MetadataRepository(tmp_path / "metadata.sqlite3")
    await service.metadata.initialize()
    service.qdrant = AsyncQdrantClient(location=":memory:")
    service.embed = AsyncMock(side_effect=AssertionError("查看分块不能调用模型"))
    service.convert_markdown = AsyncMock(side_effect=AssertionError("查看分块不能重新解析"))
    await service.ensure_collection()
    return service


# 用途：建立有索引空洞且 point ID 顺序不同于分块索引的文档，并加入不可见资料。
# 参数：service 为测试服务。返回：(知识库摘要、文档 ID、另一知识库摘要、其他文档 ID)。
async def seed_inspection_document(service: RagService):
    library = await service.metadata.create_knowledge_base("可视化资料")
    outside = await service.metadata.create_knowledge_base("隔离资料")
    document_id, same_library_id, outside_id = (str(uuid4()) for _ in range(3))
    for library_id, id, source in (
        (library.id, document_id, "inspection.md"),
        (library.id, same_library_id, "another.md"),
        (outside.id, outside_id, "outside.md"),
    ):
        await service.metadata.create_document(id, library_id, source, None)
        await service.metadata.update_document(id, "ready", chunk_count=9)
    points = [
        models.PointStruct(
            id=point_id, vector=list(range(1, 11)),
            payload={
                "knowledge_base_id": library.id, "document_id": document_id,
                "source": "inspection.md", "chunk_index": index, "indexing_state": "ready",
                "text": f"分块 {index}\n<script>只作为文本</script>",
                "custom": {"note": "实际 payload 原样可查"},
            },
        )
        for index, point_id in zip([0, 2, 3, 7, 8, 10, 11], [40, 5, 29, 2, 90, 16, 7], strict=True)
    ]
    for point_id, library_id, id, index, state in (
        (100, library.id, document_id, 1, "staging"),
        (101, library.id, same_library_id, 4, "ready"),
        (102, outside.id, outside_id, 6, "ready"),
    ):
        points.append(models.PointStruct(
            id=point_id, vector=list(range(1, 11)),
            payload={
                "knowledge_base_id": library_id, "document_id": id, "source": "hidden.md",
                "chunk_index": index, "indexing_state": state, "text": "不应出现在当前文档",
            },
        ))
    await service.qdrant.upsert(
        collection_name=service.settings.qdrant_collection, points=points, wait=True
    )
    return library, document_id, outside, same_library_id


# 用途：验证按分块索引稳定分页、真实总数、原始 payload 和真实向量摘要，且不取整库向量。
# 参数：tmp_path 为隔离数据目录。返回：无。
def test_real_stored_chunks_ordered_pagination_and_vector_summary(tmp_path):
    # 用途：构造不同 point 顺序及索引空洞，遍历三页并验证只读 API 的数据来源。
    # 参数：无（读取 tmp_path）。返回：无。
    async def run():
        service = await make_inspection_service(tmp_path)
        try:
            library, document_id, _, _ = await seed_inspection_document(service)
            service.settings.embedding_dimensions = 99
            service.qdrant.scroll = AsyncMock(wraps=service.qdrant.scroll)
            service.qdrant.retrieve = AsyncMock(wraps=service.qdrant.retrieve)
            first = await service.list_document_chunks(library.id, document_id, 3, 0)
            second = await service.list_document_chunks(library.id, document_id, 3, first.next_offset)
            final = await service.list_document_chunks(library.id, document_id, 3, second.next_offset)
            assert first.total == second.total == final.total == 7
            assert first.document.chunk_count == 9
            assert first.knowledge_base.id == library.id
            assert first.knowledge_base.name == library.name
            assert [item.chunk_index for item in first.items] == [0, 2, 3]
            assert [item.chunk_index for item in second.items] == [7, 8, 10]
            assert [item.chunk_index for item in final.items] == [11]
            assert (first.next_offset, second.next_offset, final.next_offset) == (4, 11, None)
            assert [item.point_id for item in first.items] == ["40", "5", "29"]
            assert "storage_path" not in first.document.model_dump()
            for item in first.items + second.items + final.items:
                assert item.text == item.payload["text"]
                assert item.character_count == len(item.text)
                assert item.indexing_state == item.payload["indexing_state"] == "ready"
                assert item.payload["document_id"] == document_id
                assert item.payload["knowledge_base_id"] == library.id
                assert item.payload["custom"]["note"] == "实际 payload 原样可查"
                assert item.vector_dimensions == 10
                assert item.vector_preview == pytest.approx([number / sqrt(385) for number in range(1, 9)])
            for call in service.qdrant.scroll.call_args_list:
                assert call.kwargs["limit"] == 4
                assert call.kwargs["with_vectors"] is False
                assert "offset" not in call.kwargs
                assert call.kwargs["order_by"].key == "chunk_index"
            assert [len(call.kwargs["ids"]) for call in service.qdrant.retrieve.call_args_list] == [3, 3, 1]
            assert all(call.kwargs["with_vectors"] for call in service.qdrant.retrieve.call_args_list)
            empty = await service.list_document_chunks(library.id, document_id, 3, 100)
            assert empty.total == 7 and empty.items == [] and empty.next_offset is None
            assert service.qdrant.retrieve.call_count == 3
            service.embed.assert_not_called()
            service.convert_markdown.assert_not_called()
        finally:
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证 HTTP 参数和归属校验、无模型密钥仍可浏览、文档/库状态错误为结构化响应。
# 参数：tmp_path 为临时目录；monkeypatch 替换应用共享服务和模型密钥。返回：无。
def test_document_inspection_api_validation_and_isolation(tmp_path, monkeypatch):
    # 用途：在同一循环中构造数据库，使用 TestClient 检查只读接口的公开契约。
    # 参数：无（读取 tmp_path/monkeypatch）。返回：无。
    async def run():
        service = await make_inspection_service(tmp_path)
        try:
            library, document_id, outside, _ = await seed_inspection_document(service)
            monkeypatch.setattr(app_module, "get_rag", lambda: service)
            monkeypatch.setattr(app_module.settings, "openai_api_key", "")
            client = TestClient(app_module.app)
            path = f"/api/knowledge-bases/{library.id}/documents/{document_id}/chunks"
            response = client.get(path + "?limit=2")
            assert response.status_code == 200
            page = response.json()
            assert page["total"] == 7 and page["next_offset"] == 3
            assert page["items"][0]["text"].startswith("分块 0")
            for query in ("limit=0", "limit=101", "offset=-1", "limit=abc", "offset=1.2"):
                invalid = client.get(path + "?" + query)
                assert invalid.status_code == 422
                assert invalid.json()["detail"]["code"] == "VALIDATION_ERROR"
            for bad_path in (
                f"/api/knowledge-bases/not-a-uuid/documents/{document_id}/chunks",
                f"/api/knowledge-bases/{library.id}/documents/not-a-uuid/chunks",
            ):
                assert client.get(bad_path).status_code == 422
            for invalid_library, invalid_document, code in (
                (outside.id, document_id, "DOCUMENT_NOT_FOUND"),
                (library.id, str(uuid4()), "DOCUMENT_NOT_FOUND"),
                (str(uuid4()), document_id, "KNOWLEDGE_BASE_NOT_FOUND"),
            ):
                result = client.get(
                    f"/api/knowledge-bases/{invalid_library}/documents/{invalid_document}/chunks"
                )
                assert result.status_code == 404
                assert result.json()["detail"]["code"] == code
            for status in ("importing", "failed", "cleanup_required", "deleting", "delete_failed"):
                await service.metadata.update_document(document_id, status, chunk_count=7)
                result = client.get(path)
                assert result.status_code == 409
                assert result.json()["detail"]["code"] == "DOCUMENT_NOT_READY"
            await service.metadata.update_document(document_id, "ready", chunk_count=7)
            await service.metadata.set_knowledge_base_status(library.id, "deleting")
            result = client.get(path)
            assert result.status_code == 409
            assert result.json()["detail"]["code"] == "KNOWLEDGE_BASE_NOT_ACTIVE"
            service.embed.assert_not_called()
            service.convert_markdown.assert_not_called()
        finally:
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证任一向量读取阶段失败均隐藏底层敏感异常，并给出可重试 503。
# 参数：tmp_path 为隔离目录；operation 为失败阶段。返回：无。
@pytest.mark.parametrize("operation", ["count", "scroll", "retrieve"])
def test_vector_failure_is_safe_and_retryable(tmp_path, operation):
    # 用途：注入底层故障，检查对网页暴露的领域错误不含原始异常。
    # 参数：无（读取 tmp_path/operation）。返回：无。
    async def run():
        service = await make_inspection_service(tmp_path)
        try:
            library, document_id, _, _ = await seed_inspection_document(service)
            setattr(service.qdrant, operation, AsyncMock(side_effect=RuntimeError("secret endpoint")))
            with pytest.raises(DomainError) as error:
                await service.list_document_chunks(library.id, document_id)
            assert error.value.status_code == 503
            assert error.value.code == "INDEX_OPERATION_FAILED"
            assert error.value.retryable
            assert "secret" not in str(error.value.to_detail())
        finally:
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证读取向量期间文档或知识库已删除/退出 ready 时，不返回陈旧内容。
# 参数：tmp_path 为隔离目录；change 为并发变化类型。返回：无。
@pytest.mark.parametrize("change", ["document_state", "document_deleted", "library_state", "document_updated"])
def test_document_inspection_rechecks_metadata_after_read(tmp_path, change):
    # 用途：在本页向量读取后改变元数据，断言分页响应整体失败。
    # 参数：无（读取 tmp_path/change）。返回：无。
    async def run():
        service = await make_inspection_service(tmp_path)
        try:
            library, document_id, _, _ = await seed_inspection_document(service)
            retrieve = service.qdrant.retrieve

            # 用途：模拟与阅读并发的删除或文档状态变化。
            # 参数：kwargs 为真实 retrieve 参数。返回：真实向量读取结果。
            async def retrieve_then_change(**kwargs):
                result = await retrieve(**kwargs)
                if change == "document_state":
                    await service.metadata.update_document(document_id, "deleting", chunk_count=9)
                elif change == "document_deleted":
                    await service.metadata.delete_document(library.id, document_id)
                elif change == "library_state":
                    await service.metadata.set_knowledge_base_status(library.id, "deleting")
                else:
                    await service.metadata.update_document(document_id, "ready", chunk_count=10)
                return result

            service.qdrant.retrieve = retrieve_then_change
            with pytest.raises(DomainError) as error:
                await service.list_document_chunks(library.id, document_id)
            expected = {
                "document_state": (409, "DOCUMENT_NOT_READY"),
                "document_deleted": (404, "DOCUMENT_NOT_FOUND"),
                "library_state": (409, "KNOWLEDGE_BASE_NOT_ACTIVE"),
                "document_updated": (409, "DOCUMENT_CHANGED"),
            }[change]
            assert (error.value.status_code, error.value.code) == expected
        finally:
            await service.qdrant.close()

    asyncio.run(run())
