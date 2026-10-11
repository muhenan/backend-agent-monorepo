import asyncio
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from henan_rag.errors import DomainError
from henan_rag.metadata_repository import DEFAULT_KNOWLEDGE_BASE_ID, MetadataRepository


# 验证初始化持久化且保留默认库改名；tmp_path 是 pytest 临时目录，返回 None。
def test_default_initialization_is_idempotent_and_protected(tmp_path: Path) -> None:
    # 在独立事件循环运行场景；闭包捕获 tmp_path，返回 None。
    async def scenario() -> None:
        repository = MetadataRepository(str(tmp_path / "metadata.sqlite3"))
        await repository.initialize()
        default = await repository.get_knowledge_base(DEFAULT_KNOWLEDGE_BASE_ID)
        assert default.is_default and default.name == "默认知识库"
        assert default.created_at.endswith("Z")
        await repository.update_knowledge_base(default.id, name="我的资料")
        await repository.initialize()
        reloaded = MetadataRepository(str(tmp_path / "metadata.sqlite3"))
        page = await reloaded.list_knowledge_bases()
        assert page.total == 1 and page.items[0].name == "我的资料"
        assert await reloaded.health()
        for operation in (
            reloaded.delete_knowledge_base(default.id),
            reloaded.set_knowledge_base_status(default.id, "deleting"),
        ):
            with pytest.raises(DomainError) as caught:
                await operation
            assert caught.value.code == "DEFAULT_KNOWLEDGE_BASE_PROTECTED"

    asyncio.run(scenario())


# 验证标准化名称唯一及并发写入互斥；tmp_path 为临时目录，返回 None。
def test_normalized_names_and_concurrent_duplicate_creation(tmp_path: Path) -> None:
    # 并发创建等价名称并验证回滚；闭包捕获 tmp_path，返回 None。
    async def scenario() -> None:
        repository = MetadataRepository(str(tmp_path / "metadata.sqlite3"))
        await repository.initialize()
        results = await asyncio.gather(
            repository.create_knowledge_base(" ＲＡＧ ", " 学习 "),
            repository.create_knowledge_base("rag"),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, DomainError)]
        assert len(errors) == 1 and errors[0].code == "KNOWLEDGE_BASE_NAME_EXISTS"
        page = await repository.list_knowledge_bases()
        assert page.total == 2 and page.items[0].is_default
        other = await repository.create_knowledge_base("另一库")
        with pytest.raises(DomainError) as caught:
            await repository.update_knowledge_base(other.id, name="Rag")
        assert caught.value.status_code == 409
        assert (await repository.get_knowledge_base(other.id)).name == "另一库"

    asyncio.run(scenario())


# 验证分页、ready 统计及文档跨库隔离；tmp_path 为临时目录，返回 None。
def test_document_pagination_counts_and_ownership(tmp_path: Path) -> None:
    # 构造不同状态文档并查询；闭包捕获 tmp_path，返回 None。
    async def scenario() -> None:
        repository = MetadataRepository(str(tmp_path / "metadata.sqlite3"))
        await repository.initialize()
        library = await repository.create_knowledge_base("资料")
        first, second = str(uuid4()), str(uuid4())
        await repository.create_document(first, library.id, "first.md", "first.md")
        await repository.update_document(first, "ready", chunk_count=3, file_available=True)
        await repository.create_document(second, library.id, "second.md", "second.md")
        await repository.update_document(second, "failed", last_error="解析失败")
        summary = await repository.get_knowledge_base(library.id)
        assert (summary.document_count, summary.chunk_count) == (1, 3)
        page = await repository.list_documents(library.id, limit=1, offset=0)
        assert page.total == 2 and page.items[0].document_id == second
        assert "storage_path" not in page.model_dump()["items"][0]
        next_page = await repository.list_documents(library.id, limit=1, offset=1)
        assert next_page.items[0].document_id == first
        for operation in (
            repository.get_document(DEFAULT_KNOWLEDGE_BASE_ID, first),
            repository.delete_document(DEFAULT_KNOWLEDGE_BASE_ID, first),
        ):
            with pytest.raises(DomainError) as caught:
                await operation
            assert caught.value.status_code == 404
        assert (await repository.get_document(library.id, first))["storage_path"] == "first.md"
        await repository.delete_document(library.id, first)
        assert (await repository.get_knowledge_base(library.id)).chunk_count == 0

    asyncio.run(scenario())


# 验证恢复筛选与检索状态复核；tmp_path 为临时目录，返回 None。
def test_pending_recovery_and_ready_filter(tmp_path: Path) -> None:
    # 插入完整状态集合；闭包捕获 tmp_path，返回 None。
    async def scenario() -> None:
        repository = MetadataRepository(str(tmp_path / "metadata.sqlite3"))
        await repository.initialize()
        library = await repository.create_knowledge_base("恢复场景")
        ids: dict[str, str] = {}
        for status in (
            "importing", "ready", "failed", "cleanup_required", "deleting", "delete_failed"
        ):
            document_id = str(uuid4())
            ids[status] = document_id
            await repository.create_document(document_id, library.id, f"{status}.md", None)
            await repository.update_document(document_id, status)
        pending = await repository.get_pending_documents()
        assert {item["status"] for item in pending} == {
            "importing", "cleanup_required", "deleting", "delete_failed"
        }
        assert await repository.ready_document_ids(list(ids.values()), [library.id]) == {ids["ready"]}
        assert await repository.ready_document_ids(list(ids.values()), [DEFAULT_KNOWLEDGE_BASE_ID]) == set()
        assert await repository.ready_document_ids([], [library.id]) == set()
        await repository.set_knowledge_base_status(library.id, "delete_failed", "清理失败")
        assert await repository.ready_document_ids(list(ids.values()), [library.id]) == set()
        pending_libraries = await repository.get_pending_knowledge_bases()
        assert pending_libraries[0].id == library.id
        assert pending_libraries[0].last_error == "清理失败"
        with pytest.raises(DomainError) as caught:
            await repository.create_document(str(uuid4()), library.id, "blocked.md", None)
        assert caught.value.code == "KNOWLEDGE_BASE_NOT_ACTIVE"
        with pytest.raises(DomainError):
            await repository.update_knowledge_base(library.id, description="不能修改")
        await repository.delete_knowledge_base(library.id)
        assert not await repository.get_pending_documents()
        with pytest.raises(DomainError) as caught:
            await repository.get_knowledge_base(library.id)
        assert caught.value.code == "KNOWLEDGE_BASE_NOT_FOUND"

    asyncio.run(scenario())


# 验证超过 SQLite 参数上限的批量范围仍准确且仅打开一个连接；tmp_path 为临时目录，
# monkeypatch 用于把每个 SQLite 连接限制到 999 个参数并计数，返回 None。
def test_large_scope_reads_and_candidate_filter_use_bounded_sql(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "metadata.sqlite3"
    library_ids = [str(uuid4()) for _ in range(1205)]
    document_ids = [str(uuid4()) for _ in library_ids]
    connections = []
    original_connect = sqlite3.connect

    # 打开受限连接；args/kwargs 为 sqlite3.connect 原参数，返回带 999 参数上限的连接。
    def connect_with_limit(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        connections.append(connection)
        return connection

    # 构造大规模临时元数据并验证分批读取与归属过滤；无参数，返回 None。
    async def scenario() -> None:
        repository = MetadataRepository(str(database_path))
        await repository.initialize()
        timestamp = "2026-01-01T00:00:00Z"
        connection = original_connect(database_path)
        try:
            connection.executemany(
                """INSERT INTO knowledge_bases
                (id, name, name_key, description, is_default, status, created_at, updated_at)
                VALUES (?, ?, ?, '', 0, ?, ?, ?)""",
                [
                    (id, f"批量库 {index}", f"批量库 {index}",
                     "delete_failed" if index == 1204 else "active", timestamp, timestamp)
                    for index, id in enumerate(library_ids)
                ],
            )
            connection.executemany(
                """INSERT INTO documents
                (id, knowledge_base_id, source, status, chunk_count, created_at, updated_at)
                VALUES (?, ?, 'test.md', ?, 3, ?, ?)""",
                [
                    (id, library_ids[index], "importing" if index == 2 else "ready", timestamp, timestamp)
                    for index, id in enumerate(document_ids)
                ],
            )
            connection.commit()
        finally:
            connection.close()
        monkeypatch.setattr(sqlite3, "connect", connect_with_limit)
        selected = list(reversed(library_ids))
        summaries = await repository.get_knowledge_bases(selected)
        assert len(connections) == 1
        assert [item.id for item in summaries] == selected
        assert summaries[0].name == "批量库 1204" and summaries[0].status == "delete_failed"
        assert summaries[1].document_count == 1 and summaries[1].chunk_count == 3
        assert summaries[-3].document_count == 0 and summaries[-3].chunk_count == 0
        assert await repository.get_knowledge_bases([]) == []
        assert len(connections) == 1

        # 两维都超过 999；状态不合法或未选中的文档必须被过滤，候选重复不重复返回。
        valid = await repository.ready_document_ids(
            [*document_ids, document_ids[0]], [*library_ids[1:], DEFAULT_KNOWLEDGE_BASE_ID]
        )
        assert valid == set(document_ids) - {document_ids[0], document_ids[2], document_ids[-1]}
        assert len(connections) == 2
        with pytest.raises(DomainError) as missing:
            await repository.get_knowledge_bases([*library_ids[:600], str(uuid4()), *library_ids[600:]])
        assert missing.value.status_code == 404
        assert missing.value.code == "KNOWLEDGE_BASE_NOT_FOUND"
        assert len(connections) == 3

    asyncio.run(scenario())


# 验证相对路径及分页边界；tmp_path 为临时目录，返回 None。
def test_invalid_paths_pagination_and_database_constraints(tmp_path: Path) -> None:
    # 校验非法输入不产生文档；闭包捕获 tmp_path，返回 None。
    async def scenario() -> None:
        repository = MetadataRepository(str(tmp_path / "metadata.sqlite3"))
        await repository.initialize()
        for path in ("../outside.md", "/outside.md", "C:\\outside.md", "nested/../../outside.md"):
            with pytest.raises(DomainError) as caught:
                await repository.create_document(str(uuid4()), DEFAULT_KNOWLEDGE_BASE_ID, "a.md", path)
            assert caught.value.status_code == 422
        with pytest.raises(DomainError):
            await repository.list_knowledge_bases(limit=201)
        with pytest.raises(DomainError):
            await repository.list_documents(DEFAULT_KNOWLEDGE_BASE_ID, offset=-1)
        assert (await repository.list_documents(DEFAULT_KNOWLEDGE_BASE_ID)).total == 0

    asyncio.run(scenario())
    connection = sqlite3.connect(tmp_path / "metadata.sqlite3")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_key_list(documents)").fetchone()[2] == "knowledge_bases"
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE knowledge_bases SET status='invalid' WHERE id=?", (DEFAULT_KNOWLEDGE_BASE_ID,)
            )
    finally:
        connection.close()


# 验证损坏元数据不会泄露内部内容；tmp_path 为临时目录，返回 None。
def test_database_failure_is_safe_domain_error(tmp_path: Path) -> None:
    corrupted = tmp_path / "corrupted.sqlite3"
    corrupted.write_text("not a SQLite database", encoding="utf-8")

    # 调用故障仓库；闭包捕获 corrupted，返回 None。
    async def scenario() -> None:
        repository = MetadataRepository(str(corrupted))
        assert not await repository.health()
        with pytest.raises(DomainError) as caught:
            await repository.list_knowledge_bases()
        error = caught.value
        assert error.status_code == 503 and error.retryable
        assert error.code == "METADATA_UNAVAILABLE"
        assert str(corrupted) not in str(error.to_detail())

    asyncio.run(scenario())


# 验证请求取消后等待工作线程完成再释放调用；tmp_path 为临时目录，返回 None。
def test_cancellation_waits_for_transaction_before_cleanup(tmp_path: Path) -> None:
    # 通过线程事件模拟取消落在事务中间；闭包捕获 tmp_path，返回 None。
    async def scenario() -> None:
        repository = MetadataRepository(str(tmp_path / "metadata.sqlite3"))
        await repository.initialize()
        document_id = str(uuid4())
        await repository.create_document(document_id, DEFAULT_KNOWLEDGE_BASE_ID, "cancel.md", None)
        started = threading.Event()
        release = threading.Event()

        # 暂停事务再设置 ready；connection 为工作线程连接，闭包事件控制时序，返回 None。
        def delayed_ready(connection: sqlite3.Connection) -> None:
            started.set()
            assert release.wait(timeout=2)
            connection.execute("UPDATE documents SET status='ready' WHERE id=?", (document_id,))

        task = asyncio.create_task(repository._run(delayed_ready, write=True))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await repository.update_document(document_id, "failed", last_error="已取消")
        assert (await repository.get_document(DEFAULT_KNOWLEDGE_BASE_ID, document_id))["status"] == "failed"

    asyncio.run(scenario())
