#!/usr/bin/env python3
"""
Phase 0 benchmark harness for the KeyRouter video route.

Measures cold start / generation time / output size / failure rate for a set of
real AdCraft prompts, then reports cost per generated video-second:

    cost_per_video_second = (gpu_price_per_second * total_billed_gpu_seconds)
                            / generated_video_seconds

It talks to the Modal gateway directly (default) so cold start is measurable, or
through the KeyRouter /v1/videos API (--target keyrouter) to test the whole path.

Usage:
    export MODAL_VIDEO_GATEWAY_URL=https://...modal.run
    export MODAL_VIDEO_GATEWAY_TOKEN=...
    python scripts/video-bench/bench.py --gpu L40S --runs 3
    python scripts/video-bench/bench.py --target keyrouter --base-url http://127.0.0.1:8082

Outputs results.json + results.md next to this script.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent

GPU_PRICES_PER_SECOND = {
    "L40S": 0.000542,
    "A100-80GB": 0.000694,
    "RTX PRO 6000": 0.000842,
    "H100": 0.001097,
    "B200": 0.001736,
    "B300": 0.001972,
}


def _load_prompts() -> list[dict]:
    return json.loads((HERE / "prompts.json").read_text())


def _gateway_run(client: httpx.Client, base_url: str, headers: dict, prompt: str, seed: int, args) -> dict:
    t_submit = time.monotonic()
    resp = client.post(
        f"{base_url}/generate",
        headers=headers,
        json={
            "alias": "adcraft-video",
            "family": args.family,
            "quality_tier": args.quality_tier,
            "prompt": prompt,
            "duration_seconds": args.duration,
            "resolution": args.resolution,
            "aspect_ratio": args.aspect_ratio,
            "seed": seed,
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    call_id = resp.json()["call_id"]

    while True:
        if time.monotonic() - t_submit > args.timeout:
            return {"ok": False, "error": "timeout", "total_seconds": args.timeout}
        status = client.get(f"{base_url}/status/{call_id}", headers=headers, timeout=30.0).json()
        if status.get("status") in ("completed", "failed"):
            break
        time.sleep(args.poll_interval)

    if status.get("status") != "completed":
        return {"ok": False, "error": json.dumps(status.get("error"))[:300],
                "total_seconds": round(time.monotonic() - t_submit, 1)}

    content = client.get(f"{base_url}/content/{call_id}", headers=headers, timeout=args.timeout)
    content.raise_for_status()
    usage = status.get("usage") or {}
    return {
        "ok": True,
        "total_seconds": round(time.monotonic() - t_submit, 1),
        "cold_start_seconds": usage.get("cold_start_seconds"),
        "generation_seconds": usage.get("generation_seconds"),
        "gpu_seconds": usage.get("gpu_seconds"),
        "bytes": len(content.content),
        "output": status.get("output"),
    }


def _keyrouter_run(client: httpx.Client, base_url: str, headers: dict, prompt: str, seed: int, args) -> dict:
    t_submit = time.monotonic()
    resp = client.post(
        f"{base_url}/v1/videos",
        headers=headers,
        json={
            "model": "adcraft-video",
            "prompt": prompt,
            "duration_seconds": args.duration,
            "resolution": args.resolution,
            "aspect_ratio": args.aspect_ratio,
            "seed": seed,
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    job_id = resp.json()["id"]

    while True:
        if time.monotonic() - t_submit > args.timeout:
            return {"ok": False, "error": "timeout", "total_seconds": args.timeout}
        job = client.get(f"{base_url}/v1/videos/{job_id}", headers=headers, timeout=30.0).json()
        if job.get("status") in ("completed", "failed", "cancelled"):
            break
        time.sleep(args.poll_interval)

    if job.get("status") != "completed":
        return {"ok": False, "error": json.dumps(job.get("error"))[:300],
                "total_seconds": round(time.monotonic() - t_submit, 1)}

    content = client.get(f"{base_url}/v1/videos/{job_id}/content", headers=headers, timeout=args.timeout)
    content.raise_for_status()
    usage = job.get("usage") or {}
    return {
        "ok": True,
        "total_seconds": round(time.monotonic() - t_submit, 1),
        "cold_start_seconds": usage.get("cold_start_seconds"),
        "generation_seconds": usage.get("generation_seconds"),
        "gpu_seconds": usage.get("gpu_seconds"),
        "bytes": len(content.content),
        "output": job.get("output"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["gateway", "keyrouter"], default="gateway")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--gpu", default=os.environ.get("BENCH_GPU", "L40S"))
    parser.add_argument("--family", default="wan22_ti2v_5b")
    parser.add_argument("--quality-tier", default="draft")
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--runs", type=int, default=3, help="seeds per prompt")
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--limit", type=int, default=0, help="only N prompts (0 = all)")
    args = parser.parse_args()

    if args.target == "gateway":
        base_url = (args.base_url or os.environ.get("MODAL_VIDEO_GATEWAY_URL", "")).rstrip("/")
        token = os.environ.get("MODAL_VIDEO_GATEWAY_TOKEN", "")
        run = _gateway_run
    else:
        base_url = (args.base_url or "http://127.0.0.1:8082").rstrip("/")
        token = os.environ.get("KEYROUTER_API_KEY", "")
        run = _keyrouter_run
    if not base_url:
        raise SystemExit("No base URL. Set MODAL_VIDEO_GATEWAY_URL or pass --base-url.")

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    prompts = _load_prompts()
    if args.limit:
        prompts = prompts[: args.limit]

    price = GPU_PRICES_PER_SECOND.get(args.gpu, 0.000542)
    results = []
    total_video_seconds = 0.0
    total_gpu_seconds = 0.0
    failures = 0

    with httpx.Client() as client:
        for item in prompts:
            for run_idx in range(args.runs):
                seed = 1000 + run_idx
                outcome = run(client, base_url, headers, item["prompt"], seed, args)
                row = {"prompt_id": item.get("id"), "kind": item.get("kind"), "seed": seed, **outcome}
                results.append(row)
                if outcome.get("ok"):
                    total_video_seconds += args.duration
                    total_gpu_seconds += float(outcome.get("gpu_seconds") or outcome.get("total_seconds") or 0)
                    print(f"[ok]   {item.get('id')} seed={seed} {outcome['total_seconds']}s "
                          f"cold={outcome.get('cold_start_seconds')} bytes={outcome['bytes']}")
                else:
                    failures += 1
                    print(f"[fail] {item.get('id')} seed={seed} {outcome.get('error')}")

    total_runs = len(results)
    summary = {
        "target": args.target,
        "gpu": args.gpu,
        "family": args.family,
        "quality_tier": args.quality_tier,
        "duration_seconds": args.duration,
        "resolution": args.resolution,
        "runs": total_runs,
        "failures": failures,
        "success_rate": round(1 - failures / total_runs, 3) if total_runs else 0,
        "total_gpu_seconds": round(total_gpu_seconds, 1),
        "total_video_seconds": round(total_video_seconds, 1),
        "cost_per_video_second_usd": round(price * total_gpu_seconds / total_video_seconds, 5) if total_video_seconds else None,
        "cost_per_clip_usd": round(price * total_gpu_seconds / total_runs, 4) if total_runs else None,
        "median_total_seconds": round(statistics.median([r["total_seconds"] for r in results if r.get("ok")] or [0]), 1),
    }

    (HERE / "results.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    _write_markdown(summary, results)
    print(json.dumps(summary, indent=2))
    print(f"\nWrote {HERE/'results.json'} and {HERE/'results.md'}")


def _write_markdown(summary: dict, results: list[dict]) -> None:
    lines = [
        "# Video benchmark — results",
        "",
        "Auto-generated by `bench.py`. Fill the *quality* column via blind review (1–5).",
        "",
        "## Summary",
        "",
        "| field | value |",
        "| --- | --- |",
    ]
    for key, value in summary.items():
        lines.append(f"| {key} | {value} |")
    lines += [
        "",
        "## Acceptance gates (from adcraft_video.md §2.4)",
        "",
        "- cold start ≤ 120s",
        "- 5s 720p generation ≤ 3 min",
        "- draft ≤ $0.10/clip, final ≤ $0.40/clip",
        "- success ≥ 95%",
        "- blind review ≥ 3.5/5 draft, ≥ 4/5 final",
        "",
        "## Per-run",
        "",
        "| prompt | kind | seed | ok | total s | cold start s | gen s | gpu s | bytes | quality |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in results:
        lines.append(
            "| {pid} | {kind} | {seed} | {ok} | {total} | {cold} | {gen} | {gpu} | {bytes} | |".format(
                pid=row.get("prompt_id"), kind=row.get("kind"), seed=row.get("seed"),
                ok="✓" if row.get("ok") else "✗", total=row.get("total_seconds"),
                cold=row.get("cold_start_seconds"), gen=row.get("generation_seconds"),
                gpu=row.get("gpu_seconds"), bytes=row.get("bytes"),
            )
        )
    (HERE / "results.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
