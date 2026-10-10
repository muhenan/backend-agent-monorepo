import re


def split_markdown(text: str, chunk_size: int = 1100, overlap: int = 150) -> list[str]:
    """Split exported Markdown near paragraph boundaries, then window long paragraphs."""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Require chunk_size > overlap >= 0")

    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not normalized:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    chunks: list[str] = []
    current = ""

    def add_long_paragraph(paragraph: str) -> None:
        start = 0
        while start < len(paragraph):
            end = min(start + chunk_size, len(paragraph))
            if end < len(paragraph):
                boundary = max(paragraph.rfind(" ", start, end), paragraph.rfind("。", start, end))
                if boundary > start + chunk_size // 2:
                    end = boundary + 1
            piece = paragraph[start:end].strip()
            if piece:
                chunks.append(piece)
            if end == len(paragraph):
                break
            start = max(start + 1, end - overlap)

    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            add_long_paragraph(paragraph)
            continue

        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            tail = current[-overlap:].strip() if overlap and current else ""
            current = f"{tail}\n\n{paragraph}" if tail else paragraph

    if current:
        chunks.append(current)
    return chunks
