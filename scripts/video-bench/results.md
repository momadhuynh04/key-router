# Video benchmark — results

Run `python scripts/video-bench/bench.py` to generate this file (it is overwritten
each run). It compares model × GPU on the metric that matters for cost:

```
cost_per_video_second = (gpu_price_per_second × total_billed_gpu_seconds)
                        / generated_video_seconds
```

`total_billed_gpu_seconds` includes container start + ComfyUI start + model load +
generation + `scaledown_window` (that is why `min_containers=0` matters).

## Decision table (fill after benchmark)

| model / family | gpu | resolution | duration | median total s | cold start s | cost/clip | success | blind quality |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Wan2.2-TI2V-5B | L40S | 720p | 5s | | | | | |
| Wan2.2-TI2V-5B | A100-80GB | 720p | 5s | | | | | |
| Wan2.2-I2V-A14B (4-step) | L40S | 720p | 5s | | | | | |
| Wan2.2-I2V-A14B (4-step) | H100 | 720p | 5s | | | | | |
| (hosted baseline) | — | 720p | 5s | | | | | |

## Acceptance gates (adcraft_video.md §2.4)

- cold start ≤ 120s (container + ComfyUI + weights)
- 5s 720p generation ≤ 3 min
- draft ≤ $0.10/clip, final ≤ $0.40/clip
- success rate ≥ 95% over 20 prompts × 3 seeds
- blind review: ≥ 3.5/5 draft, ≥ 4/5 final

## GPU price reference (Modal, per second)

| GPU | $/s | $/h |
| --- | --- | --- |
| L40S 48GB | 0.000542 | 1.95 |
| A100 80GB | 0.000694 | 2.50 |
| RTX PRO 6000 96GB | 0.000842 | 3.03 |
| H100 SXM5 80GB | 0.001097 | 3.95 |
| B200 180GB | 0.001736 | 6.25 |
| B300 288GB | 0.001972 | 7.10 |

> Pick by *generated video-seconds per GPU-dollar*, not by hourly price.
