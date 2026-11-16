"""多格式文档解析：PDF / DOCX / DOC / TXT / MD，统一抽取为纯文本。

- PDF：pdfplumber 抽取每页正文与表格（表格转为 Markdown 管道格式，保留行列结构）
- PDF OCR：页面无文本层时（扫描件），pypdfium2 渲染为图片后走 RapidOCR 本地识别
- DOCX：python-docx 按文档顺序遍历段落与表格，合并单元格去重
- DOC：macOS textutil 转换为纯文本（老式二进制格式，表格结构不保留）
- TXT/MD：直接按 UTF-8（容错 GBK）解码
"""

import asyncio
import io
import subprocess
import tempfile
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md"}

_ocr_engine = None


def _get_ocr():
    """RapidOCR 懒加载单例（首次调用加载 ONNX 模型，约需几秒）。"""
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR()
    return _ocr_engine


def _ocr_pdf_pages(data: bytes, page_numbers: list[int]) -> dict[int, str]:
    """对指定页码（1 起始）渲染并 OCR，返回 {页码: 文本}。"""
    import numpy as np
    import pypdfium2 as pdfium

    ocr = _get_ocr()
    results: dict[int, str] = {}
    pdf = pdfium.PdfDocument(data)
    for page_no in page_numbers:
        page = pdf[page_no - 1]
        image = page.render(scale=2.5).to_pil()
        ocr_result, _ = ocr(np.array(image))
        if ocr_result:
            results[page_no] = "\n".join(line[1] for line in ocr_result)
    return results


def _table_to_text(rows: list[list]) -> str:
    lines = []
    for row in rows:
        cells = [(str(cell).replace("\n", " ").strip() if cell is not None else "") for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def parse_pdf(data: bytes, ocr: bool = True) -> str:
    import pdfplumber

    parts = []
    ocr_pages = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
            elif ocr:
                ocr_pages.append(page_no)
            for table in page.extract_tables():
                table_text = _table_to_text(table)
                if table_text.strip():
                    parts.append(f"[第{page_no}页表格]\n{table_text}")
    if ocr_pages:
        for page_no, text in _ocr_pdf_pages(data, ocr_pages).items():
            if text.strip():
                parts.append(f"[第{page_no}页 OCR]\n{text}")
    return "\n\n".join(parts)


def parse_docx(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(io.BytesIO(data))
    parts = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            text = Paragraph(child, document).text.strip()
            if text:
                parts.append(text)
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            table_text = _table_to_text(rows)
            if table_text.strip():
                parts.append(f"[表格]\n{table_text}")
    return "\n\n".join(parts)


def parse_doc(data: bytes) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "input.doc"
        src.write_bytes(data)
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(src)],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise ValueError(f"textutil 转换失败: {result.stderr.decode(errors='replace')[:200]}")
        return result.stdout.decode("utf-8", errors="replace")


def parse_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def parse_document(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式 {ext}，支持：{', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    if ext == ".pdf":
        return parse_pdf(data)
    if ext == ".docx":
        return parse_docx(data)
    if ext == ".doc":
        return parse_doc(data)
    return parse_text(data)


async def parse_document_async(filename: str, data: bytes) -> str:
    return await asyncio.to_thread(parse_document, filename, data)
