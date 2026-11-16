# RAG Demo (FastAPI + 火山方舟)

最小可运行的 RAG 知识库 demo：FastAPI 提供接口，火山方舟（Ark）提供 Embedding 与对话模型，向量存储默认使用 **Milvus Lite**（嵌入式、持久化、无需 Docker），可通过环境变量切换回内存 numpy 实现。

## 结构

- `app/main.py` — FastAPI 入口：`/ingest` 写入（支持切分）、`/query` RAG 问答、`/retrieve` 纯检索、`/documents` 列表/删除、`/stats`、`/healthz`
- `app/rag.py` — 内存向量库 + RAG 编排
- `app/milvus_store.py` — Milvus 向量存储（COSINE 索引、AUTOINDEX、持久化）
- `app/chunking.py` — 文档切分（句子边界优先 + 重叠）
- `app/parsers.py` — 多格式文档解析（PDF 正文+表格 / DOCX 段落+表格 / DOC / TXT / MD）
- `app/tasks.py` — 导入任务管理（异步执行、状态跟踪、取消）
- `app/ark_client.py` — Ark API 客户端（OpenAI 兼容协议）
- `seed_docs.py` — 写入示例文档
- `benchmark.py` — 高并发压测（QPS / P99 / 错误率）

## 启动

一键启动（后端 8000 + 前端 5173，自动检查依赖，Ctrl+C 同时停止）：

```bash
./start.sh
```

停止服务：`./start.sh stop`。若启动时报端口占用，说明服务已在运行，可先 stop 再启动。

或分别手动启动：

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

配置在 `.env`（`ARK_API_KEY`、`ARK_BASE_URL`、模型名）。

向量存储配置：

- `VECTOR_BACKEND=milvus`（默认）/ `memory`（内存 numpy，重启丢失）
- `RAG_MILVUS_URI=./milvus_demo.db`（Milvus Lite 本地文件；也可填 `http://localhost:19530` 连接独立 Milvus 服务。注意不要用 `MILVUS_URI` 变量名，会与 pymilvus 内置配置冲突）
- `MILVUS_COLLECTION=rag_docs`（集合名，首次写入时按向量维度自动建表）

## 前端（React + Ant Design）

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173，/api 代理到 8000 端口后端
```

三个页面：智能问答（答案 + 引用来源 + 耗时）、文档管理（批量写入 + 可选切分 + 文档列表/删除 + 统计）、检索测试（相似度表格）。`npm run build` 产出静态文件。

## 文档管理 API

```bash
# 列表（分页）
curl "localhost:8000/documents?limit=100&offset=0"
# 删除
curl -X DELETE localhost:8000/documents/1
# 切分写入（长文自动按句子边界切成 200 字片段，重叠 40 字）
curl -X POST localhost:8000/ingest -H 'Content-Type: application/json' \
  -d '{"texts":["长文档……"],"chunk":true,"chunk_size":200,"chunk_overlap":40}'
```

## 文件导入

```bash
# 创建异步导入任务（立即返回任务 ID）
curl -X POST localhost:8000/imports \
  -F "file=@report.pdf" -F "chunk=true" -F "chunk_size=500" -F "chunk_overlap=50"
# 任务列表 / 状态查询 / 取消
curl localhost:8000/imports
curl localhost:8000/imports/<task_id>
curl -X POST localhost:8000/imports/<task_id>/cancel
```

- 任务状态：pending / processing / completed / failed / cancelled，阶段：parsing / embedding / done
- PDF：pdfplumber 逐页抽取正文 + 表格（表格转 Markdown 管道格式）
- 扫描件 PDF：无文本层的页面自动用 pypdfium2 渲染为图片，经 RapidOCR 本地识别（ONNX，中英文，无需外部服务）
- DOCX：按文档顺序遍历段落与表格，保留表格行列结构
- DOC：macOS textutil 转换（不保留表格结构）
- 文件上限 50MB；OCR 首次调用需加载模型（约几秒）
- 前端文档管理页支持点击/拖拽上传，导入任务列表每 2 秒自动轮询，进行中任务可取消
- 旧接口 `POST /ingest/file`（同步导入）保留可用

## 快速体验

```bash
python seed_docs.py
curl -X POST localhost:8000/query -H 'Content-Type: application/json' -d '{"question":"什么是RAG？"}'
```

## 压测

```bash
# 框架自身极限（无外部调用）
python benchmark.py --endpoint /healthz --concurrency 200 --duration 15
# 检索接口（命中查询向量缓存）
python benchmark.py --endpoint /retrieve --concurrency 50 --duration 10
# 检索接口（--unique 绕过缓存，直连 Ark embedding，受其 RPM 限制）
python benchmark.py --endpoint /retrieve --concurrency 5 --duration 15 --unique
# 恒定速率
python benchmark.py --endpoint /healthz --mode rps --rps 200 --duration 15
```

注意：`/query` 会调用 LLM，压测会消耗 token 且延迟在秒级，建议小并发。

## 实测数据（M 系列 Mac，本机回环 + Ark 北京）

| 场景 | 并发 | QPS | P99 延迟 | 错误率 |
| --- | --- | --- | --- | --- |
| /healthz（框架极限） | 200 | 685 | 1346ms | 0% |
| /retrieve（缓存命中） | 50 | 669 | 473ms | 0% |
| /retrieve（无缓存，直连 Ark embedding） | 5 | 6.0 | 3962ms | 8.3%（Ark 429 限流） |
| /query（完整 RAG，embedding 缓存 + LLM） | 3 | 2.3 | 6466ms | 0% |

关键结论：

- 瓶颈不在 FastAPI（单机数百 QPS 无压力），而在远程模型调用：Ark coding plan 的 embedding 有较低 RPM 限制，持续高速请求会返回 429。
- 已内置 429 指数退避重试（`ark_client.py`）和查询向量 LRU 缓存（`rag.py`，`EMBED_CACHE_SIZE` 可调），重复问题直接命中缓存。
- 生产建议：embedding 批量/缓存、请求队列 + 限流（信号量控制对 Ark 的并发）、多 worker（`uvicorn --workers N`）。

## 后续演进

- 向量库：内存 numpy → pgvector / Milvus / OpenSearch
- 文档切分：chunking + overlap，元数据过滤
- 缓存：embedding 缓存、答案缓存（Redis）
- 并发：对 Ark 的出站限流与降级、多 worker 部署
