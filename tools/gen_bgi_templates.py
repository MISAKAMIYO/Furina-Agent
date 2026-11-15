# -*- coding: utf-8 -*-
"""从截图生成 BetterGI / 原神更新流程需要的按钮模板

用法：
    python tools/gen_bgi_templates.py [截图目录]

约定目录下的文件名（中文/英文都行，脚本按关键字识别）：
    更新游戏*.png    → game_btn_update.png
    开始游戏*.png    → game_btn_start.png
    恢复下载*.png    → game_btn_resume.png
    校验并修复*.png  → game_status_verify.png
    *better*更新*.png → bgi_btn_update.png

生成的模板位于 data/bettergi/templates/，更新流程里按名字引用。
"""
from __future__ import annotations

import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "bettergi", "templates")
VENDOR = os.path.join(ROOT, "data", "py_deps", "ocr")
if os.path.isdir(VENDOR):
    sys.path.insert(0, VENDOR)

import numpy as np  # noqa: E402
import cv2  # noqa: E402


def load(path: str):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def yellow_mask(img):
    b, g, r = img[:, :, 0].astype(int), img[:, :, 1].astype(int), img[:, :, 2].astype(int)
    return ((r > 200) & (g > 150) & (b < 120)).astype(np.uint8) * 255


def green_mask(img):
    b, g, r = img[:, :, 0].astype(int), img[:, :, 1].astype(int), img[:, :, 2].astype(int)
    return ((r < 160) & (g > 170) & (b < 170)).astype(np.uint8) * 255


def best_box(img, mask, min_w=60, min_h=24, ratio_range=(2.2, 5.0)):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < min_w or h < min_h:
            continue
        ratio = w / h
        if not (ratio_range[0] <= ratio <= ratio_range[1]):
            continue
        area = w * h
        if best is None or area > best[0]:
            best = (area, x, y, w, h)
    return None if best is None else best[1:]


def save_crop(img, box, name, pad=4):
    x, y, w, h = box
    x1, y1 = max(0, x - pad), max(0, y - pad)
    x2, y2 = min(img.shape[1], x + w + pad), min(img.shape[0], y + h + pad)
    crop = img[y1:y2, x1:x2]
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        raise RuntimeError("模板编码失败: " + name)
    with open(os.path.join(OUT, name), "wb") as f:
        f.write(buf.tobytes())
    print(f"  生成 {name} ({crop.shape[1]}x{crop.shape[0]})")


def find_one(pattern, src_dir):
    hits = sorted(glob.glob(os.path.join(src_dir, pattern)))
    return hits[0] if hits else ""


def main():
    src_dir = sys.argv[1] if len(sys.argv) > 1 else ROOT
    os.makedirs(OUT, exist_ok=True)

    mapping = [
        ("*更新游戏*.png", "yellow", "game_btn_update.png"),
        ("*开始游戏*.png", "yellow", "game_btn_start.png"),
        ("*恢复下载*.png", "yellow", "game_btn_resume.png"),
        ("*校验并修复*.png", "yellow", "game_status_verify.png"),
        ("*better*更新*.png", "green", "bgi_btn_update.png"),
        ("*better*update*.png", "green", "bgi_btn_update.png"),
    ]

    base_update_box = None
    for pattern, color, out_name in mapping:
        path = find_one(pattern, src_dir)
        if not path:
            print(f"  跳过 {out_name}（找不到 {pattern}）")
            continue
        img = load(path)
        if img is None:
            print(f"  跳过 {out_name}（读不了 {path}）")
            continue
        mask = yellow_mask(img) if color == "yellow" else green_mask(img)
        if out_name == "game_status_verify.png":
            h, w = img.shape[:2]
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cand = []
            for c in cnts:
                x, y, ww, hh = cv2.boundingRect(c)
                if not (150 <= ww * hh <= 4000) or y < h * 0.6 or x < w * 0.45:
                    continue
                cand.append((ww * hh, x, y, ww, hh))
            cand.sort(reverse=True)
            if not cand:
                print("  跳过 game_status_verify（找不到状态条）")
                continue
            _, x, y, ww, hh = cand[0]
            save_crop(img, (max(0, x - 40), max(0, y - 16), 230, 62), out_name, pad=0)
            continue

        box = best_box(img, mask, min_w=80 if out_name.startswith("bgi") else 60)
        if not box:
            print(f"  跳过 {out_name}（找不到按钮区域）")
            continue
        if out_name in ("game_btn_update.png", "game_status_download.png"):
            base_update_box = box
        save_crop(img, box, out_name)

    print("模板目录:", OUT)


if __name__ == "__main__":
    main()
