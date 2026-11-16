import asyncio
import os
import time
import uuid

import numpy as np

from . import ark_client

EMBED_CACHE_SIZE = int(os.getenv("EMBED_CACHE_SIZE", "10000"))


class EmbeddingCache:
    """LRU 缓存查询向量，避免重复问题每次都请求远程 embedding。"""

    def __init__(self, maxsize: int = 10000):
        self._cache: dict[str, list[float]] = {}
        self._order: list[str] = []
        self._maxsize = maxsize
        self._lock = asyncio.Lock()

    async def get_or_embed(self, text: str) -> list[float]:
        async with self._lock:
            if text in self._cache:
                return self._cache[text]
        (vec,) = await ark_client.embed([text])
        async with self._lock:
            if len(self._order) >= self._maxsize:
                oldest = self._order.pop(0)
                self._cache.pop(oldest, None)
            self._cache[text] = vec
            self._order.append(text)
        return vec


_query_cache = EmbeddingCache(EMBED_CACHE_SIZE)


async def get_query_vector(query: str) -> list[float]:
    return await _query_cache.get_or_embed(query)


class Document:
    __slots__ = ("id", "text", "metadata", "created_at")

    def __init__(self, text: str, metadata: dict | None = None):
        self.id = uuid.uuid4().hex
        self.text = text
        self.metadata = metadata or {}
        self.created_at = time.time()


class VectorStore:
    def __init__(self):
        self._docs: list[Document] = []
        self._vectors: np.ndarray | None = None
        self._lock = asyncio.Lock()

    async def add(self, texts: list[str], metadatas: list[dict] | None = None) -> list[str]:
        embeddings = await ark_client.embed(texts)
        metadatas = metadatas or [{} for _ in texts]
        async with self._lock:
            ids = []
            for text, meta in zip(texts, metadatas):
                doc = Document(text, meta)
                self._docs.append(doc)
                ids.append(doc.id)
            block = np.array(embeddings, dtype=np.float32)
            self._vectors = block if self._vectors is None else np.vstack([self._vectors, block])
        return ids

    async def search(self, query: str, top_k: int = 3) -> list[dict]:
        if self._vectors is None or len(self._docs) == 0:
            return []
        query_vec = await get_query_vector(query)
        q = np.array(query_vec, dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-12)
        v = self._vectors / (np.linalg.norm(self._vectors, axis=1, keepdims=True) + 1e-12)
        scores = v @ q
        top_idx = np.argsort(scores)[::-1][:top_k]
        return [
            {"id": self._docs[i].id, "text": self._docs[i].text, "score": float(scores[i])}
            for i in top_idx
        ]

    def count(self) -> int:
        return len(self._docs)

    async def list_documents(self, limit: int = 100, offset: int = 0) -> list[dict]:
        return [{"id": doc.id, "text": doc.text} for doc in self._docs[offset : offset + limit]]

    async def delete(self, doc_id: str) -> bool:
        async with self._lock:
            for index, doc in enumerate(self._docs):
                if doc.id == doc_id:
                    self._docs.pop(index)
                    self._vectors = np.delete(self._vectors, index, axis=0)
                    return True
        return False


PROMPT_TEMPLATE = """你是一个知识库问答助手。请仅根据下面的参考资料回答用户问题，如果资料中没有相关信息，请明确说明"知识库中未找到相关信息"。

参考资料：
{context}

用户问题：{question}
回答："""


async def answer(store: VectorStore, question: str, top_k: int = 3) -> dict:
    hits = await store.search(question, top_k=top_k)
    if not hits:
        return {"answer": "知识库为空，请先调用 /ingest 写入文档。", "sources": []}
    context = "\n\n".join(f"[{i + 1}] {h['text']}" for i, h in enumerate(hits))
    reply = await ark_client.chat(
        [{"role": "user", "content": PROMPT_TEMPLATE.format(context=context, question=question)}]
    )
    return {"answer": reply, "sources": hits}
