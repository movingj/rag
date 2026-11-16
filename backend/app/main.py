import json
import os
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import ark_client
from .chunking import chunk_text
from .parsers import parse_document_async
from .rag import VectorStore, answer
from .tasks import import_manager


def create_store():
    backend = os.getenv("VECTOR_BACKEND", "milvus").lower()
    if backend == "milvus":
        from .milvus_store import MilvusStore

        return MilvusStore()
    return VectorStore()


store = create_store()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if ark_client._chat_client is not None:
        await ark_client._chat_client.aclose()
    if ark_client._embedding_client is not None:
        await ark_client._embedding_client.aclose()


app = FastAPI(title="RAG Demo (Volcengine Ark)", lifespan=lifespan)


class IngestRequest(BaseModel):
    texts: list[str] = Field(min_length=1)
    metadatas: list[dict] | None = None
    chunk: bool = False
    chunk_size: int = Field(default=500, ge=50, le=8000)
    chunk_overlap: int = Field(default=50, ge=0, le=1000)


class QueryRequest(BaseModel):
    question: str
    top_k: int = 3


@app.post("/ingest")
async def ingest(req: IngestRequest):
    texts = req.texts
    if req.chunk:
        if req.chunk_overlap >= req.chunk_size:
            raise HTTPException(status_code=422, detail="chunk_overlap 必须小于 chunk_size")
        texts = [
            piece
            for text in req.texts
            for piece in chunk_text(text, req.chunk_size, req.chunk_overlap)
        ]
        if not texts:
            raise HTTPException(status_code=422, detail="切分后无有效内容")
    try:
        ids = await store.add(texts, req.metadatas if not req.chunk else None)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"embedding failed: {exc}")
    return {"ids": ids, "chunks": len(ids), "total_docs": store.count()}


@app.post("/ingest/file")
async def ingest_file(
    file: UploadFile = File(...),
    chunk: bool = Form(True),
    chunk_size: int = Form(500),
    chunk_overlap: int = Form(50),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="文件为空")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 50MB 限制")
    try:
        text = await parse_document_async(file.filename or "unknown", data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"文档解析失败: {exc}")
    if not text.strip():
        raise HTTPException(status_code=422, detail="未从文档中解析出文本内容（OCR 也未识别到文字，请检查文件质量）")

    texts = [text]
    if chunk:
        if chunk_overlap >= chunk_size:
            raise HTTPException(status_code=422, detail="chunk_overlap 必须小于 chunk_size")
        texts = chunk_text(text, chunk_size, chunk_overlap)
    try:
        ids = await store.add(texts)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"embedding failed: {exc}")
    return {
        "filename": file.filename,
        "parsed_chars": len(text),
        "chunks": len(ids),
        "ids": ids,
        "total_docs": store.count(),
    }


async def _run_import(task_id: str, data: bytes, filename: str, chunk: bool, chunk_size: int, chunk_overlap: int):
    task = import_manager.get(task_id)
    task.stage = "parsing"
    text = await parse_document_async(filename, data)
    if not text.strip():
        raise ValueError("未从文档中解析出文本内容（OCR 也未识别到文字，请检查文件质量）")
    task.parsed_chars = len(text)

    texts = [text]
    if chunk:
        texts = chunk_text(text, chunk_size, chunk_overlap)

    task.stage = "embedding"
    ids = await store.add(texts)
    task.stage = "done"
    task.chunks = len(ids)


@app.post("/imports")
async def create_import(
    file: UploadFile = File(...),
    chunk: bool = Form(True),
    chunk_size: int = Form(500),
    chunk_overlap: int = Form(50),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="文件为空")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 50MB 限制")
    if chunk and chunk_overlap >= chunk_size:
        raise HTTPException(status_code=422, detail="chunk_overlap 必须小于 chunk_size")

    task = import_manager.create(file.filename or "unknown", len(data))
    import_manager.start(
        task.id,
        lambda tid: _run_import(tid, data, file.filename or "unknown", chunk, chunk_size, chunk_overlap),
    )
    return task.to_dict()


@app.get("/imports")
async def list_imports():
    return {"tasks": import_manager.list()}


@app.get("/imports/{task_id}")
async def get_import(task_id: str):
    task = import_manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task.to_dict()


@app.post("/imports/{task_id}/cancel")
async def cancel_import(task_id: str):
    result = import_manager.cancel(task_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="任务不存在")
    if result == "not_cancellable":
        raise HTTPException(status_code=409, detail="任务已结束，无法取消")
    return {"cancelled": task_id}


@app.get("/documents")
async def documents(limit: int = 100, offset: int = 0):
    return {"documents": await store.list_documents(limit=limit, offset=offset), "total": store.count()}


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    deleted = await store.delete(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    return {"deleted": doc_id, "total_docs": store.count()}


@app.post("/query")
async def query(req: QueryRequest):
    try:
        return await answer(store, req.question, req.top_k)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"query failed: {exc}")


@app.get("/retrieve")
async def retrieve(q: str, top_k: int = 3):
    try:
        return {"hits": await store.search(q, top_k=top_k)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"retrieve failed: {exc}")


@app.get("/stats")
async def stats():
    return {"documents": store.count()}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------------- #
#  大模型 API 配置读写（config.json）
# ---------------------------------------------------------------------- #
class ApiSection(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    rpm: float | None = None
    tpm: float | None = None


class ApiConfigPayload(BaseModel):
    chat: ApiSection
    embedding: ApiSection


def _mask_key(key: str) -> str:
    """API Key 掩码显示：前 4 + **** + 后 4，避免明文暴露。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}****{key[-4:]}"


def _read_config_file() -> dict:
    try:
        return json.loads(ark_client._CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _config_view(raw: dict) -> dict:
    def section(name: str) -> dict:
        sec = raw.get(name, {}) if isinstance(raw, dict) else {}
        return {
            "base_url": sec.get("base_url", ""),
            "api_key": _mask_key(sec.get("api_key", "")),
            "model": sec.get("model", ""),
            "rpm": sec.get("rpm"),
            "tpm": sec.get("tpm"),
        }

    return {"chat": section("chat"), "embedding": section("embedding")}


def _clean_num(v: float | None) -> float | int | None:
    """整数值的浮点数转为 int，让 config.json 更干净（3.0 → 3）。"""
    if v is None:
        return None
    return int(v) if float(v).is_integer() else v


@app.get("/config")
async def get_config():
    """读取当前大模型 API 配置（api_key 以掩码返回）。"""
    return _config_view(_read_config_file())


@app.put("/config")
async def update_config(payload: ApiConfigPayload):
    """保存大模型 API 配置到 config.json 并热重载，无需重启服务。

    api_key 留空或回传掩码（含 ****）时沿用原值，便于前端掩码展示后直接保存。
    """
    raw = _read_config_file()

    def merge(name: str, new: ApiSection, old: dict) -> dict:
        new_key = (new.api_key or "").strip()
        if not new_key or "****" in new_key:
            new_key = old.get("api_key", "")
        sec = {
            "base_url": (new.base_url or "").strip(),
            "api_key": new_key,
            "model": (new.model or "").strip(),
        }
        if new.rpm is not None:
            sec["rpm"] = _clean_num(new.rpm)
        if new.tpm is not None:
            sec["tpm"] = _clean_num(new.tpm)
        return sec

    merged = {
        "chat": merge("chat", payload.chat, raw.get("chat", {}) if isinstance(raw, dict) else {}),
        "embedding": merge("embedding", payload.embedding, raw.get("embedding", {}) if isinstance(raw, dict) else {}),
    }

    ark_client._CONFIG_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    await ark_client.reload_config()
    return {"status": "saved", "config": _config_view(merged)}


class ConfigTestPayload(BaseModel):
    section: str  # "chat" | "embedding"
    base_url: str
    api_key: str = ""  # 留空或掩码（含****）时沿用 config.json 中的原 key
    model: str


@app.post("/config/test")
async def test_config(payload: ConfigTestPayload):
    """对表单当前填写的配置发一个最小真实请求，验证连通性。

    - chat：调用 /chat/completions（小 max_tokens 控制开销）
    - embedding：调用 /embeddings（单条短文本）
    api_key 掩码/留空时自动沿用磁盘原 key，无需重新输入即可测试。
    """
    if payload.section not in ("chat", "embedding"):
        raise HTTPException(status_code=422, detail="section 仅支持 chat / embedding")

    base_url = (payload.base_url or "").strip()
    model = (payload.model or "").strip()
    if not base_url or not model:
        raise HTTPException(status_code=422, detail="base_url 和 model 不能为空")

    api_key = (payload.api_key or "").strip()
    if not api_key or "****" in api_key:
        raw = _read_config_file()
        old = raw.get(payload.section, {}) if isinstance(raw, dict) else {}
        api_key = old.get("api_key", "")

    if payload.section == "chat":
        path = "/chat/completions"
        body = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 16}
    else:
        path = "/embeddings"
        body = {"model": model, "input": ["连接测试"]}

    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=httpx.Timeout(30.0),
        ) as client:
            resp = await client.post(path, json=body)
    except httpx.HTTPError as exc:
        return {"ok": False, "status_code": None, "message": f"网络错误：{type(exc).__name__}: {str(exc)[:200]}"}

    if resp.status_code < 400:
        return {"ok": True, "status_code": resp.status_code, "message": "连接成功，服务可用"}
    return {"ok": False, "status_code": resp.status_code, "message": f"HTTP {resp.status_code}: {resp.text[:280]}"}
