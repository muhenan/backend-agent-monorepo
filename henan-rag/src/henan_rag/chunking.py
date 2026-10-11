import re


# 用途：优先按 Markdown 段落合并文本，遇到超长段落时使用带重叠的滑动窗口切分。
# 参数 text：解析器导出的 Markdown 原文；空白内容会返回空列表。
# 参数 chunk_size：每块的目标字符数，必须大于 0；这里计算字符数，不是 token 数。
# 参数 overlap：相邻块保留的重叠字符数，必须满足 0 <= overlap < chunk_size。
# 返回：按原文顺序排列的文本块列表；保留上一块尾部后再拼接完整段落时，可能超过目标大小。
# 异常：切块大小或重叠范围不合法时抛出 ValueError；不会修改传入的原文。
def split_markdown(text: str, chunk_size: int = 1100, overlap: int = 150) -> list[str]:
    """按段落边界切分 Markdown，并为超长段落保留窗口间的重叠内容。"""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Require chunk_size > overlap >= 0")

    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not normalized:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    chunks: list[str] = []
    current = ""

    # 用途：把一个超长段落切成多个片段，尽量在空格或中文句号附近结束片段。
    # 参数 paragraph：需要继续切分的单个段落文本。
    # 闭包依赖：读取外层 chunk_size 和 overlap（均为字符数），向外层 chunks 列表追加片段。
    # 返回：None；切分结果通过修改外层 chunks 保存，不会修改 paragraph。
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
