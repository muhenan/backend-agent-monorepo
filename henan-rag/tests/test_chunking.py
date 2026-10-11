import pytest

from henan_rag.chunking import split_markdown


# 用途：验证总字符数未超过目标大小时，多个短段落能够合并到同一个文本块。
# 参数：无；测试内构造 Markdown，并指定 chunk_size=100 字符、overlap=20 字符。
# 返回：None；合并结果不符合预期时，断言会让测试失败。
def test_split_markdown_keeps_short_paragraphs_together() -> None:
    chunks = split_markdown(
        "# Title\n\nFirst paragraph.\n\nSecond paragraph.", chunk_size=100, overlap=20
    )

    assert len(chunks) == 1
    assert "First paragraph." in chunks[0]
    assert "Second paragraph." in chunks[0]


# 用途：验证超长段落会被分成多个块，各块满足窗口大小，并保留原文中的测试句子。
# 参数：无；测试内构造长文本，并指定 chunk_size=80 字符、overlap=15 字符。
# 返回：None；切分结果不符合预期时，断言会让测试失败。
def test_split_markdown_splits_long_paragraphs_with_overlap() -> None:
    text = "RAG 检索证据。" * 80
    chunks = split_markdown(text, chunk_size=80, overlap=15)

    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert "RAG 检索证据。" in "".join(chunks)


# 用途：验证重叠字符数等于块大小时，会拒绝无效参数并抛出 ValueError。
# 参数：无；测试内指定 chunk_size=10 字符、overlap=10 字符。
# 返回：None；未抛出预期异常时，pytest 会让测试失败。
def test_split_markdown_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        split_markdown("text", chunk_size=10, overlap=10)


# 用途：验证只包含空格和换行的文本不会生成文本块。
# 参数：无；测试内传入空白字符串，使用函数默认的块大小和重叠字符数。
# 返回：None；结果不是空列表时，断言会让测试失败。
def test_split_markdown_returns_no_chunks_for_blank_input() -> None:
    assert split_markdown(" \n\n ") == []
