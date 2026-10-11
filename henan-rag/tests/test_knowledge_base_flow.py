import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient, models

from henan_rag import app as app_module
from henan_rag.errors import DomainError
from henan_rag.metadata_repository import DEFAULT_KNOWLEDGE_BASE_ID, MetadataRepository
from henan_rag.rag_service import RagService


# 用途：构造使用真实临时 SQLite 和 Qdrant 本地引擎的服务，模型和解析输出可控。
# 参数：tmp_path 为 pytest 临时目录。返回：初始化后的服务；不会连接外部模型或真实资料。
async def make_service(tmp_path: Path) -> RagService:
    service = object.__new__(RagService)
    service.settings = SimpleNamespace(
        qdrant_collection="test-knowledge-chunks", embedding_dimensions=2,
        upload_dir=str(tmp_path / "uploads"), chat_model="test-model",
    )
    service.metadata = MetadataRepository(tmp_path / "metadata.sqlite3")
    await service.metadata.initialize()
    service.qdrant = AsyncQdrantClient(location=":memory:")
    service._busy = set()
    service._converter_semaphore = asyncio.Semaphore(1)

    # 用途：返回相同的两维向量，让测试只检查范围与状态过滤。
    # 参数：texts 为待嵌入文本列表。返回：每个输入一条固定向量。
    async def embed(texts):
        return [[1.0, 0.0] for _ in texts]

    # 用途：把测试 Markdown 文件直接读取为解析结果，避开下载 Docling 模型。
    # 参数：path 为服务保存的文件。返回：UTF-8 文本。
    async def convert(path):
        return path.read_text(encoding="utf-8")

    service.embed = embed
    service.convert_markdown = convert
    await service.ensure_collection()
    return service


# 用途：验证单库隔离、多库并集、重命名引用与删除归属检查。
# 参数：tmp_path 为临时数据目录。返回：无，所有测试数据在临时存储中。
def test_isolation_union_rename_and_delete(tmp_path):
    # 用途：在同一事件循环中完成入库、过滤、改名和删除断言。
    # 参数：无（读取 tmp_path）。返回：无。
    async def run():
        service = await make_service(tmp_path)
        try:
            first = await service.metadata.create_knowledge_base("产品文档")
            second = await service.metadata.create_knowledge_base("技术资料")
            one = await service.ingest_file("product.md", "产品限制为 42 个。".encode(), first.id)
            two = await service.ingest_file("deploy.md", "部署内存为 8 GB。".encode(), second.id)
            assert one.status == two.status == "ready"
            assert {chunk.knowledge_base_id for chunk in await service.retrieve("内容", [first.id], 10)} == {first.id}
            combined = await service.retrieve("内容", [first.id, second.id], 10)
            assert {chunk.knowledge_base_id for chunk in combined} == {first.id, second.id}
            renamed = await service.metadata.update_knowledge_base(first.id, name="新版产品文档")
            assert renamed.id == first.id
            assert (await service.retrieve("内容", [first.id], 1))[0].knowledge_base_name == renamed.name
            with pytest.raises(DomainError) as cross_delete:
                await service.delete_document(first.id, two.document_id)
            assert cross_delete.value.status_code == 404
            with pytest.raises(DomainError) as default_delete:
                await service.delete_knowledge_base(DEFAULT_KNOWLEDGE_BASE_ID)
            assert default_delete.value.code == "DEFAULT_KNOWLEDGE_BASE_PROTECTED"
            await service.delete_knowledge_base(second.id)
            with pytest.raises(DomainError):
                await service.resolve_scope([first.id, second.id])
            assert len(await service.retrieve("内容", [first.id], 10)) == 1
            assert len(list(Path(service.settings.upload_dir).iterdir())) == 1
            await service.delete_document(first.id, one.document_id)
            assert await service.retrieve("内容", [first.id], 10) == []
            assert (await service.metadata.get_knowledge_base(first.id)).document_count == 0
        finally:
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证超过原 8 库上限仍可在真实本地 Qdrant 检索，任一未知或停用库整体拒绝。
# 参数：tmp_path 为临时数据目录。返回：无；不调用外部模型，不读取已有资料。
def test_large_scope_retrieves_union_and_rejects_invalid_members(tmp_path):
    # 用途：构造 12 个选中库和一个未选中的库，验证范围顺序、全局 top-k 和失败时不调用模型。
    # 参数：无（读取 tmp_path）。返回：无。
    async def run():
        service = await make_service(tmp_path)
        try:
            libraries = [
                await service.metadata.create_knowledge_base(f"范围库 {index}")
                for index in range(12)
            ]
            for index, library in enumerate(libraries):
                await service.ingest_file(f"scope-{index}.md", b"selected content", library.id)
            await service.ingest_file("outside.md", b"outside content", DEFAULT_KNOWLEDGE_BASE_ID)
            selected = [item.id for item in reversed(libraries)]
            scope = await service.resolve_scope(selected)
            assert [item.id for item in scope] == selected
            assert [item.name for item in scope] == [item.name for item in reversed(libraries)]
            chunks = await service.retrieve("content", selected, 12)
            assert len(chunks) == 12
            assert {item.knowledge_base_id for item in chunks} == set(selected)
            assert DEFAULT_KNOWLEDGE_BASE_ID not in {item.knowledge_base_id for item in chunks}
            assert len(await service.retrieve("content", selected, 3)) == 3

            service.embed = AsyncMock(return_value=[[1.0, 0.0]])
            with pytest.raises(DomainError) as missing:
                await service.retrieve("content", [*selected[:6], str(uuid4()), *selected[6:]], 3)
            assert missing.value.code == "KNOWLEDGE_BASE_NOT_FOUND"
            await service.metadata.set_knowledge_base_status(libraries[6].id, "delete_failed")
            with pytest.raises(DomainError) as inactive:
                await service.retrieve("content", selected, 3)
            assert inactive.value.code == "KNOWLEDGE_BASE_NOT_ACTIVE"
            service.embed.assert_not_awaited()
        finally:
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证查询同时过滤 staging、无元数据残留及重复结果，并保持全局 top-k 上限。
# 参数：tmp_path 为临时目录。返回：无。
def test_retrieve_filters_staging_and_orphan_points(tmp_path):
    # 用途：向真实本地 Qdrant 加入可检索/不可检索记录，检查最终引用。
    # 参数：无（读取 tmp_path）。返回：无。
    async def run():
        service = await make_service(tmp_path)
        try:
            knowledge_base_id = DEFAULT_KNOWLEDGE_BASE_ID
            document = await service.ingest_file("valid.md", b"valid content", knowledge_base_id)
            base_payload = {
                "knowledge_base_id": knowledge_base_id, "document_id": document.document_id,
                "source": "extra.md", "text": "should not be retrieved", "chunk_index": 0,
            }
            await service.qdrant.upsert(
                collection_name=service.settings.qdrant_collection, wait=True,
                points=[
                    models.PointStruct(id=str(uuid4()), vector=[1.0, 0.0],
                                       payload={**base_payload, "indexing_state": "staging"}),
                    models.PointStruct(id=str(uuid4()), vector=[1.0, 0.0],
                                       payload={**base_payload, "indexing_state": "ready", "document_id": str(uuid4())}),
                ],
            )
            chunks = await service.retrieve("content", [knowledge_base_id], 2)
            assert len(chunks) == 1
            assert chunks[0].source == "valid.md"
            await service.metadata.update_document(document.document_id, "delete_failed", file_available=True)
            assert await service.retrieve("content", [knowledge_base_id], 2) == []
        finally:
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证批次中途失败清除已写向量与文件，并保留 failed 文档记录。
# 参数：tmp_path 为临时目录；monkeypatch 替换切块输出为跨两个批次的文本。返回：无。
def test_partial_import_failure_is_cleaned(tmp_path, monkeypatch):
    # 切块回调用途：构造两个批次；text 为解析文本，返回 65 个固定片段。
    monkeypatch.setattr("henan_rag.rag_service.split_markdown", lambda text: ["chunk"] * 65)

    # 用途：让第二次 embedding 失败，确认第一批 64 个向量不会残留可检索内容。
    # 参数：无（读取 tmp_path）。返回：无。
    async def run():
        service = await make_service(tmp_path)
        service.embed = AsyncMock(side_effect=[[[1.0, 0.0]] * 64, DomainError(502, "MODEL_FAILED", "模拟向量化失败", True)])
        try:
            with pytest.raises(DomainError) as failure:
                await service.ingest_file("failure.md", b"content", DEFAULT_KNOWLEDGE_BASE_ID)
            assert failure.value.document_id is not None
            record = await service.metadata.get_document(DEFAULT_KNOWLEDGE_BASE_ID, failure.value.document_id)
            assert record["status"] == "failed"
            assert record["chunk_count"] == 0 and not record["file_available"]
            assert (await service.qdrant.count(service.settings.qdrant_collection, exact=True)).count == 0
            assert list(Path(service.settings.upload_dir).iterdir()) == []
            assert not service._busy
        finally:
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证清理失败可观察、不可检索，并能在下一次启动恢复中再次清理。
# 参数：tmp_path 为临时目录；monkeypatch 产生跨批次文本。返回：无。
def test_cleanup_failure_can_be_recovered(tmp_path, monkeypatch):
    # 切块回调用途：构造两个批次；text 为解析文本，返回 65 个固定片段。
    monkeypatch.setattr("henan_rag.rag_service.split_markdown", lambda text: ["chunk"] * 65)

    # 用途：模拟入库失败且 Qdrant 暂时无法删除，恢复连接后验证启动清理。
    # 参数：无（读取 tmp_path）。返回：无。
    async def run():
        service = await make_service(tmp_path)
        service.embed = AsyncMock(side_effect=[[[1.0, 0.0]] * 64, RuntimeError("模拟供应商故障")])
        original_delete = service.qdrant.delete
        service.qdrant.delete = AsyncMock(side_effect=RuntimeError("模拟 Qdrant 不可用"))
        try:
            with pytest.raises(DomainError) as failure:
                await service.ingest_file("failure.md", b"content", DEFAULT_KNOWLEDGE_BASE_ID)
            record = await service.metadata.get_document(DEFAULT_KNOWLEDGE_BASE_ID, failure.value.document_id)
            assert record["status"] == "cleanup_required"
            assert not record["file_available"]
            assert (await service.qdrant.count(service.settings.qdrant_collection, exact=True)).count == 64
            service.qdrant.delete = original_delete
            await service.recover_pending()
            record = await service.metadata.get_document(DEFAULT_KNOWLEDGE_BASE_ID, failure.value.document_id)
            assert record["status"] == "failed"
            assert (await service.qdrant.count(service.settings.qdrant_collection, exact=True)).count == 0
        finally:
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证客户端取消时线程仍读文件期间不释放锁、不删除文件，线程退出后彻底清理。
# 参数：tmp_path 为临时目录。返回：无；用线程事件控制解析结束，不依赖固定延时。
def test_cancel_import_waits_for_converter_thread(tmp_path):
    started = threading.Event()
    release = threading.Event()

    # 用途：模拟尚在读取原文件的 Docling 调用，等待测试主动放行。
    # 参数：path 为上传文件路径。返回：具有 Markdown 导出方法的解析结果。
    def convert(path):
        assert Path(path).exists()
        started.set()
        assert release.wait(timeout=5)
        assert Path(path).exists()
        # 导出回调用途：模拟已解析正文；无参数，返回固定 Markdown。
        return SimpleNamespace(document=SimpleNamespace(export_to_markdown=lambda: "content"))

    # 用途：发起上传并取消，验证实际线程完成前后的文件和写锁状态。
    # 参数：无（读取 tmp_path/started/release）。返回：无。
    async def run():
        service = await make_service(tmp_path)
        service.converter = SimpleNamespace(convert=convert)
        service.convert_markdown = RagService.convert_markdown.__get__(service, RagService)
        task = asyncio.create_task(service.ingest_file("cancel.md", b"content", DEFAULT_KNOWLEDGE_BASE_ID))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            # 让取消传播至 shield，读线程仍阻塞时任务必须保持未结束。
            await asyncio.sleep(0)
            assert not task.done()
            assert DEFAULT_KNOWLEDGE_BASE_ID in service._busy
            assert len(list(Path(service.settings.upload_dir).iterdir())) == 1
            with pytest.raises(DomainError) as busy:
                await service.delete_knowledge_base(DEFAULT_KNOWLEDGE_BASE_ID)
            assert busy.value.code == "KNOWLEDGE_BASE_BUSY"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
            assert not service._busy
            assert list(Path(service.settings.upload_dir).iterdir()) == []
            page = await service.metadata.list_documents(DEFAULT_KNOWLEDGE_BASE_ID)
            assert page.items[0].status == "failed"
            assert (await service.qdrant.count(service.settings.qdrant_collection, exact=True)).count == 0
        finally:
            release.set()
            if not task.done():
                await task
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证删除失败退出检索范围，重复删除能完成文件、向量及元数据清理。
# 参数：tmp_path 为临时目录。返回：无。
def test_delete_failure_is_retryable(tmp_path):
    # 用途：模拟 Qdrant 删除短暂故障，检查状态和第二次成功删除。
    # 参数：无（读取 tmp_path）。返回：无。
    async def run():
        service = await make_service(tmp_path)
        try:
            document = await service.ingest_file("retry.md", b"content", DEFAULT_KNOWLEDGE_BASE_ID)
            original_delete = service.qdrant.delete
            service.qdrant.delete = AsyncMock(side_effect=RuntimeError("模拟索引故障"))
            with pytest.raises(DomainError) as failure:
                await service.delete_document(DEFAULT_KNOWLEDGE_BASE_ID, document.document_id)
            assert failure.value.retryable
            assert (await service.metadata.get_document(DEFAULT_KNOWLEDGE_BASE_ID, document.document_id))["status"] == "delete_failed"
            assert await service.retrieve("content", [DEFAULT_KNOWLEDGE_BASE_ID], 2) == []
            service.qdrant.delete = original_delete
            await service.delete_document(DEFAULT_KNOWLEDGE_BASE_ID, document.document_id)
            assert (await service.metadata.list_documents(DEFAULT_KNOWLEDGE_BASE_ID)).total == 0
            assert list(Path(service.settings.upload_dir).iterdir()) == []
        finally:
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证创建 importing 记录已提交但响应取消时，也立即清理为 failed。
# 参数：tmp_path 为临时目录。返回：无；不会等到重启才处理这条记录。
def test_cancel_after_document_creation_cleans_metadata(tmp_path):
    # 用途：在创建记录之后阻塞返回，再取消上传并检查失败状态及写锁。
    # 参数：无（读取 tmp_path）。返回：无。
    async def run():
        service = await make_service(tmp_path)
        created = asyncio.Event()
        original_create = service.metadata.create_document

        # 用途：模拟元数据已提交，但客户端在服务拿到返回值前取消请求。
        # 参数：args/kwargs 为原 create_document 的文档、库和路径参数。返回：不正常返回。
        async def create_then_wait(*args, **kwargs):
            await original_create(*args, **kwargs)
            created.set()
            await asyncio.Event().wait()

        service.metadata.create_document = create_then_wait
        task = asyncio.create_task(service.ingest_file("cancel.md", b"content", DEFAULT_KNOWLEDGE_BASE_ID))
        try:
            await asyncio.wait_for(created.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            page = await service.metadata.list_documents(DEFAULT_KNOWLEDGE_BASE_ID)
            assert page.items[0].status == "failed"
            assert not page.items[0].file_available
            assert not service._busy
        finally:
            await service.qdrant.close()

    asyncio.run(run())


# 用途：验证 SSE 响应开始前拒绝未知知识库，旧路由移除且请求必须明确选择库。
# 参数：tmp_path 为临时目录；monkeypatch 替换全局服务和密钥配置。返回：无。
def test_api_prevalidates_scope_and_removes_legacy_routes(tmp_path, monkeypatch):
    service = asyncio.run(make_service(tmp_path))

    # 用途：提供临时服务给 API 路由。
    # 参数：无。返回：当前测试服务。
    def get_service():
        return service

    monkeypatch.setattr(app_module, "get_rag", get_service)
    monkeypatch.setattr(app_module.settings, "openai_api_key", "test-key")
    client = TestClient(app_module.app)
    try:
        unknown = str(uuid4())
        response = client.post("/api/chat/stream", json={"question": "问题", "knowledge_base_ids": [DEFAULT_KNOWLEDGE_BASE_ID, unknown]})
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["detail"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"
        assert client.post("/api/retrieve", json={"question": "问题"}).status_code == 422
        assert client.post("/api/retrieve", json={"question": "问题", "knowledge_base_ids": []}).status_code == 422
        assert client.post("/api/retrieve", json={"question": "问题", "knowledge_base_ids": [DEFAULT_KNOWLEDGE_BASE_ID], "tenant_id": "default"}).status_code == 422
        assert client.get("/api/documents").status_code == 404
        assert client.post("/api/ingest/file").status_code == 404
        default_delete = client.delete(f"/api/knowledge-bases/{DEFAULT_KNOWLEDGE_BASE_ID}")
        assert default_delete.status_code == 409
        page = client.get("/api/knowledge-bases?limit=1&offset=0").json()
        assert page["total"] == 1 and page["default_knowledge_base_id"] == DEFAULT_KNOWLEDGE_BASE_ID
        assert client.get("/api/knowledge-bases?limit=201").status_code == 422

        # 用途：提供超过旧上限的有效库 ID，测试 HTTP 校验与 SSE 开始前的完整范围检查。
        # 参数：无（读取 service）。返回：12 个新建库 ID。
        async def create_scope():
            return [
                (await service.metadata.create_knowledge_base(f"API 范围库 {index}")).id
                for index in range(12)
            ]

        selected = asyncio.run(create_scope())
        assert client.post("/api/retrieve", json={"question": "问题", "knowledge_base_ids": selected}).status_code == 200
        missing_scope = client.post("/api/chat/stream", json={"question": "问题", "knowledge_base_ids": [*selected, unknown]})
        assert missing_scope.status_code == 404
        assert missing_scope.headers["content-type"].startswith("application/json")
        asyncio.run(service.metadata.set_knowledge_base_status(selected[7], "delete_failed"))
        inactive_scope = client.post("/api/chat/stream", json={"question": "问题", "knowledge_base_ids": selected})
        assert inactive_scope.status_code == 409
        assert inactive_scope.json()["detail"]["code"] == "KNOWLEDGE_BASE_NOT_ACTIVE"
    finally:
        asyncio.run(service.qdrant.close())
