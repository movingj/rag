"""高并发压测：统计 QPS、延迟分位数、错误率。

用法:
    python benchmark.py --endpoint /healthz --concurrency 50 --duration 10
    python benchmark.py --endpoint /retrieve --concurrency 20 --duration 30
    python benchmark.py --endpoint /query --concurrency 5 --duration 30  # 会消耗 LLM token

支持 --mode fixed (固定并发) 和 --mode rps --rps 100 (恒定请求速率)。
"""

import argparse
import asyncio
import json
import random
import statistics
import time

import httpx

DEFAULT_QUESTIONS = [
    "什么是 RAG？",
    "FastAPI 有什么特点？",
    "火山方舟是什么？",
    "余弦相似度用在哪里？",
    "有哪些压测工具？",
]


def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    data = sorted(data)
    k = min(len(data) - 1, max(0, int(len(data) * p)))
    return data[k]


async def one_request(client: httpx.AsyncClient, endpoint: str, unique: bool = False) -> tuple[float, bool, str | None]:
    start = time.perf_counter()
    ok = True
    error_detail = None
    question = random.choice(DEFAULT_QUESTIONS)
    if unique:
        question = f"{question}#{random.getrandbits(40)}"
    try:
        if endpoint == "/query":
            resp = await client.post(endpoint, json={"question": question})
        elif endpoint == "/retrieve":
            resp = await client.get(endpoint, params={"q": question})
        else:
            resp = await client.get(endpoint)
        if resp.status_code >= 400:
            ok = False
            error_detail = f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as exc:
        ok = False
        error_detail = f"{type(exc).__name__}: {exc}"
    return time.perf_counter() - start, ok, error_detail


class OpenLoopGate:
    """mode=rps 时的放行闸门；fixed 模式下 acquire 立即返回。"""

    def __init__(self, rps: float | None):
        self.rps = rps
        self._sem = asyncio.Semaphore(0)
        self._task: asyncio.Task | None = None
        if rps:
            self._task = asyncio.create_task(self._fill())

    async def _fill(self):
        interval = 1.0 / self.rps
        while True:
            self._sem.release()
            await asyncio.sleep(interval)

    async def acquire(self):
        if self.rps:
            await self._sem.acquire()

    def stop(self):
        if self._task:
            self._task.cancel()


async def worker(client, endpoint, end_time, results, gate, unique):
    while time.perf_counter() < end_time:
        await gate.acquire()
        if time.perf_counter() >= end_time:
            break
        latency, ok, detail = await one_request(client, endpoint, unique)
        results.append((latency, ok, detail))


async def run(args):
    limits = httpx.Limits(
        max_connections=args.concurrency * 2,
        max_keepalive_connections=args.concurrency * 2,
    )
    timeout = httpx.Timeout(args.timeout)
    results: list[tuple[float, bool]] = []

    async with httpx.AsyncClient(base_url=args.url, limits=limits, timeout=timeout) as client:
        resp = await client.get("/healthz")
        resp.raise_for_status()
        print(f"目标 {args.url} 健康检查通过，预热 {args.warmup}s ...")

        warmup_end = time.perf_counter() + args.warmup

        async def warmup_worker():
            while time.perf_counter() < warmup_end:
                await one_request(client, args.endpoint, args.unique)

        await asyncio.gather(*[warmup_worker() for _ in range(args.concurrency)])

        gate = OpenLoopGate(args.rps if args.mode == "rps" else None)
        end_time = time.perf_counter() + args.duration
        started = time.perf_counter()
        workers = [
            asyncio.create_task(worker(client, args.endpoint, end_time, results, gate, args.unique))
            for _ in range(args.concurrency)
        ]
        await asyncio.gather(*workers)
        gate.stop()
        elapsed = time.perf_counter() - started

    latencies = [r[0] for r in results]
    errors = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    sample_errors = []
    for _, ok, detail in results:
        if not ok and detail and detail not in sample_errors:
            sample_errors.append(detail)
        if len(sample_errors) >= 3:
            break
    report = {
        "endpoint": args.endpoint,
        "mode": args.mode,
        "concurrency": args.concurrency,
        "duration_s": round(elapsed, 2),
        "total_requests": total,
        "errors": errors,
        "error_rate": round(errors / total, 4) if total else 0,
        "qps": round(total / elapsed, 2),
        "sample_errors": sample_errors,
        "latency_ms": {
            "avg": round(statistics.mean(latencies) * 1000, 1) if latencies else 0,
            "p50": round(percentile(latencies, 0.50) * 1000, 1),
            "p90": round(percentile(latencies, 0.90) * 1000, 1),
            "p95": round(percentile(latencies, 0.95) * 1000, 1),
            "p99": round(percentile(latencies, 0.99) * 1000, 1),
            "max": round(max(latencies) * 1000, 1) if latencies else 0,
        },
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="RAG demo 压测工具")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--endpoint", default="/healthz")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--duration", type=float, default=10, help="压测时长(秒)")
    parser.add_argument("--warmup", type=float, default=2, help="预热时长(秒)")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--mode", choices=["fixed", "rps"], default="fixed")
    parser.add_argument("--rps", type=float, default=100, help="mode=rps 时的目标速率")
    parser.add_argument("--unique", action="store_true", help="每个请求使用唯一问题，绕过查询向量缓存")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
