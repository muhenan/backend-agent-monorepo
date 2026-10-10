import pytest

from henan_rag.chunking import split_markdown


def test_split_markdown_keeps_short_paragraphs_together() -> None:
    chunks = split_markdown(
        "# Title\n\nFirst paragraph.\n\nSecond paragraph.", chunk_size=100, overlap=20
    )

    assert len(chunks) == 1
    assert "First paragraph." in chunks[0]
    assert "Second paragraph." in chunks[0]


def test_split_markdown_splits_long_paragraphs_with_overlap() -> None:
    text = "RAG 检索证据。" * 80
    chunks = split_markdown(text, chunk_size=80, overlap=15)

    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert "RAG 检索证据。" in "".join(chunks)


def test_split_markdown_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        split_markdown("text", chunk_size=10, overlap=10)


def test_split_markdown_returns_no_chunks_for_blank_input() -> None:
    assert split_markdown(" \n\n ") == []
