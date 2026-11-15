"""
随机点名

特性:
- 名单分组 + 成员权重（权重越高被抽中概率越大）
- 时间戳伪随机算法：以毫秒时间戳（或指定 seed）派生随机种子，结果可复现
- 去重：最近 N 次抽取（批次）内不重复抽中同一人；池子不足时自动放宽
- 支持一次抽多人、权重轮盘无放回抽取

对外接口:
- 分组: create_group/list_groups/get_group/delete_group
- 成员: list_members/add_member/update_member/delete_member/bulk_members
- 抽取: draw(request) / history(group_id) / reset_logs(group_id)
"""
from __future__ import annotations

import hashlib
import logging
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from zhiyan_web.db.database import execute_delete, execute_insert, execute_query, execute_update

from .models import teaching_settings

logger = logging.getLogger("ZhiYanWeb.Edu")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_seed(custom: str = "") -> Tuple[random.Random, str]:
    """时间戳伪随机：以毫秒时间戳派生种子；给定 seed 时结果可复现"""
    if custom:
        raw = str(custom)
    else:
        raw = str(time.time_ns())
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16)), digest[:16]


# ============= 分组 =============

def create_group(name: str, description: str = "") -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"success": False, "message": "名单名称不能为空"}
    gid = execute_insert(
        "INSERT INTO edu_rollcall_groups (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, description or "", _now(), _now()),
    )
    if not gid:
        return {"success": False, "message": "创建失败"}
    return {"success": True, "id": gid, "message": f"已创建名单「{name}」"}


def list_groups() -> List[Dict[str, Any]]:
    rows = execute_query(
        "SELECT g.id, g.name, g.description, g.created_at, "
        "(SELECT COUNT(*) FROM edu_rollcall_members m WHERE m.group_id = g.id) AS member_count "
        "FROM edu_rollcall_groups g ORDER BY g.id DESC"
    )
    out = []
    for r in rows:
        item = dict(r)
        item["member_count"] = int(item.get("member_count") or 0)
        out.append(item)
    return out


def get_group(group_id: int) -> Optional[Dict[str, Any]]:
    row = execute_query("SELECT * FROM edu_rollcall_groups WHERE id = ?", (int(group_id),), fetch_one=True)
    return dict(row) if row else None


def delete_group(group_id: int) -> Dict[str, Any]:
    if not get_group(group_id):
        return {"success": False, "message": "名单不存在"}
    execute_delete("DELETE FROM edu_rollcall_members WHERE group_id = ?", (int(group_id),))
    execute_delete("DELETE FROM edu_rollcall_logs WHERE group_id = ?", (int(group_id),))
    affected = execute_delete("DELETE FROM edu_rollcall_groups WHERE id = ?", (int(group_id),))
    return {"success": affected > 0, "message": "已删除名单" if affected > 0 else "删除失败"}


# ============= 成员 =============

def _row_to_member(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    try:
        item["weight"] = float(row.get("weight") or 1.0)
    except Exception:
        item["weight"] = 1.0
    item["active"] = bool(int(row.get("active") if row.get("active") is not None else 1))
    item["drawn_count"] = int(row.get("drawn_count") or 0)
    return item


def list_members(group_id: int) -> List[Dict[str, Any]]:
    rows = execute_query(
        "SELECT * FROM edu_rollcall_members WHERE group_id = ? ORDER BY id ASC", (int(group_id),)
    )
    return [_row_to_member(r) for r in rows]


def add_member(group_id: int, name: str, weight: float = 1.0, active: bool = True) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"success": False, "message": "姓名不能为空"}
    if not get_group(group_id):
        return {"success": False, "message": "名单不存在"}
    mid = execute_insert(
        "INSERT INTO edu_rollcall_members (group_id, name, weight, active, drawn_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, ?, ?)",
        (int(group_id), name, max(0.01, float(weight or 1.0)), 1 if active else 0, _now(), _now()),
    )
    if not mid:
        return {"success": False, "message": "添加失败"}
    return {"success": True, "id": mid, "message": f"已添加「{name}」"}


def bulk_members(group_id: int, text: str = "", names: Optional[List[str]] = None,
                 default_weight: float = 1.0, replace: bool = False) -> Dict[str, Any]:
    """批量导入：text 每行一个名字，支持「名字,权重」或「名字 权重」"""
    if not get_group(group_id):
        return {"success": False, "message": "名单不存在"}

    entries: List[Tuple[str, float]] = []
    for nm in (names or []):
        nm = str(nm).strip()
        if nm:
            entries.append((nm, float(default_weight or 1.0)))
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p for p in line.replace("，", ",").replace("\t", ",").replace(" ", ",").split(",") if p.strip()]
        name = parts[0].strip()
        weight = float(default_weight or 1.0)
        if len(parts) > 1:
            try:
                weight = float(parts[1].strip())
            except Exception:
                pass
        if name:
            entries.append((name, max(0.01, weight)))

    if not entries:
        return {"success": False, "message": "没有可导入的姓名"}

    if replace:
        execute_delete("DELETE FROM edu_rollcall_members WHERE group_id = ?", (int(group_id),))

    added = 0
    for name, weight in entries:
        mid = execute_insert(
            "INSERT INTO edu_rollcall_members (group_id, name, weight, active, drawn_count, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, 0, ?, ?)",
            (int(group_id), name, weight, _now(), _now()),
        )
        if mid:
            added += 1
    return {"success": True, "added": added, "message": f"已导入 {added} 人"}


def update_member(member_id: int, patch: Dict[str, Any]) -> bool:
    fields: List[str] = []
    params: List[Any] = []
    if patch.get("name") is not None:
        fields.append("name = ?")
        params.append(str(patch["name"]).strip())
    if patch.get("weight") is not None:
        try:
            fields.append("weight = ?")
            params.append(max(0.01, float(patch["weight"])))
        except Exception:
            pass
    if patch.get("active") is not None:
        fields.append("active = ?")
        params.append(1 if patch["active"] else 0)
    if not fields:
        return False
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(int(member_id))
    return execute_update(f"UPDATE edu_rollcall_members SET {', '.join(fields)} WHERE id = ?", tuple(params)) > 0


def delete_member(member_id: int) -> bool:
    return execute_delete("DELETE FROM edu_rollcall_members WHERE id = ?", (int(member_id),)) > 0


# ============= 抽取 =============

def _recent_excluded(group_id: int, window: int) -> set:
    """最近 window 个批次内抽中过的成员 id 集合（去重用）"""
    if window <= 0:
        return set()
    batches = execute_query(
        "SELECT batch FROM edu_rollcall_logs WHERE group_id = ? AND batch != '' "
        "GROUP BY batch ORDER BY MAX(id) DESC LIMIT ?",
        (int(group_id), int(window)),
    )
    batch_ids = [b["batch"] for b in batches if b.get("batch")]
    if not batch_ids:
        return set()
    placeholders = ",".join("?" for _ in batch_ids)
    rows = execute_query(
        f"SELECT DISTINCT member_id FROM edu_rollcall_logs WHERE group_id = ? AND batch IN ({placeholders})",
        tuple([int(group_id)] + batch_ids),
    )
    return {int(r["member_id"]) for r in rows if int(r.get("member_id") or 0)}


def _weighted_pick(pool: List[Dict[str, Any]], count: int, rng: random.Random) -> List[Dict[str, Any]]:
    """按权重无放回抽取 count 人"""
    available = [dict(m) for m in pool]
    picked: List[Dict[str, Any]] = []
    for _ in range(min(count, len(available))):
        total = sum(max(0.01, float(m.get("weight") or 1.0)) for m in available)
        if total <= 0:
            break
        point = rng.uniform(0, total)
        acc = 0.0
        chosen = available[-1]
        for m in available:
            acc += max(0.01, float(m.get("weight") or 1.0))
            if point <= acc:
                chosen = m
                break
        picked.append(chosen)
        available = [m for m in available if m["id"] != chosen["id"]]
    return picked


def draw(request: Dict[str, Any]) -> Dict[str, Any]:
    """抽取点名"""
    cfg = teaching_settings("rollcall")
    group_id = int(request.get("group_id") or 0)
    group = get_group(group_id)
    if not group:
        return {"success": False, "message": "名单不存在"}

    members = [m for m in list_members(group_id) if m.get("active")]
    if not members:
        return {"success": False, "message": "名单中没有可抽取的成员（请先添加姓名或启用成员）"}

    try:
        count = max(1, min(int(request.get("count") or 1), len(members)))
    except Exception:
        count = 1

    window = request.get("no_repeat_window")
    if window is None:
        try:
            window = int(cfg.get("no_repeat_window", 3))
        except Exception:
            window = 3

    algorithm = str(request.get("algorithm") or cfg.get("algorithm") or "timestamp")
    seed_text = str(request.get("seed") or "")
    rng, digest = _new_seed(seed_text if algorithm == "random" else seed_text)

    # 去重：排除最近 window 批次抽中的人；若剩余不足，逐步放宽
    excluded = _recent_excluded(group_id, int(window))
    pool = [m for m in members if m["id"] not in excluded]
    relaxed = False
    if len(pool) < count:
        pool = members
        relaxed = True

    picked = _weighted_pick(pool, count, rng)
    batch = f"{int(time.time() * 1000)}_{digest[:6]}"
    names: List[str] = []
    for m in picked:
        names.append(m["name"])
        execute_insert(
            "INSERT INTO edu_rollcall_logs (group_id, member_id, name, seed, batch, algorithm, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (group_id, int(m["id"]), m["name"], digest, batch, algorithm, _now()),
        )
        execute_update(
            "UPDATE edu_rollcall_members SET drawn_count = drawn_count + 1, updated_at = ? WHERE id = ?",
            (_now(), int(m["id"])),
        )

    message = "本次抽中：" + "、".join(names)
    if relaxed:
        message += "（可抽人选不足，已放宽去重限制）"
    return {
        "success": True,
        "message": message,
        "names": names,
        "members": picked,
        "seed": digest,
        "batch": batch,
        "algorithm": algorithm,
        "relaxed": relaxed,
    }


def history(group_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    rows = execute_query(
        "SELECT id, member_id, name, seed, batch, algorithm, created_at FROM edu_rollcall_logs "
        "WHERE group_id = ? ORDER BY id DESC LIMIT ?",
        (int(group_id), int(limit)),
    )
    return [dict(r) for r in rows]


def reset_logs(group_id: int) -> Dict[str, Any]:
    removed = execute_delete("DELETE FROM edu_rollcall_logs WHERE group_id = ?", (int(group_id),))
    execute_update("UPDATE edu_rollcall_members SET drawn_count = 0 WHERE group_id = ?", (int(group_id),))
    return {"success": True, "removed": removed, "message": f"已清空 {removed} 条抽取记录"}