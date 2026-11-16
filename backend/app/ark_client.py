import asyncio
import json
import math
import os
import time
from pathlib import Path

import httpx

# 配置文件路径：backend/config.json（相对于当前文件所在的 app/ 上一级目录）
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def _load_section(cfg: dict, section: str, env_prefix: str) -> dict:
    """加载单个配置块（chat / embedding），环境变量优先于配置文件。

    rpm / tpm 为可选限速字段（数字），未配置时不限速。
    """
    sec = cfg.get(section, {}) if isinstance(cfg, dict) else {}
    rpm_raw = os.getenv(f"{env_prefix}_RPM") or sec.get("rpm")
    tpm_raw = os.getenv(f"{env_prefix}_TPM") or sec.get("tpm")
    return {
        "base_url": os.getenv(f"{env_prefix}_BASE_URL") or sec.get("base_url", ""),
        "api_key": os.getenv(f"{env_prefix}_API_KEY") or sec.get("api_key", ""),
        "model": os.getenv(f"{env_prefix}_MODEL") or sec.get("model", ""),
        "rpm": float(rpm_raw) if rpm_raw else None,
        "tpm": float(tpm_raw) if tpm_raw else None,
    }


def _load_config() -> dict:
    """从 config.json 加载大模型 API 配置。

    chat 与 embedding 各自独立一块配置（base_url / api_key / model），
    支持 chat 与 embedding 使用不同的服务、密钥和模型。
    配置优先级：环境变量 > config.json；代码中不硬编码任何默认值。
    """
    cfg: dict = {}
    if _CONFIG_PATH.is_file():
        try:
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[ark_client] 读取配置文件失败，将仅使用环境变量: {exc}", flush=True)

    return {
        "chat": _load_section(cfg, "chat", "ARK_CHAT"),
        "embedding": _load_section(cfg, "embedding", "ARK_EMBEDDING"),
    }


_CONFIG = _load_config()

# ---- 对话（chat）配置 ----
CHAT_BASE_URL = _CONFIG["chat"]["base_url"]
CHAT_API_KEY = _CONFIG["chat"]["api_key"]
CHAT_MODEL = _CONFIG["chat"]["model"]

# ---- 向量化（embedding）配置 ----
EMBEDDING_BASE_URL = _CONFIG["embedding"]["base_url"]
EMBEDDING_API_KEY = _CONFIG["embedding"]["api_key"]
EMBEDDING_MODEL = _CONFIG["embedding"]["model"]
EMBEDDING_RPM = _CONFIG["embedding"]["rpm"]   # 每分钟最大请求数（None 不限速）
EMBEDDING_TPM = _CONFIG["embedding"]["tpm"]   # 每分钟最大 token 数（None 不限速）

# embedding 分批与限速参数
_EMBED_BATCH_TOKEN_LIMIT = 8000   # 单批 token 估算上限（低于常见 TPM 限额，留余量）
_CHAR_PER_TOKEN = 1.6             # 粗略 token 估算系数（中文约 1.6 字符/token，保守）
_embed_lock = asyncio.Lock()      # 全局串行闸：多任务共享限速配额，避免互相挤占
_last_embed_request_at = 0.0

_chat_client: httpx.AsyncClient | None = None
_embedding_client: httpx.AsyncClient | None = None


def _build_client(base_url: str, api_key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=httpx.Timeout(60.0),
    )


def get_chat_client() -> httpx.AsyncClient:
    global _chat_client
    if _chat_client is None:
        _chat_client = _build_client(CHAT_BASE_URL, CHAT_API_KEY)
    return _chat_client


def get_embedding_client() -> httpx.AsyncClient:
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = _build_client(EMBEDDING_BASE_URL, EMBEDDING_API_KEY)
    return _embedding_client


async def reload_config() -> None:
    """重新读取 config.json 并热重载全局配置与 client（配置页面保存后调用）。"""
    global _CONFIG, CHAT_BASE_URL, CHAT_API_KEY, CHAT_MODEL
    global EMBEDDING_BASE_URL, EMBEDDING_API_KEY, EMBEDDING_MODEL, EMBEDDING_RPM, EMBEDDING_TPM
    global _chat_client, _embedding_client

    # 关闭旧 client，避免连接泄漏
    for client in (_chat_client, _embedding_client):
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    _CONFIG = _load_config()

    CHAT_BASE_URL = _CONFIG["chat"]["base_url"]
    CHAT_API_KEY = _CONFIG["chat"]["api_key"]
    CHAT_MODEL = _CONFIG["chat"]["model"]

    EMBEDDING_BASE_URL = _CONFIG["embedding"]["base_url"]
    EMBEDDING_API_KEY = _CONFIG["embedding"]["api_key"]
    EMBEDDING_MODEL = _CONFIG["embedding"]["model"]
    EMBEDDING_RPM = _CONFIG["embedding"]["rpm"]
    EMBEDDING_TPM = _CONFIG["embedding"]["tpm"]

    _chat_client = None
    _embedding_client = None
    print("[ark_client] 配置已热重载", flush=True)


async def _post_with_retry(client: httpx.AsyncClient, path: str, payload: dict,
                           max_retries: int = 6) -> httpx.Response:
    """带限流重试的 POST：429 时按 retry-after 或指数退避等待后重试。

    重试序列 1s→2s→4s→8s→16s→30s（上限 30s），6 次重试累计可等待约 1 分钟，
    足以熬过 Voyage 免费层 3 RPM 之类的分钟级限流窗口。
    """
    delay = 1.0
    resp: httpx.Response | None = None
    for attempt in range(max_retries + 1):
        resp = await client.post(path, json=payload)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        if attempt == max_retries:
            resp.raise_for_status()
        retry_after = resp.headers.get("retry-after")
        wait = float(retry_after) if retry_after else delay
        wait = min(wait, 30.0)
        print(f"[ark_client] 429 限流，{attempt + 1}/{max_retries} 次重试，等待 {wait:.1f}s", flush=True)
        await asyncio.sleep(wait)
        delay = min(delay * 2, 30.0)
    return resp


def _batch_interval(tokens: int) -> float:
    """按 rpm / tpm 限速配置计算该批次请求前需等待的秒数。"""
    intervals = []
    if EMBEDDING_RPM:
        intervals.append(60.0 / EMBEDDING_RPM)
    if EMBEDDING_TPM and tokens > 0:
        intervals.append(tokens / EMBEDDING_TPM * 60.0)
    return max(intervals) if intervals else 0.0


def _split_batches(texts: list[str]) -> list[tuple[list[str], int]]:
    """按 token 估算把 texts 拆成不超过单批上限的批次，保持顺序。

    返回 [(批次文本, 批次估算 token 数), ...]。返回向量顺序与输入一一对应。
    """
    batches: list[tuple[list[str], int]] = []
    cur: list[str] = []
    cur_tokens = 0
    for text in texts:
        n = max(1, math.ceil(len(text) / _CHAR_PER_TOKEN))
        if cur and cur_tokens + n > _EMBED_BATCH_TOKEN_LIMIT:
            batches.append((cur, cur_tokens))
            cur, cur_tokens = [text], n
        else:
            cur.append(text)
            cur_tokens += n
    if cur:
        batches.append((cur, cur_tokens))
    return batches


async def embed(texts: list[str]) -> list[list[float]]:
    """批量向量化：分批 + 全局串行限速，兼容 Voyage 免费层 3 RPM / 10K TPM 限制。

    - 按 token 估算分批（单批 ≤ 8000 tokens），结果顺序与输入一一对应
    - 全局锁串行化：多个导入任务并发时排队依次执行，不互相挤占配额
    - 批间按 rpm/tpm 配置自适应等待；429 时再走 _post_with_retry 兜底重试
    """
    batches = _split_batches(texts)
    if not batches:
        return []

    global _last_embed_request_at
    embeddings: list[list[float]] = []

    async with _embed_lock:
        for i, (batch, tokens) in enumerate(batches):
            interval = _batch_interval(tokens)
            if EMBEDDING_TPM and tokens > EMBEDDING_TPM:
                print(
                    f"[ark_client] 警告：单批估算 {tokens} tokens 超过 TPM 限额 {EMBEDDING_TPM:.0f}，"
                    "该批次可能持续 429。建议导入大文档时开启 chunk 切分。",
                    flush=True,
                )
            if i > 0 or _last_embed_request_at > 0:
                now = time.monotonic()
                wait = _last_embed_request_at + interval - now
                if wait > 0:
                    print(
                        f"[ark_client] embedding 批次 {i + 1}/{len(batches)}（约 {tokens} tokens），"
                        f"限速等待 {wait:.0f}s",
                        flush=True,
                    )
                    await asyncio.sleep(wait)

            # 注：不传 encoding_format（OpenAI 默认即 float；Voyage 等服务不认识该参数）
            resp = await _post_with_retry(
                get_embedding_client(),
                "/embeddings",
                {"model": EMBEDDING_MODEL, "input": batch},
            )
            _last_embed_request_at = time.monotonic()
            data = resp.json()["data"]
            data.sort(key=lambda x: x["index"])
            embeddings.extend(item["embedding"] for item in data)

    return embeddings


async def chat(messages: list[dict], temperature: float = 0.3) -> str:
    resp = await _post_with_retry(
        get_chat_client(),
        "/chat/completions",
        {"model": CHAT_MODEL, "messages": messages, "temperature": temperature},
    )
    return resp.json()["choices"][0]["message"]["content"]
