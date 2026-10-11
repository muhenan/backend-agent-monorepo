import asyncio
import sqlite3
import unicodedata
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from .errors import DomainError
from .schemas import (
    DocumentPage,
    DocumentSummary,
    KnowledgeBasePage,
    KnowledgeBaseSummary,
    clean_knowledge_base_description,
    clean_knowledge_base_name,
)

DEFAULT_KNOWLEDGE_BASE_ID = "00000000-0000-4000-8000-000000000001"
_SQL_PARAMETER_BATCH_SIZE = 500
_T = TypeVar("_T")
_KNOWLEDGE_BASE_STATUSES = {"active", "deleting", "delete_failed"}
_DOCUMENT_STATUSES = {
    "importing", "ready", "failed", "cleanup_required", "deleting", "delete_failed"
}
_KNOWLEDGE_BASE_SELECT = """
    SELECT kb.*,
        COALESCE(counts.document_count, 0) AS document_count,
        COALESCE(counts.chunk_count, 0) AS chunk_count
    FROM knowledge_bases AS kb
    LEFT JOIN (
        SELECT knowledge_base_id, COUNT(*) AS document_count, SUM(chunk_count) AS chunk_count
        FROM documents WHERE status = 'ready' GROUP BY knowledge_base_id
    ) AS counts ON counts.knowledge_base_id = kb.id
"""
_DDL = """
    CREATE TABLE IF NOT EXISTS knowledge_bases (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        name_key TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'deleting', 'delete_failed')),
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_one_default_knowledge_base
        ON knowledge_bases(is_default) WHERE is_default = 1;
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE RESTRICT,
        source TEXT NOT NULL,
        storage_path TEXT,
        file_available INTEGER NOT NULL DEFAULT 0 CHECK (file_available IN (0, 1)),
        status TEXT NOT NULL CHECK (status IN (
            'importing', 'ready', 'failed', 'cleanup_required', 'deleting', 'delete_failed'
        )),
        chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_documents_kb_created
        ON documents(knowledge_base_id, created_at DESC, id);
    CREATE INDEX IF NOT EXISTS idx_documents_kb_status
        ON documents(knowledge_base_id, status);
"""


# 生成统一 UTC 时间；无参数，返回带 Z 后缀的 ISO 8601 字符串。
def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


# 生成唯一名称键；name 为已清理名称，返回 NFKC 标准化及 casefold 后的字符串。
def _name_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()


# 验证分页；limit 为页大小、offset 为偏移，合法时无返回值，非法时抛业务错误。
def _validate_pagination(limit: int, offset: int) -> None:
    if not 1 <= limit <= 200 or offset < 0:
        raise DomainError(422, "VALIDATION_ERROR", "分页 limit 必须为 1 至 200，offset 不能为负数。")


# 转换文档内部记录；row 是 SQLite 行，返回包含 storage_path 及 document_id 的字典。
def _document_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["document_id"] = record.pop("id")
    record["file_available"] = bool(record["file_available"])
    return record


# 转换库摘要；row 是包含 ready 文档统计的 SQLite 行，返回对外 DTO。
def _knowledge_base_summary(row: sqlite3.Row) -> KnowledgeBaseSummary:
    return KnowledgeBaseSummary.model_validate(dict(row))


# 读取库；connection 是本线程连接、kb_id 为库 ID，返回原始行，不存在时抛 404。
def _require_knowledge_base(connection: sqlite3.Connection, kb_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
    if row is None:
        raise DomainError(404, "KNOWLEDGE_BASE_NOT_FOUND", "所选知识库不存在，请刷新后重新选择。")
    return row


# 读取库内文档；connection 为连接、kb_id/document_id 为目标 ID，返回行，不归属时抛 404。
def _require_document(
    connection: sqlite3.Connection, kb_id: str, document_id: str
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM documents WHERE knowledge_base_id = ? AND id = ?", (kb_id, document_id)
    ).fetchone()
    if row is None:
        raise DomainError(404, "DOCUMENT_NOT_FOUND", "文档不存在或不属于所选知识库。")
    return row


class MetadataRepository:
    # 配置仓库；self 为当前实例、db_path 为 SQLite 文件路径，创建时不访问磁盘，无返回值。
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)

    # 执行短数据库操作；self 为仓库、operation 接收独立连接、write 控制写事务，返回操作结果。
    async def _run(self, operation: Callable[[sqlite3.Connection], _T], *, write: bool = False) -> _T:
        # 在线程内创建并关闭连接；闭包捕获 self/operation/write，返回操作结果。
        def execute() -> _T:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                str(self.db_path), timeout=5.0, isolation_level=None,
                autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL,
            )
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                try:
                    result = operation(connection)
                    if write:
                        connection.execute("COMMIT")
                    return result
                except BaseException:
                    if write and connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
            finally:
                connection.close()

        task = asyncio.create_task(asyncio.to_thread(execute))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # 线程内事务不能被协程取消中断。先等提交/回滚及连接关闭，再把取消传给 service。
            # 否则 service 可能先完成失败清理，旧线程随后又把文档改回 ready。
            while not task.done():
                with suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(task)
            if not task.cancelled():
                task.exception()
            raise
        except (sqlite3.DatabaseError, OSError) as error:
            raise DomainError(
                503, "METADATA_UNAVAILABLE", "知识库元数据暂时不可用，请稍后重试。", retryable=True
            ) from error

    # 建表和默认库初始化；self 为仓库，不迁移历史资料，不覆盖默认库改名，返回 None。
    async def initialize(self) -> None:
        # 建表并插入默认库；connection 为独立连接，返回 None。
        def initialize_tables(connection: sqlite3.Connection) -> None:
            # executescript 隐式提交，因此单独完成建表，再显式开启默认库插入事务。
            connection.executescript(_DDL)
            timestamp = _utc_now()
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """INSERT INTO knowledge_bases
                    (id, name, name_key, description, is_default, status, created_at, updated_at)
                    VALUES (?, ?, ?, '', 1, 'active', ?, ?) ON CONFLICT(id) DO NOTHING""",
                    (DEFAULT_KNOWLEDGE_BASE_ID, "默认知识库", _name_key("默认知识库"), timestamp, timestamp),
                )
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

        await self._run(initialize_tables)

    # 创建库；self 为仓库、name 为名称、description 为描述，返回带稳定 UUID 的摘要。
    async def create_knowledge_base(
        self, name: str, description: str = ""
    ) -> KnowledgeBaseSummary:
        try:
            name = clean_knowledge_base_name(name)
            description = clean_knowledge_base_description(description)
        except ValueError as error:
            raise DomainError(422, "VALIDATION_ERROR", str(error)) from error
        kb_id = str(uuid4())

        # 事务插入并读取摘要；connection 为连接，返回新库，重名时抛 409。
        def create(connection: sqlite3.Connection) -> KnowledgeBaseSummary:
            timestamp = _utc_now()
            try:
                connection.execute(
                    """INSERT INTO knowledge_bases
                    (id, name, name_key, description, is_default, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, 'active', ?, ?)""",
                    (kb_id, name, _name_key(name), description, timestamp, timestamp),
                )
            except sqlite3.IntegrityError as error:
                raise DomainError(409, "KNOWLEDGE_BASE_NAME_EXISTS", "知识库名称已存在。") from error
            row = connection.execute(_KNOWLEDGE_BASE_SELECT + " WHERE kb.id = ?", (kb_id,)).fetchone()
            return _knowledge_base_summary(row)

        return await self._run(create, write=True)

    # 查询库；self 为仓库、id 为稳定库 ID，返回摘要，未知库抛 404。
    async def get_knowledge_base(self, id: str) -> KnowledgeBaseSummary:
        # 读取统计摘要；connection 为本次连接，返回目标库摘要。
        def get(connection: sqlite3.Connection) -> KnowledgeBaseSummary:
            row = connection.execute(_KNOWLEDGE_BASE_SELECT + " WHERE kb.id = ?", (id,)).fetchone()
            if row is None:
                raise DomainError(404, "KNOWLEDGE_BASE_NOT_FOUND", "所选知识库不存在，请刷新后重新选择。")
            return _knowledge_base_summary(row)

        return await self._run(get)

    # 批量查询库；self 为仓库、ids 为明确选择的库 ID，返回同顺序摘要（不默认为全部库）。
    # 在一个连接和读快照内分批查询；任一库不存在则整个请求抛 404，不返回部分范围。
    async def get_knowledge_bases(self, ids: list[str]) -> list[KnowledgeBaseSummary]:
        if not ids:
            return []
        unique_ids = list(dict.fromkeys(ids))

        # 分批读取统计摘要；connection 为本次连接，返回保持输入顺序及同一名称快照的列表。
        def get_many(connection: sqlite3.Connection) -> list[KnowledgeBaseSummary]:
            connection.execute("BEGIN")
            try:
                summaries: dict[str, KnowledgeBaseSummary] = {}
                for start in range(0, len(unique_ids), _SQL_PARAMETER_BATCH_SIZE):
                    batch = unique_ids[start : start + _SQL_PARAMETER_BATCH_SIZE]
                    placeholders = ",".join("?" for _ in batch)
                    rows = connection.execute(
                        f"""WITH selected AS (
                            SELECT * FROM knowledge_bases WHERE id IN ({placeholders})
                        ), counts AS (
                            SELECT d.knowledge_base_id, COUNT(*) AS document_count,
                                SUM(d.chunk_count) AS chunk_count
                            FROM selected JOIN documents d ON d.knowledge_base_id = selected.id
                            WHERE d.status = 'ready' GROUP BY d.knowledge_base_id
                        )
                        SELECT kb.*, COALESCE(counts.document_count, 0) AS document_count,
                            COALESCE(counts.chunk_count, 0) AS chunk_count
                        FROM selected kb
                        LEFT JOIN counts ON counts.knowledge_base_id = kb.id""",
                        batch,
                    ).fetchall()
                    summaries.update((row["id"], _knowledge_base_summary(row)) for row in rows)
                if len(summaries) != len(unique_ids):
                    raise DomainError(
                        404, "KNOWLEDGE_BASE_NOT_FOUND", "所选知识库不存在，请刷新后重新选择。"
                    )
                return [summaries[id] for id in ids]
            finally:
                connection.execute("ROLLBACK")

        return await self._run(get_many)

    # 分页列库；self 为仓库、limit 为页大小、offset 为偏移，返回默认库优先的分页摘要。
    async def list_knowledge_bases(self, limit: int = 50, offset: int = 0) -> KnowledgeBasePage:
        _validate_pagination(limit, offset)

        # 在读快照内获取总数和当前页；connection 为连接，返回分页 DTO。
        def list_page(connection: sqlite3.Connection) -> KnowledgeBasePage:
            connection.execute("BEGIN")
            try:
                total = connection.execute("SELECT COUNT(*) FROM knowledge_bases").fetchone()[0]
                rows = connection.execute(
                    _KNOWLEDGE_BASE_SELECT
                    + " ORDER BY kb.is_default DESC, kb.created_at DESC, kb.id ASC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                return KnowledgeBasePage(
                    items=[_knowledge_base_summary(row) for row in rows], total=total,
                    limit=limit, offset=offset, default_knowledge_base_id=DEFAULT_KNOWLEDGE_BASE_ID,
                )
            finally:
                connection.execute("ROLLBACK")

        return await self._run(list_page)

    # 修改库；self 为仓库、id 为库 ID，name/description 为新值，None 保留原值，返回摘要。
    async def update_knowledge_base(
        self, id: str, name: str | None = None, description: str | None = None
    ) -> KnowledgeBaseSummary:
        try:
            if name is not None:
                name = clean_knowledge_base_name(name)
            if description is not None:
                description = clean_knowledge_base_description(description)
        except ValueError as error:
            raise DomainError(422, "VALIDATION_ERROR", str(error)) from error

        # 校验状态并修改；connection 为事务连接，返回摘要，重名或非 active 抛 409。
        def update(connection: sqlite3.Connection) -> KnowledgeBaseSummary:
            row = _require_knowledge_base(connection, id)
            if row["status"] != "active":
                raise DomainError(409, "KNOWLEDGE_BASE_NOT_ACTIVE", "知识库正在删除或删除失败，不能修改。")
            new_name = row["name"] if name is None else name
            new_description = row["description"] if description is None else description
            try:
                connection.execute(
                    "UPDATE knowledge_bases SET name=?, name_key=?, description=?, updated_at=? WHERE id=?",
                    (new_name, _name_key(new_name), new_description, _utc_now(), id),
                )
            except sqlite3.IntegrityError as error:
                raise DomainError(409, "KNOWLEDGE_BASE_NAME_EXISTS", "知识库名称已存在。") from error
            updated = connection.execute(_KNOWLEDGE_BASE_SELECT + " WHERE kb.id = ?", (id,)).fetchone()
            return _knowledge_base_summary(updated)

        return await self._run(update, write=True)

    # 设库状态；self 为仓库、id 为库 ID、status 为合法状态、last_error 为公开原因，返回 None。
    async def set_knowledge_base_status(
        self, id: str, status: str, last_error: str | None = None
    ) -> None:
        if status not in _KNOWLEDGE_BASE_STATUSES:
            raise DomainError(422, "VALIDATION_ERROR", "未知知识库状态。")

        # 更新状态并保护默认库；connection 为连接，返回 None。
        def set_status(connection: sqlite3.Connection) -> None:
            row = _require_knowledge_base(connection, id)
            if (id == DEFAULT_KNOWLEDGE_BASE_ID or row["is_default"]) and status != "active":
                raise DomainError(409, "DEFAULT_KNOWLEDGE_BASE_PROTECTED", "默认知识库不能删除。")
            connection.execute(
                "UPDATE knowledge_bases SET status=?, last_error=?, updated_at=? WHERE id=?",
                (status, last_error, _utc_now(), id),
            )

        await self._run(set_status, write=True)

    # 删除库及文档元数据；self 为仓库、id 为库 ID，service 预先清理文件/向量，返回 None。
    async def delete_knowledge_base(self, id: str) -> None:
        # 事务保护默认库并删除关联记录；connection 为连接，返回 None。
        def delete(connection: sqlite3.Connection) -> None:
            row = _require_knowledge_base(connection, id)
            if id == DEFAULT_KNOWLEDGE_BASE_ID or row["is_default"]:
                raise DomainError(409, "DEFAULT_KNOWLEDGE_BASE_PROTECTED", "默认知识库不能删除。")
            connection.execute("DELETE FROM documents WHERE knowledge_base_id = ?", (id,))
            connection.execute("DELETE FROM knowledge_bases WHERE id = ?", (id,))

        await self._run(delete, write=True)

    # 建立 importing 文档；self 为仓库、document_id 为 UUID、kb_id 为归属库、source 为文件名、
    # storage_path 为上传目录内相对路径，返回内部记录；文件尚未写入故 file_available=False。
    async def create_document(
        self, document_id: str, kb_id: str, source: str, storage_path: str | None
    ) -> dict[str, Any]:
        if storage_path is not None:
            relative = Path(storage_path)
            if (relative.is_absolute() or storage_path.startswith("/") or ".." in relative.parts
                    or "\\" in storage_path or ":" in storage_path):
                raise DomainError(422, "VALIDATION_ERROR", "文档存储路径必须是上传目录内的相对路径。")

        # 检查库状态并写入文档；connection 为事务连接，返回内部记录。
        def create(connection: sqlite3.Connection) -> dict[str, Any]:
            kb = _require_knowledge_base(connection, kb_id)
            if kb["status"] != "active":
                raise DomainError(409, "KNOWLEDGE_BASE_NOT_ACTIVE", "知识库正在删除或删除失败，不能上传。")
            timestamp = _utc_now()
            try:
                connection.execute(
                    """INSERT INTO documents
                    (id, knowledge_base_id, source, storage_path, file_available, status,
                     chunk_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, 'importing', 0, ?, ?)""",
                    (document_id, kb_id, source, storage_path, timestamp, timestamp),
                )
            except sqlite3.IntegrityError as error:
                raise DomainError(409, "DOCUMENT_EXISTS", "文档 ID 已存在。") from error
            return _document_record(_require_document(connection, kb_id, document_id))

        return await self._run(create, write=True)

    # 读取文档；self 为仓库、kb_id 为库 ID、document_id 为文档 ID，返回记录，归属错误抛 404。
    async def get_document(self, kb_id: str, document_id: str) -> dict[str, Any]:
        # 复合条件读取；connection 为连接，返回含路径记录。
        def get(connection: sqlite3.Connection) -> dict[str, Any]:
            return _document_record(_require_document(connection, kb_id, document_id))

        return await self._run(get)

    # 更新文档；self 为仓库、document_id 为 ID、status 为处理状态、chunk_count 为片段数量、
    # file_available 表示原文件存在、last_error 为公开原因；覆盖整组状态字段，返回 None。
    async def update_document(
        self, document_id: str, status: str, *, chunk_count: int = 0,
        file_available: bool = False, last_error: str | None = None,
    ) -> None:
        if status not in _DOCUMENT_STATUSES or chunk_count < 0:
            raise DomainError(422, "VALIDATION_ERROR", "未知文档状态或无效片段数量。")

        # 按全局文档 ID 更新；connection 为事务连接，未知文档抛 404，返回 None。
        def update(connection: sqlite3.Connection) -> None:
            cursor = connection.execute(
                """UPDATE documents SET status=?, chunk_count=?, file_available=?,
                    last_error=?, updated_at=? WHERE id=?""",
                (status, chunk_count, int(file_available), last_error, _utc_now(), document_id),
            )
            if cursor.rowcount == 0:
                raise DomainError(404, "DOCUMENT_NOT_FOUND", "文档不存在。")

        await self._run(update, write=True)

    # 删除文档元数据；self 为仓库、kb_id 为归属库、document_id 为文档 ID，返回 None。
    async def delete_document(self, kb_id: str, document_id: str) -> None:
        # 验证归属后事务删除；connection 为连接，跨库操作抛 404，返回 None。
        def delete(connection: sqlite3.Connection) -> None:
            _require_document(connection, kb_id, document_id)
            connection.execute(
                "DELETE FROM documents WHERE knowledge_base_id = ? AND id = ?", (kb_id, document_id)
            )

        await self._run(delete, write=True)

    # 分页列出全部状态文档；self 为仓库、kb_id 为库 ID、limit 页大小、offset 偏移，返回无路径页。
    async def list_documents(self, kb_id: str, limit: int = 50, offset: int = 0) -> DocumentPage:
        _validate_pagination(limit, offset)

        # 读快照校验库并获取总数和页；connection 为连接，返回文档分页 DTO。
        def list_page(connection: sqlite3.Connection) -> DocumentPage:
            connection.execute("BEGIN")
            try:
                _require_knowledge_base(connection, kb_id)
                total = connection.execute(
                    "SELECT COUNT(*) FROM documents WHERE knowledge_base_id = ?", (kb_id,)
                ).fetchone()[0]
                rows = connection.execute(
                    """SELECT * FROM documents WHERE knowledge_base_id = ?
                    ORDER BY created_at DESC, id ASC LIMIT ? OFFSET ?""", (kb_id, limit, offset)
                ).fetchall()
                return DocumentPage(
                    items=[DocumentSummary.model_validate(_document_record(row)) for row in rows],
                    total=total, limit=limit, offset=offset,
                )
            finally:
                connection.execute("ROLLBACK")

        return await self._run(list_page)

    # 获取库内所有内部记录；self 为仓库、kb_id 为库 ID，返回含路径列表供库删除清理使用。
    async def get_documents_for_knowledge_base(self, kb_id: str) -> list[dict[str, Any]]:
        # 校验库并读取所有文档；connection 为连接，返回内部列表。
        def get_all(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            _require_knowledge_base(connection, kb_id)
            rows = connection.execute(
                "SELECT * FROM documents WHERE knowledge_base_id = ? ORDER BY created_at, id", (kb_id,)
            ).fetchall()
            return [_document_record(row) for row in rows]

        return await self._run(get_all)

    # 获取重启待处理文档；self 为仓库，返回 importing/cleanup_required/deleting/delete_failed。
    async def get_pending_documents(self) -> list[dict[str, Any]]:
        # 查找未完成入库或删除记录；connection 为连接，返回内部文档列表。
        def get_pending(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = connection.execute(
                """SELECT * FROM documents
                WHERE status IN ('importing', 'cleanup_required', 'deleting', 'delete_failed')
                ORDER BY created_at, id"""
            ).fetchall()
            return [_document_record(row) for row in rows]

        return await self._run(get_pending)

    # 获取未完成删除的库；self 为仓库，返回 deleting/delete_failed 状态摘要列表。
    async def get_pending_knowledge_bases(self) -> list[KnowledgeBaseSummary]:
        # 查找待恢复库删除；connection 为连接，返回知识库摘要列表。
        def get_pending(connection: sqlite3.Connection) -> list[KnowledgeBaseSummary]:
            rows = connection.execute(
                _KNOWLEDGE_BASE_SELECT + " WHERE kb.status IN ('deleting', 'delete_failed')"
                + " ORDER BY kb.created_at, kb.id"
            ).fetchall()
            return [_knowledge_base_summary(row) for row in rows]

        return await self._run(get_pending)

    # 校验可检索候选；self 为仓库、document_ids 为候选文档、knowledge_base_ids 为所选库，
    # 返回归属所选 active 库且状态 ready 的文档 ID 集合。
    async def ready_document_ids(
        self, document_ids: list[str], knowledge_base_ids: list[str]
    ) -> set[str]:
        if not document_ids or not knowledge_base_ids:
            return set()
        document_ids = list(dict.fromkeys(document_ids))
        selected_ids = set(knowledge_base_ids)

        # 只按候选文档分批查询，避免所选库数量占用 SQL 参数；connection 为连接，返回 ready ID。
        def get_ready(connection: sqlite3.Connection) -> set[str]:
            result: set[str] = set()
            for start in range(0, len(document_ids), _SQL_PARAMETER_BATCH_SIZE):
                batch = document_ids[start : start + _SQL_PARAMETER_BATCH_SIZE]
                document_placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""SELECT d.id, d.knowledge_base_id FROM documents d
                    JOIN knowledge_bases kb ON kb.id = d.knowledge_base_id
                    WHERE d.status = 'ready' AND kb.status = 'active'
                    AND d.id IN ({document_placeholders})""",
                    batch,
                ).fetchall()
                result.update(row["id"] for row in rows if row["knowledge_base_id"] in selected_ids)
            return result

        return await self._run(get_ready)

    # 检查元数据可读；self 为仓库，表可读返回 True，文件或数据库故障返回 False。
    async def health(self) -> bool:
        # 探测知识库表；connection 为连接，读取成功返回 True。
        def check(connection: sqlite3.Connection) -> bool:
            connection.execute("SELECT id FROM knowledge_bases LIMIT 1").fetchone()
            return True

        try:
            return await self._run(check)
        except DomainError:
            return False
