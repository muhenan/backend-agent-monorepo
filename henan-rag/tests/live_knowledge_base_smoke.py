"""手动执行的真实 HTTP/模型验收脚本，不由 pytest 自动运行。"""

import argparse
import json
import sys
from collections import Counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


# 用途：发送真实 HTTP 请求并解析 JSON，204 时返回 None。
# 参数 base_url 为服务地址；path 为 API 路径；method 为方法；data 为 JSON 输入；
# raw 为可选二进制请求体；content_type 为其媒体类型。返回：JSON 对象或 None。
def request(base_url, path, method="GET", data=None, *, raw=None, content_type=None):
    headers = {}
    if data is not None:
        raw = json.dumps(data, ensure_ascii=False).encode()
        content_type = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    with urlopen(
        Request(base_url + path, data=raw, method=method, headers=headers), timeout=90
    ) as response:
        body = response.read()
        return json.loads(body) if body else None


# 用途：验证指定无效请求确实被服务拒绝，避免选择范围被自动扩大或误删。
# 参数 base_url 为服务地址；path 为 API 路径；status 为期望 HTTP 状态；
# method 为请求方法；data 为 JSON 输入。返回：无，状态不符时抛出断言异常。
def expect_error(base_url, path, status, method="POST", data=None):
    try:
        request(base_url, path, method, data)
    except HTTPError as error:
        assert error.code == status, (error.code, error.read().decode())
    else:
        raise AssertionError(f"Expected HTTP {status}: {method} {path}")


# 用途：将合成 Markdown 通过与网页相同的 multipart 接口上传到指定知识库。
# 参数 base_url 为服务地址；knowledge_base_id 为目标库；name 为文件名；text 为原文。
# 返回：已入库文档的 JSON 摘要。
def upload(base_url, knowledge_base_id, name, text):
    boundary = "rag-smoke-" + uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: text/markdown\r\n\r\n"
        f"{text}\r\n--{boundary}--\r\n"
    ).encode()
    return request(
        base_url,
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        "POST",
        raw=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )


# 用途：调用实际配置的 Agent 和 Embeddings，验证多库工具范围、零命中记录与文字增量。
# 参数 base_url 为服务地址；knowledge_base_ids 为本次挂载范围；
# evidence_ids 为含示例证据的库 ID 集合。返回：事件计数，用于打印简短验收结果。
def stream_answer(base_url, knowledge_base_ids, evidence_ids):
    payload = {
        "question": "青禾平台的产品版本和部署最低内存分别是多少？请分别引用证据。",
        "knowledge_base_ids": knowledge_base_ids,
        "top_k": 6,
    }
    http_request = Request(
        base_url + "/api/chat/stream",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    names, deltas = [], []
    final, started, ended = None, None, None
    with urlopen(http_request, timeout=90) as response:
        assert response.headers["Content-Type"].startswith("text/event-stream")
        name = ""
        for raw in response:
            line = raw.decode().strip()
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                names.append(name)
                if name == "error":
                    raise RuntimeError(data["message"])
                if name == "tool_start":
                    started = data
                if name == "tool_end":
                    ended = data
                if name == "text_delta":
                    deltas.append(data["delta"])
                if name == "done":
                    final = data
    assert final and final["answer"] == "".join(deltas)
    assert "3.2" in final["answer"] and "8" in final["answer"], final["answer"]
    assert names.count("tool_start") == names.count("tool_end") == 1
    assert started["call_id"] == ended["call_id"]
    assert names.index("tool_end") < names.index("text_delta") < names.index("done")
    expected_ids = set(knowledge_base_ids)
    assert {item["id"] for item in started["knowledge_bases"]} == expected_ids
    assert {item["id"] for item in final["knowledge_bases"]} == expected_ids
    assert {item["knowledge_base_id"] for item in final["citations"]} == evidence_ids
    assert {item["id"] for item in ended["hits_by_knowledge_base"]} == expected_ids
    for item in ended["hits_by_knowledge_base"]:
        assert (item["chunk_count"] > 0) == (item["id"] in evidence_ids)
    return dict(Counter(names))


# 用途：创建独立合成知识库，验收 CRUD、真实入库、范围过滤与流式问答。
# 参数 base_url 为服务地址；keep 为是否留下两个合成示例库供手动体验。
# 返回：验收摘要；默认在 finally 中删除脚本自己创建的库，不影响已有库。
def run(base_url, keep=False):
    suffix = uuid4().hex[:6]
    created = []
    kept = set()
    try:
        page = request(base_url, "/api/knowledge-bases?limit=1&offset=0")
        default_id = page["default_knowledge_base_id"]
        assert len(page["items"]) <= 1
        for label in ("示例产品资料", "示例部署资料", "隔离测试资料"):
            kb = request(base_url, "/api/knowledge-bases", "POST", {"name": label + " · " + suffix})
            created.append(kb["id"])
        product_id, deployment_id, excluded_id = created
        for number in range(8):
            empty = request(
                base_url,
                "/api/knowledge-bases",
                "POST",
                {"name": f"空库验收 {number + 1} · {suffix}"},
            )
            created.append(empty["id"])
        selected_ids = [product_id, deployment_id, *created[3:]]
        assert len(selected_ids) == 10
        product = upload(
            base_url, product_id, "product.md", "# 产品资料\n\n青禾平台当前产品版本为 3.2。"
        )
        upload(
            base_url,
            deployment_id,
            "deployment.md",
            "# 部署资料\n\n青禾平台部署最低内存要求为 8 GB。",
        )
        upload(base_url, excluded_id, "excluded.md", "# 其他资料\n\n隔离测试标记为 orange-9876。")
        docs = request(base_url, f"/api/knowledge-bases/{product_id}/documents?limit=1&offset=0")
        assert docs["total"] == 1 and docs["items"][0]["status"] == "ready"
        detail = request(base_url, f"/api/knowledge-bases/{product_id}")
        assert detail["document_count"] == 1 and detail["chunk_count"] > 0
        renamed = request(
            base_url,
            f"/api/knowledge-bases/{product_id}",
            "PATCH",
            {"name": "示例产品知识库 · " + suffix, "description": "多知识库验收的合成资料"},
        )
        assert renamed["id"] == product_id and renamed["chunk_count"] == detail["chunk_count"]
        temporary = upload(
            base_url, product_id, "temporary.md", "# 临时资料\n\n临时删除验收标记 violet-321。"
        )
        request(
            base_url,
            f"/api/knowledge-bases/{product_id}/documents/{temporary['document_id']}",
            "DELETE",
        )
        after_delete = request(base_url, f"/api/knowledge-bases/{product_id}/documents")
        assert after_delete["total"] == 1
        assert after_delete["items"][0]["document_id"] == product["document_id"]
        for scope in ([product_id], [deployment_id], [product_id, deployment_id]):
            chunks = request(
                base_url,
                "/api/retrieve",
                "POST",
                {
                    "question": "青禾平台产品版本和部署内存",
                    "knowledge_base_ids": scope,
                    "top_k": 6,
                },
            )
            assert {item["knowledge_base_id"] for item in chunks} == set(scope)
            assert all(item["knowledge_base_name"] for item in chunks)
            assert all(item["document_id"] != temporary["document_id"] for item in chunks)
        many = request(
            base_url,
            "/api/retrieve",
            "POST",
            {"question": "版本和内存", "knowledge_base_ids": selected_ids, "top_k": 6},
        )
        assert {item["knowledge_base_id"] for item in many} == {product_id, deployment_id}
        expect_error(
            base_url, "/api/retrieve", 422, data={"question": "版本", "knowledge_base_ids": []}
        )
        expect_error(
            base_url, "/api/retrieve", 422, data={"question": "版本", "tenant_id": "default"}
        )
        expect_error(base_url, f"/api/knowledge-bases/{default_id}", 409, "DELETE")
        expect_error(
            base_url,
            f"/api/knowledge-bases/{deployment_id}/documents/{product['document_id']}",
            404,
            "DELETE",
        )
        events = stream_answer(base_url, selected_ids, {product_id, deployment_id})
        request(base_url, f"/api/knowledge-bases/{excluded_id}", "DELETE")
        created.remove(excluded_id)
        remaining = request(
            base_url,
            "/api/retrieve",
            "POST",
            {
                "question": "版本",
                "knowledge_base_ids": [product_id, deployment_id],
                "top_k": 6,
            },
        )
        assert {item["knowledge_base_id"] for item in remaining} == {product_id, deployment_id}
        if keep:
            kept.update((product_id, deployment_id))
        return {
            "result": "LIVE_MULTI_KB_OK",
            "events": events,
            "selected_knowledge_base_count": len(selected_ids),
            "kept_knowledge_base_ids": sorted(kept),
        }
    finally:
        original_error = sys.exception()
        cleanup_errors = []
        for knowledge_base_id in created:
            if knowledge_base_id not in kept:
                try:
                    request(base_url, f"/api/knowledge-bases/{knowledge_base_id}", "DELETE")
                except (URLError, OSError, ValueError) as error:
                    # 保留原始验收错误，同时列出本脚本残留的库，方便手动清理或再次删除。
                    print(
                        f"Cleanup incomplete for test library {knowledge_base_id}: {error}",
                        file=sys.stderr,
                    )
                    cleanup_errors.append(knowledge_base_id)
        if cleanup_errors and original_error is None:
            raise RuntimeError(f"验收资料清理未完成：{cleanup_errors}")


# 用途：读取命令行选项并打印真实验收摘要。
# 参数：无；地址和保留示例库选项来自 CLI。返回：无，验收失败以非零状态退出。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--keep", action="store_true", help="保留两份合成示例知识库")
    args = parser.parse_args()
    print(json.dumps(run(args.base_url.rstrip("/"), args.keep), ensure_ascii=False))


if __name__ == "__main__":
    main()
