"""真实上传多块合成资料，检查网页分块浏览接口与 Qdrant 实际存储一致。"""

import argparse
import json
from urllib.parse import quote
from uuid import uuid4

from live_knowledge_base_smoke import expect_error, request, upload


# 用途：生成含多个章节的公开合成资料，让真实 Docling/切块流程产生超过一页的内容。
# 参数：无。返回：Markdown 文本；不包含真实业务数据。
def sample_document():
    sections = ["# 分块浏览学习样例\n\n以下内容为虚构资料，用来观察分块、重叠与存储字段。"]
    for number in range(1, 17):
        title = f"## 第 {number} 节：实验步骤 {number}"
        body = (
            f"章节标记 SECTION-{number:02d}。本节学习如何组织文档、检查解析结果和追踪检索证据。"
            "先创建学习知识库，再把合成资料上传到指定库。上传完成后，确认文档状态为已入库。"
            "打开分块浏览，观察编号与正文，比较相邻片段末尾和开头的重复文本。"
            "每个片段都有独立的向量记录，知识库和文档标识用于限定所属范围。"
            "数值预览只展示向量的一小部分，完整的正文和元数据则按实际存储展示。"
            "切换聊天页，选择资料范围并提问；可以通过引用回到相关文档片段。"
            "这些文字只用于演示，实际项目需要根据文档类型和检索效果调整切块策略。"
        )
        sections.append(f"{title}\n\n{body * 3}")
    return "\n\n".join(sections)


# 用途：验证真实上传、按块顺序分页、存储字段一致性、归属校验和错误参数。
# 参数：base_url 为 RAG 服务；qdrant_url 为向量服务；collection 为新集合；
# keep 表示保留脚本创建的示例供网页体验。返回：验收摘要；失败时仅清理本脚本的库。
def run(base_url, qdrant_url, collection, keep=False):
    suffix = uuid4().hex[:6]
    knowledge_base_id = None
    succeeded = False
    try:
        knowledge_base = request(
            base_url,
            "/api/knowledge-bases",
            "POST",
            {
                "name": f"分块可视化示例 · {suffix}",
                "description": "合成多章节文档，用于观察分块和向量存储",
            },
        )
        knowledge_base_id = knowledge_base["id"]
        document = upload(base_url, knowledge_base_id, "chunk-browser-demo.md", sample_document())
        assert document["status"] == "ready" and document["chunk_count"] > 12, document
        path = (
            f"/api/knowledge-bases/{knowledge_base_id}/documents/{document['document_id']}/chunks"
        )
        chunks, offsets, offset = [], [], 0
        while True:
            assert offset not in offsets, "分页游标未前进"
            offsets.append(offset)
            page = request(base_url, f"{path}?limit=12&offset={offset}")
            assert page["document"]["document_id"] == document["document_id"]
            assert page["knowledge_base"]["id"] == knowledge_base_id
            assert page["total"] == document["chunk_count"] and len(page["items"]) <= 12
            for chunk in page["items"]:
                assert chunk["character_count"] == len(chunk["text"])
                assert chunk["payload"]["text"] == chunk["text"]
                assert chunk["payload"]["knowledge_base_id"] == knowledge_base_id
                assert chunk["payload"]["document_id"] == document["document_id"]
                assert chunk["indexing_state"] == chunk["payload"]["indexing_state"] == "ready"
                assert chunk["vector_dimensions"] >= len(chunk["vector_preview"]) == 8
            chunks.extend(page["items"])
            assert len(chunks) <= document["chunk_count"], "分页重复返回了存储块"
            if page["next_offset"] is None:
                break
            assert page["next_offset"] > offset, "下一页游标必须前进"
            offset = page["next_offset"]
        assert len(offsets) >= 2
        assert len(chunks) == document["chunk_count"]
        assert [chunk["chunk_index"] for chunk in chunks] == list(range(len(chunks)))
        assert len({chunk["point_id"] for chunk in chunks}) == len(chunks)
        combined = "\n".join(chunk["text"] for chunk in chunks)
        assert all(f"SECTION-{number:02d}" in combined for number in range(1, 17))
        stored = request(
            qdrant_url,
            f"/collections/{quote(collection, safe='')}/points",
            "POST",
            {
                "ids": [chunk["point_id"] for chunk in chunks],
                "with_payload": True,
                "with_vector": True,
            },
        )
        by_id = {str(point["id"]): point for point in stored["result"]}
        assert len(by_id) == len(chunks)
        for chunk in chunks:
            point = by_id[chunk["point_id"]]
            assert chunk["payload"] == point["payload"]
            assert chunk["vector_dimensions"] == len(point["vector"])
            assert chunk["vector_preview"] == point["vector"][:8]
        after_end = request(base_url, f"{path}?limit=12&offset={len(chunks)}")
        assert after_end["items"] == [] and after_end["next_offset"] is None
        expect_error(base_url, path + "?limit=0", 422, "GET")
        expect_error(base_url, path + "?offset=-1", 422, "GET")
        default_id = request(base_url, "/api/knowledge-bases?limit=1")["default_knowledge_base_id"]
        expect_error(
            base_url,
            f"/api/knowledge-bases/{default_id}/documents/{document['document_id']}/chunks",
            404,
            "GET",
        )
        succeeded = True
        return {
            "result": "LIVE_DOCUMENT_INSPECTION_OK",
            "chunk_count": len(chunks),
            "page_count": len(offsets),
            "vector_dimensions": chunks[0]["vector_dimensions"],
            "kept_knowledge_base_id": knowledge_base_id if keep else None,
            "document_id": document["document_id"] if keep else None,
        }
    finally:
        if knowledge_base_id and not (keep and succeeded):
            request(base_url, f"/api/knowledge-bases/{knowledge_base_id}", "DELETE")


# 用途：读取真实服务地址与保留选项，打印简短验收结果。
# 参数：无；来自 CLI。返回：无；断言失败时以非零状态退出。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--qdrant-url", default="http://qdrant:6333")
    parser.add_argument("--collection", default="henan_rag_knowledge_chunks")
    parser.add_argument("--keep", action="store_true", help="保留多块合成资料供浏览器测试")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.base_url.rstrip("/"), args.qdrant_url.rstrip("/"), args.collection, args.keep),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
