# -*- coding: utf-8 -*-
"""BetterGI 更新检查与更新历史。

- 本地版本：读 BetterGI.exe 的 FileVersion（Windows），失败则退回配置文件/日志里的版本号。
- 远端版本：GitHub Releases API，直连失败时走 ghfast 镜像（国内常用）。
- 历史：每次检查/更新落一条 JSON，便于回滚与审计。

不依赖项目其它模块，方便单测（http_getter / version_getter 可注入）。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

DEFAULT_REPO = "babalae/better-genshin-impact"
MIRRORS = (
    "https://api.github.com/repos/{repo}/releases/latest",
    "https://ghfast.top/https://api.github.com/repos/{repo}/releases/latest",
)


def parse_version(text: str) -> tuple:
    """'0.67.0.0' / 'v0.67' / 'BetterGI 0.67.0' -> (0, 67, 0, 0)。"""
    nums = re.findall(r"\d+", str(text or ""))
    out = [int(n) for n in nums[:4]]
    while len(out) < 4:
        out.append(0)
    return tuple(out)


def version_str(v: tuple) -> str:
    return ".".join(str(x) for x in v)


def local_bgi_version(exe_path: str = "") -> str:
    """读 BetterGI.exe 的文件版本；非 Windows / 读不到返回空串。"""
    if exe_path and os.path.isfile(exe_path):
        try:
            import ctypes
            from ctypes import wintypes

            size = ctypes.windll.version.GetFileVersionInfoSizeW(exe_path, None)
            if size:
                buf = ctypes.create_string_buffer(size)
                if ctypes.windll.version.GetFileVersionInfoW(exe_path, 0, size, buf):
                    r = ctypes.c_void_p()
                    ln = wintypes.UINT()
                    if ctypes.windll.version.VerQueryValueW(
                        buf, "\\", ctypes.byref(r), ctypes.byref(ln)
                    ):
                        fixed = ctypes.cast(r, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
                        ms, ls = fixed[2], fixed[3]
                        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        except Exception:
            pass
    # 退回：从目录里的 version.txt / 日志里找
    root = os.path.dirname(exe_path) if exe_path else ""
    if root and os.path.isdir(root):
        for name in ("version.txt", "VERSION", "Version.txt"):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        return f.read(64).strip()
                except Exception:
                    pass
    return ""


def fetch_latest_release(
    repo: str = DEFAULT_REPO,
    http_getter: Optional[Callable[[str], Dict[str, Any]]] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """取最新 release；返回 {ok, tag, name, url, published, notes, source, error}。"""
    errors = []
    for tpl in MIRRORS:
        url = tpl.format(repo=repo)
        try:
            if http_getter is not None:
                data = http_getter(url)
            else:
                import requests

                resp = requests.get(
                    url,
                    timeout=timeout if "ghfast" not in url else timeout + 5,
                    headers={"User-Agent": "FurinaAgent-BetterGI", "Accept": "application/vnd.github+json"},
                )
                if resp.status_code != 200:
                    errors.append(f"{url} -> HTTP {resp.status_code}")
                    continue
                data = resp.json()
            tag = str(data.get("tag_name") or data.get("name") or "").strip()
            if not tag:
                errors.append(f"{url} -> 没有 tag_name")
                continue
            return {
                "ok": True,
                "repo": repo,
                "tag": tag,
                "version": version_str(parse_version(tag)),
                "name": data.get("name") or tag,
                "url": data.get("html_url") or "",
                "published": data.get("published_at") or "",
                "notes": (data.get("body") or "")[:2000],
                "prerelease": bool(data.get("prerelease")),
                "source": url,
                "error": "",
            }
        except Exception as exc:
            errors.append(f"{url} -> {type(exc).__name__}: {exc}")
    return {"ok": False, "repo": repo, "tag": "", "version": "", "error": "; ".join(errors)[-800:]}


class UpdateManager:
    def __init__(
        self,
        history_path: str,
        exe_getter: Callable[[], str],
        config_getter: Callable[[], Dict[str, Any]],
        release_getter: Optional[Callable[[str], Dict[str, Any]]] = None,
    ) -> None:
        self._history_path = history_path
        self._exe = exe_getter
        self._config = config_getter
        self._fetch = release_getter or (lambda repo: fetch_latest_release(repo))

    # ── 历史 ───────────────────────────────────────────────────
    def _load(self) -> List[Dict[str, Any]]:
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save(self, items: List[Dict[str, Any]]) -> None:
        os.makedirs(os.path.dirname(self._history_path) or ".", exist_ok=True)
        tmp = self._history_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items[-200:], f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._history_path)

    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._load()[-max(1, min(limit, 200)):][::-1]

    def record(self, **entry: Any) -> Dict[str, Any]:
        items = self._load()
        row = {"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        row.update(entry)
        items.append(row)
        self._save(items)
        return row

    # ── 检查 ───────────────────────────────────────────────────
    def local_version(self) -> str:
        return local_bgi_version(self._exe() or "")

    def check(self, record: bool = True) -> Dict[str, Any]:
        repo = str((self._config() or {}).get("update_repo") or DEFAULT_REPO)
        local = self.local_version()
        remote = self._fetch(repo)
        out: Dict[str, Any] = {
            "local": local,
            "local_parsed": version_str(parse_version(local)) if local else "",
            "remote": remote.get("version") if remote.get("ok") else "",
            "remote_tag": remote.get("tag") if remote.get("ok") else "",
            "has_update": False,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "repo": repo,
            "error": remote.get("error", ""),
        }
        if remote.get("ok"):
            out["release"] = {k: remote.get(k) for k in ("tag", "name", "url", "published", "notes", "prerelease", "source")}
            if local:
                out["has_update"] = parse_version(remote["version"]) > parse_version(local)
                out["direction"] = "update" if out["has_update"] else (
                    "same" if parse_version(remote["version"]) == parse_version(local) else "local_newer"
                )
            else:
                out["has_update"] = None
                out["direction"] = "unknown-local"
        if record:
            self.record(kind="check", local=local, remote=out.get("remote"), has_update=out["has_update"], error=out["error"])
        return out
