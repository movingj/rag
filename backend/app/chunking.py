"""轻量文档切分：优先按段落/句子边界切，超长片段再按字符硬切，支持重叠。"""

import re

SENTENCE_SEP = re.compile(r"(?<=[。！？；.!?;\n])")


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    pieces = [p for p in SENTENCE_SEP.split(text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if len(piece) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(piece), chunk_size - overlap):
                segment = piece[i : i + chunk_size].strip()
                if segment:
                    chunks.append(segment)
            continue
        if len(current) + len(piece) > chunk_size and current:
            chunks.append(current.strip())
            current = current[-overlap:] if overlap > 0 else ""
        current += piece
    if current.strip():
        chunks.append(current.strip())
    return chunks
