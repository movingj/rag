"""写入几条示例文档，方便快速体验。"""

import asyncio

import httpx

DOCS = [
    "FastAPI 是一个现代、高性能的 Python Web 框架，基于 Starlette 和 Pydantic，原生支持异步。",
    "RAG（检索增强生成）先通过向量检索找到相关文档，再把文档作为上下文交给大模型生成答案。",
    "火山方舟（Volcengine Ark）是字节跳动的大模型服务平台，提供豆包系列模型的 API，兼容 OpenAI 接口格式。",
    "向量检索常用余弦相似度衡量查询与文档的语义相近程度。",
    "Locust 和 wrk 是常见的 HTTP 压测工具；本 demo 用 asyncio + httpx 自实现了一个轻量压测脚本。",
]


async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=60) as client:
        resp = await client.post("/ingest", json={"texts": DOCS})
        print(resp.status_code, resp.json())


if __name__ == "__main__":
    asyncio.run(main())
