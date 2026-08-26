"""Benchmark EasyOCRRecognizer with gpu=False vs gpu=True on this machine.

Background: `_RecognizerLoader.load()` (ocr_region_watcher/qt/app.py) passes
gpu=True unconditionally -- EasyOCR falls back to identical CPU behavior when
no CUDA-capable torch is present, so this is safe everywhere, but the actual
speedup on a given machine is worth measuring rather than assumed. This
script does that.

Usage:
    python benchmarks/ocr_gpu_benchmark.py              # compare: runs both, prints a table
    python benchmarks/ocr_gpu_benchmark.py --gpu true    # single run, prints one line + JSON to stdout

Each run is its own process (compare mode shells out twice) so one device's
CUDA context/warmup never contaminates the other's timing. Both runs use the
same fixed synthetic dataset -- small rendered crops of prices, percentages,
negatives, and decimals, resembling what OCRRegionWatcher actually reads --
so timing and accuracy are directly comparable.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ocr_region_watcher.recognize import EasyOCRRecognizer  # noqa: E402


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans-Bold.ttf", "consola.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_samples(n: int = 60, seed: int = 1234) -> list[tuple[np.ndarray, str]]:
    """Synthetic number crops resembling real OCRRegionWatcher regions:
    plain integers, decimals, thousands-grouped amounts, percentages, and
    negative values, at varied sizes/colors -- fixed seed so the CPU and GPU
    runs see byte-identical input."""
    rng = random.Random(seed)
    samples = []
    for i in range(n):
        kind = i % 5
        if kind == 0:
            text = str(rng.randint(0, 9999))
        elif kind == 1:
            text = f"{rng.randint(0, 999)}.{rng.randint(0, 99):02d}"
        elif kind == 2:
            text = f"{rng.randint(1, 999):,}"
        elif kind == 3:
            text = f"{rng.randint(0, 100)}%"
        else:
            text = f"-{rng.randint(1, 500)}.{rng.randint(0, 99):02d}"

        font_size = rng.choice([20, 24, 28])
        font = _font(font_size)
        pad = 10
        dummy = Image.new("RGB", (10, 10))
        bbox = ImageDraw.Draw(dummy).textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0] + pad * 2, bbox[3] - bbox[1] + pad * 2

        dark_on_light = rng.random() > 0.3
        bg = rng.choice([(255, 255, 255), (230, 230, 230)]) if dark_on_light else rng.choice([(20, 20, 20), (0, 0, 0)])
        fg = rng.choice([(0, 0, 0), (10, 10, 10)]) if dark_on_light else rng.choice([(0, 255, 0), (255, 255, 255)])

        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)
        draw.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=fg)
        samples.append((np.array(img)[:, :, ::-1].copy(), text))  # RGB -> BGR to match cv2 convention
    return samples


def run_once(use_gpu: bool, n: int) -> dict:
    samples = make_samples(n)

    t0 = time.perf_counter()
    recognizer = EasyOCRRecognizer(gpu=use_gpu)
    load_s = time.perf_counter() - t0
    device = str(getattr(recognizer._reader, "device", "unknown"))

    # One untimed warmup call -- first inference pays a one-off cost (cudnn
    # autotune / lazy init) that steady-state usage never repeats.
    recognizer.read(samples[0][0], None)

    per_image_s = []
    correct = 0
    results = []
    for img, expected in samples:
        t0 = time.perf_counter()
        reading = recognizer.read(img, None)
        per_image_s.append(time.perf_counter() - t0)
        got = reading.raw_text.strip()
        ok = got.replace(" ", "") == expected.replace(" ", "")
        correct += ok
        results.append({"expected": expected, "got": got, "ok": ok})

    return {
        "gpu_requested": use_gpu,
        "device_actual": device,
        "model_load_s": load_s,
        "n": len(samples),
        "correct": correct,
        "total_recognize_s": sum(per_image_s),
        "avg_ms_per_image": 1000 * sum(per_image_s) / len(per_image_s),
        "median_ms_per_image": 1000 * sorted(per_image_s)[len(per_image_s) // 2],
        "min_ms_per_image": 1000 * min(per_image_s),
        "max_ms_per_image": 1000 * max(per_image_s),
        "results": results,
    }


def _print_table(cpu: dict, gpu: dict) -> None:
    def row(label, cpu_v, gpu_v):
        print(f"{label:<28} {cpu_v:>14} {gpu_v:>14}")

    print()
    row("", "CPU", "GPU")
    row("device", cpu["device_actual"], gpu["device_actual"])
    row("model load", f"{cpu['model_load_s']:.2f}s", f"{gpu['model_load_s']:.2f}s")
    row("avg per-region", f"{cpu['avg_ms_per_image']:.2f}ms", f"{gpu['avg_ms_per_image']:.2f}ms")
    row("median per-region", f"{cpu['median_ms_per_image']:.2f}ms", f"{gpu['median_ms_per_image']:.2f}ms")
    cpu_thr = cpu["n"] / cpu["total_recognize_s"]
    gpu_thr = gpu["n"] / gpu["total_recognize_s"]
    row("throughput", f"{cpu_thr:.1f} img/s", f"{gpu_thr:.1f} img/s")
    row("accuracy", f"{cpu['correct']}/{cpu['n']}", f"{gpu['correct']}/{gpu['n']}")
    print()
    print(f"Speedup (median): {cpu['median_ms_per_image'] / gpu['median_ms_per_image']:.2f}x")

    cpu_bad = {r["expected"] for r in cpu["results"] if not r["ok"]}
    gpu_bad = {r["expected"] for r in gpu["results"] if not r["ok"]}
    if cpu_bad != gpu_bad:
        print(f"WARNING: mismatched failures between devices -- CPU-only: {cpu_bad - gpu_bad}, "
              f"GPU-only: {gpu_bad - cpu_bad}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gpu", choices=["true", "false"], default=None,
                     help="single-run mode: benchmark just this device and print one JSON line. "
                          "Omit to run both (as subprocesses) and print a comparison table.")
    ap.add_argument("-n", type=int, default=60, help="number of synthetic samples")
    args = ap.parse_args()

    if args.gpu is not None:
        result = run_once(args.gpu == "true", args.n)
        print(json.dumps(result))
        return

    # Compare mode: shell out to this same script once per device so neither
    # run's CUDA context/warmup can leak into the other's numbers.
    outputs = {}
    for gpu_flag in ("false", "true"):
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--gpu", gpu_flag, "-n", str(args.n)],
            capture_output=True, text=True, check=True,
        )
        # The script's own JSON is the last line; EasyOCR/torch may print
        # warnings to stdout/stderr before it.
        outputs[gpu_flag] = json.loads(proc.stdout.strip().splitlines()[-1])

    _print_table(outputs["false"], outputs["true"])


if __name__ == "__main__":
    main()
