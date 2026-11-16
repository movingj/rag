"""Milvus 向量存储（默认 Milvus Lite 嵌入式，数据持久化到本地文件）。

通过 RAG_MILVUS_URI 可切换到独立部署的 Milvus 服务（如 http://localhost:19530）。
注意：不能使用 MILVUS_URI 作为变量名，pymilvus 在 import 时会解析它并拒绝本地文件路径。
"""

import asyncio
import os

from pymilvus import DataType, MilvusClient

from . import ark_client


class MilvusStore:
    def __init__(self):
        uri = os.getenv("RAG_MILVUS_URI", "./milvus_demo.db")
        self._collection = os.getenv("MILVUS_COLLECTION", "rag_docs")
        self._client = MilvusClient(uri=uri)

    def _exists(self) -> bool:
        return self._client.has_collection(self._collection)

    def _ensure_loaded(self):
        if self._exists():
            self._client.load_collection(self._collection)

    def _create_collection(self, dim: int):
        schema = self._client.create_schema(auto_id=True, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field("text", DataType.VARCHAR, max_length=65535)
        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        self._client.create_collection(
            self._collection, schema=schema, index_params=index_params
        )

    async def add(self, texts: list[str], metadatas: list[dict] | None = None) -> list[str]:
        embeddings = await ark_client.embed(texts)

        def _insert():
            if not self._exists():
                self._create_collection(len(embeddings[0]))
            rows = [{"vector": vec, "text": text} for vec, text in zip(embeddings, texts)]
            return self._client.insert(self._collection, rows)

        result = await asyncio.to_thread(_insert)
        return [str(doc_id) for doc_id in result["ids"]]

    async def search(self, query: str, top_k: int = 3) -> list[dict]:
        if not self._exists():
            return []
        await asyncio.to_thread(self._ensure_loaded)
        from .rag import get_query_vector

        query_vec = await get_query_vector(query)

        def _search():
            return self._client.search(
                self._collection,
                data=[query_vec],
                limit=top_k,
                output_fields=["text"],
                search_params={"metric_type": "COSINE"},
            )

        results = await asyncio.to_thread(_search)
        if not results:
            return []
        return [
            {
                "id": str(hit["id"]),
                "text": hit["entity"]["text"],
                "score": float(hit["distance"]),
            }
            for hit in results[0]
        ]

    def count(self) -> int:
        if not self._exists():
            return 0
        self._client.load_collection(self._collection)
        result = self._client.query(self._collection, filter="", output_fields=["count(*)"])
        return result[0]["count(*)"] if result else 0

    async def list_documents(self, limit: int = 100, offset: int = 0) -> list[dict]:
        if not self._exists():
            return []
        await asyncio.to_thread(self._ensure_loaded)

        def _query():
            return self._client.query(
                self._collection,
                filter="",
                output_fields=["id", "text"],
                limit=limit,
                offset=offset,
            )

        rows = await asyncio.to_thread(_query)
        return [{"id": str(row["id"]), "text": row["text"]} for row in rows]

    async def delete(self, doc_id: str) -> bool:
        if not self._exists():
            return False
        await asyncio.to_thread(self._ensure_loaded)

        def _delete():
            existing = self._client.query(
                self._collection, filter=f"id == {int(doc_id)}", output_fields=["id"]
            )
            if not existing:
                return False
            self._client.delete(self._collection, ids=[int(doc_id)])
            return True

        return await asyncio.to_thread(_delete)
