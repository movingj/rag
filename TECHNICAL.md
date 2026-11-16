# RAG 知识库 Demo 技术文档

版本：v0.1.0 · 更新日期：2026-10-29

## 1. 项目概述

基于 **Python + FastAPI + 火山方舟（Volcengine Ark）+ Milvus** 的最小可运行 RAG（检索增强生成）知识库 Demo，并附带高并发压测工具与 React + Ant Design 图形化界面。

核心目标：

- 验证 RAG 全链路：文档写入 → 向量化 → 相似度检索 → LLM 生成答案
- 测量服务在高并发下的 QPS、延迟分位数与错误率，定位性能瓶颈
- 为后续正式知识库建设（向量库、文档切分、持久化）预留清晰的替换边界

## 2. 当前功能清单

### 2.1 知识库写入 `/ingest`

- 批量提交文本列表，自动调用 Ark Embedding 模型向量化
- 向量与原文存入 **Milvus**（默认 Milvus Lite 嵌入式，持久化到本地文件 `milvus_demo.db`，重启不丢失）
- 支持可选的 `metadatas` 元数据字段（预留，暂未参与过滤）
- 支持可选文档切分：`chunk=true` 时按句子边界切分（`chunk_size` 默认 500、`chunk_overlap` 默认 50），超长句子按字符硬切
- 返回文档 ID 列表、写入片段数与库中文档总数
- 写入过程使用异步锁保证并发安全

### 2.1.1 文档管理 `/documents`

- `GET /documents?limit&offset`：分页列出文档 ID 与原文，附总数
- `DELETE /documents/{id}`：按 ID 删除（先查询存在性，不存在返回 404），Milvus 与内存后端行为一致

### 2.1.2 文件导入 `/ingest/file`

- `multipart/form-data` 上传，支持 `.pdf` / `.docx` / `.doc` / `.txt` / `.md`，上限 50MB
- 表单参数 `chunk`（默认 true）、`chunk_size`、`chunk_overlap`，解析后统一走切分 + 向量化入库
- PDF：pdfplumber 逐页 `extract_text()` + `extract_tables()`，表格转为 `| 列 | 列 |` 结构化文本并标注页码
- PDF OCR：页面无文本层时（扫描件），pypdfium2 以 2.5 倍缩放渲染为图片，RapidOCR（ONNX 本地推理，模型随包内置，懒加载单例）识别为文本并标注页码
- DOCX：python-docx 按 body 元素顺序遍历段落与表格，表格保留行列结构
- DOC：macOS `textutil` 转纯文本（表格结构不保留）
- TXT/MD：UTF-8 解码，容错 GBK
- 解析在线程池执行（`asyncio.to_thread`），不阻塞事件循环；解析失败返回 422 及原因
- 返回文件名、解析字符数、写入片段数与库内总数
- 限制：OCR 不保留表格结构（纯文本行）；首次 OCR 需加载模型约几秒；手写体/低质量图片识别率有限

### 2.1.3 异步导入任务 `/imports`

- `POST /imports`：上传文件后立即返回任务 ID，后台异步执行 解析 → 向量化入库
- `GET /imports`：任务列表（按创建时间倒序）；`GET /imports/{id}`：单任务详情
- `POST /imports/{id}/cancel`：取消进行中任务（asyncio.Task.cancel 协作式取消），已结束任务返回 409
- 任务字段：状态（pending/processing/completed/failed/cancelled）、阶段（parsing/embedding/done）、解析字数、写入片段数、错误信息、起止时间
- 内存实现，任务记录重启清空；前端每 2 秒轮询活跃任务，全部结束后自动刷新文档列表与统计

### 2.2 RAG 问答 `/query`

- 用户提问 → 查询向量（优先命中缓存）→ 余弦相似度 Top-K 检索
- 将检索结果按编号拼接为上下文，注入固定 Prompt 模板
- 调用 Ark Chat 模型生成仅基于知识库内容的回答
- 知识库为空时直接返回提示，不调用 LLM（零成本短路）
- 返回回答文本 + 引用来源（文档 ID、原文、相似度分数）

### 2.3 纯检索 `/retrieve`

- 只走向量检索，不调用 LLM，适合调试检索质量与单独压测检索链路
- 支持 `top_k` 参数（默认 3）

### 2.4 运维接口

- `GET /stats`：返回库中文档数量
- `GET /healthz`：健康检查，无外部依赖，用于框架极限压测

### 2.5 性能与稳定性机制

- **429 指数退避重试**：Ark 返回限流时自动重试（最多 3 次，退避 0.5s→8s，优先遵循 `Retry-After` 响应头）
- **查询向量 LRU 缓存**：重复问题不再请求远程 Embedding，容量默认 10000，可用 `EMBED_CACHE_SIZE` 调整
- **共享异步 HTTP 客户端**：全局复用 `httpx.AsyncClient` 连接池，应用关闭时优雅释放
- **可切换向量后端**：`VECTOR_BACKEND=milvus`（默认，持久化）/ `memory`（内存 numpy），接口一致，业务代码零改动
- **Milvus 同步调用线程池化**：`asyncio.to_thread` 包裹阻塞调用，避免阻塞事件循环
- **统一异常处理**：上游调用失败返回 `502` 及原因，不暴露 500 堆栈

### 2.6 压测工具 `benchmark.py`

- 固定并发模式（closed-loop）与恒定速率模式（open-loop，`--mode rps`）
- `--unique` 参数为每个请求生成唯一问题，绕过缓存测量真实上游吞吐
- 输出：总请求数、QPS、错误率、平均/P50/P90/P95/P99/最大延迟、采样错误详情（最多 3 条去重）
- 内置预热阶段（默认 2s）与目标健康检查，预热数据不计入统计

## 3. 技术架构

```text
客户端 / benchmark.py
        │ HTTP (asyncio + httpx)
        ▼
┌─────────────────────────────────────┐
│  FastAPI (uvicorn, 单 event loop)    │
│  app/main.py（VECTOR_BACKEND 选择存储实现）│
│  ├── /ingest  ──────────┐           │
│  ├── /query   ─────┐    │           │
│  └── /retrieve ────┤    │           │
└────────────────────┼────┼───────────┘
                     ▼    ▼
        app/rag.py（RAG 编排 / EmbeddingCache / Prompt）
                     │
        ┌────────────┴─────────────┐
        ▼                          ▼
 app/milvus_store.py         app/ark_client.py
 MilvusStore                 （共享 AsyncClient + 429 重试）
 ├── Milvus Lite 嵌入式             │ HTTPS
 ├── AUTOINDEX + COSINE             ▼
 └── 持久化 ./milvus_demo.db  火山方舟 Ark API
                              ├── /embeddings       doubao-embedding-vision-251215
                              └── /chat/completions deepseek-v4-1-flash-261017
```

## 4. 模块说明

| 文件 | 职责 |
| --- | --- |
| `app/main.py` | FastAPI 入口，路由定义、请求/响应模型（Pydantic）、异常转换、生命周期管理 |
| `app/rag.py` | `Document` / `VectorStore` / `EmbeddingCache` 数据结构，RAG 编排函数 `answer()` |
| `app/chunking.py` | 文档切分：正则句子边界（中英文标点/换行）+ 重叠窗口 + 超长硬切 |
| `app/parsers.py` | 多格式解析：PDF（pdfplumber 正文+表格）、DOCX（python-docx 段落+表格）、DOC（textutil）、TXT/MD |
| `app/milvus_store.py` | `MilvusStore`：集合自动创建（按向量维度）、COSINE 索引、检索前自动 `load_collection`、持久化存储 |
| `app/ark_client.py` | Ark API 封装：配置读取、共享客户端、`_post_with_retry()`、`embed()`、`chat()` |
| `seed_docs.py` | 示例文档写入脚本（5 条中文知识条目） |
| `benchmark.py` | 压测工具（见 2.6） |
| `.env` / `.env.example` | 环境配置（`.env` 已 gitignore） |
| `requirements.txt` | fastapi、uvicorn[standard]、httpx、python-dotenv、numpy |

## 5. 配置项

| 环境变量 | 当前值 | 说明 |
| --- | --- | --- |
| `ARK_API_KEY` | （密钥，见 `.env`） | 火山方舟 API Key |
| `ARK_BASE_URL` | `https://ark.cn-beijing.volces.com/api/coding/v3` | Ark 接入点（coding plan） |
| `ARK_EMBEDDING_MODEL` | `doubao-embedding-vision-251215` | 文本/多模态向量模型 |
| `ARK_CHAT_MODEL` | `deepseek-v4-1-flash-261017` | 对话生成模型（带推理过程） |
| `EMBED_CACHE_SIZE` | `10000` | 查询向量 LRU 缓存容量 |
| `VECTOR_BACKEND` | `milvus` | 向量存储后端：`milvus`（持久化）/ `memory`（内存） |
| `RAG_MILVUS_URI` | `./milvus_demo.db` | Milvus Lite 文件路径，或独立服务地址 `http://localhost:19530`。勿用 `MILVUS_URI`（与 pymilvus 内置配置冲突） |
| `MILVUS_COLLECTION` | `rag_docs` | Milvus 集合名 |

## 5.1 Milvus 集合结构

首次写入时按实际向量维度自动建表：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INT64 | 主键，auto_id 自增 |
| `vector` | FLOAT_VECTOR(dim) | 文本向量，AUTOINDEX，COSINE 度量 |
| `text` | VARCHAR(65535) | 文档原文 |

检索/查询前自动调用 `load_collection`（重启后集合为 released 状态，必须先加载）。

模型选择说明：该 Key 属于 coding plan，`doubao-embedding-text-*` 与 `doubao-seed-1-6-*` 不可用（`UnsupportedModel`）。可用模型列表可通过 `GET {ARK_BASE_URL}/models` 查询。

## 6. API 参考

### POST /ingest

```json
// 请求
{"texts": ["文档一", "文档二"], "metadatas": [{"source": "manual"}]}
// 响应
{"ids": ["<uuid>", "<uuid>"], "total_docs": 2}
```

### POST /query

```json
// 请求
{"question": "什么是RAG？", "top_k": 3}
// 响应
{
  "answer": "RAG（检索增强生成）是指……",
  "sources": [{"id": "<uuid>", "text": "……", "score": 0.595}]
}
```

### GET /retrieve?q=...&top_k=3

```json
{"hits": [{"id": "<uuid>", "text": "……", "score": 0.525}]}
```

### GET /stats · GET /healthz

```json
{"documents": 5}
{"status": "ok"}
```

错误约定：上游（Ark）失败统一返回 `502 {"detail": "..."}`；参数校验失败返回 FastAPI 默认 `422`。

## 7. 检索算法

- Milvus 后端：AUTOINDEX + COSINE 相似度，由 Milvus 完成 ANN 检索与 Top-K，结果按分数降序
- 内存后端（可选）：向量归一化后矩阵乘法（`v @ q`），`np.argsort` 取 Top-K，O(N·D)
- 查询向量统一走 `get_query_vector()`（LRU 缓存），两种后端共享

## 8. 压测实测结果

环境：本机回环（M 系列 Mac）+ Ark 北京区域，单 uvicorn worker。

| 场景 | 并发 | QPS | P99 延迟 | 错误率 |
| --- | --- | --- | --- | --- |
| `/healthz`（框架极限） | 200 | 685 | 1346ms | 0% |
| `/retrieve`（缓存命中） | 50 | 669 | 473ms | 0% |
| `/retrieve`（`--unique` 无缓存） | 5 | 6.0 | 3962ms | 8.3%（Ark 429） |
| `/query`（完整 RAG） | 3 | 2.3 | 6466ms | 0% |
| `/retrieve`（Milvus，缓存命中） | 50 | 232 | 983ms | 0% |

结论：

1. **瓶颈在远程模型配额而非框架**：FastAPI 单机数百 QPS 无压力；Ark coding plan 的 Embedding 接口 RPM 较低，持续高速请求触发 429
2. **缓存收益显著**：重复问题场景下检索接口从 ~6 QPS 提升到 ~669 QPS
3. **`/query` 延迟主要来自 LLM 生成**（秒级），属正常现象

## 9. 运行与测试

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

python seed_docs.py   # 写入示例文档
curl -X POST localhost:8000/query -H 'Content-Type: application/json' \
     -d '{"question":"什么是RAG？"}'

# 压测
python benchmark.py --endpoint /healthz --concurrency 200 --duration 15
python benchmark.py --endpoint /retrieve --concurrency 50 --duration 10
python benchmark.py --endpoint /retrieve --concurrency 5 --duration 15 --unique
python benchmark.py --endpoint /healthz --mode rps --rps 200 --duration 15
```

## 10. 当前限制

- 切分为轻量字符/句子规则，未做语义切分与父子文档关联
- 元数据不参与检索过滤
- 单 worker 部署；对 Ark 出站请求无主动限流（仅被动重试）
- 无鉴权、无自动化单元测试
- Milvus Lite 为单机嵌入式，不适合多实例共享；多实例需切换独立 Milvus 集群

## 11. 后续演进路线

1. **向量库升级**：Milvus Lite → 独立 Milvus 集群（多实例共享、水平扩展），`RAG_MILVUS_URI` 直接切换
2. **文档处理**：chunking + overlap、元数据过滤、文档管理接口（更新/删除）
3. **缓存体系**：Redis 共享 embedding 缓存与答案缓存，支持多实例
4. **并发治理**：Ark 出站信号量限流、批量 embedding、请求队列与降级、多 worker 部署
5. **工程化**：鉴权、结构化日志与指标（Prometheus）、单元/集成测试
