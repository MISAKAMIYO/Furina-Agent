"""
QQ 平台 Agent 工具注册模块
=========================

将 QQ 平台能力（好友/群列表读取、指定对象发消息/媒体）注册到 FunctionToolManager，
使 Agent（LLM）能够通过函数调用读取 QQ 联系人并针对指定对象回复。

在 app.py 初始化时调用 register_qq_tools()。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("ZhiYanWeb.QQPlatform.Register")


def register_qq_tools() -> None:
    """将 QQ 平台能力注册为 Agent 工具"""
    try:
        from zhiyan_web.core import func_tool_manager
    except (ImportError, AttributeError):
        logger.warning("FunctionToolManager 不可用，跳过 QQ 工具注册")
        return

    if func_tool_manager is None:
        logger.warning("func_tool_manager 为 None，跳过 QQ 工具注册")
        return

    from zhiyan_web.platform.manager import get_qq_platform_manager, _get_qq_platform_cfg
    from zhiyan_web.platform.onebot_client import build_media_segment, path_to_qq_file

    count = 0

    def _register(name: str, params: list, desc: str, handler) -> None:
        nonlocal count
        func_tool_manager.add_func(name=name, func_args=params, desc=desc, handler=handler)
        count += 1
        logger.debug(f"注册 QQ 平台工具: {name}")

    def _mgr_online():
        """返回 (管理器, client) 或 (None, None)"""
        mgr = get_qq_platform_manager()
        client = mgr.client
        if not client or not client.is_online:
            return None, None
        return mgr, client

    async def _send_payload_any(mgr, client, ttype: str, target_id, payload) -> str:
        """发送消息负载（支持 [文件:路径] 段走 NapCat 上传接口）。

        Args:
            ttype: "private" 或 "group"
            payload: 纯文本字符串或消息段列表
        Returns: 成功消息或 "错误:" 开头的失败信息
        """
        from zhiyan_web.platform.manager import extract_media_markers
        # 若纯文本中夹带 [文件:路径] 标记，先解析为消息段
        if isinstance(payload, str) and "[文件:" in payload:
            media_segments, clean_text = extract_media_markers(payload)
            if media_segments:
                payload = ([{"type": "text", "data": {"text": clean_text}}] if clean_text else []) \
                    + media_segments

        if isinstance(payload, list):
            file_segs = [s for s in payload if isinstance(s, dict) and s.get("type") == "file"]
            normal_segs = [s for s in payload if not (isinstance(s, dict) and s.get("type") == "file")]
        else:
            file_segs, normal_segs = [], payload

        if normal_segs:
            if ttype == "private":
                resp = await client.send_private_msg(target_id, normal_segs, timeout=30)
            else:
                resp = await client.send_group_msg(target_id, normal_segs, timeout=30)
            status = (resp or {}).get("status")
            if status not in ("ok", "success"):
                return f"错误: 发送失败: {resp}"

        for seg in file_segs:
            data = seg.get("data", {}) or {}
            file_ref = mgr._qq_upload_path(data.get("file", ""))
            if not file_ref:
                continue
            if ttype == "private":
                resp = await client.upload_private_file(target_id, file_ref, timeout=60)
            else:
                resp = await client.upload_group_file(target_id, file_ref, timeout=60)
            status = (resp or {}).get("status")
            if status not in ("ok", "success"):
                return f"错误: 文件上传失败: {resp}"
        return ""

    # ────────── 好友 / 群列表读取 ──────────

    async def _get_friend_list() -> str:
        """读取 QQ 好友列表"""
        _m, client = _mgr_online()
        if not client:
            return "错误: QQ 平台未连接，无法读取好友列表"
        resp = await client.get_friend_list(timeout=10)
        if not resp or resp.get("status") not in ("ok", "success"):
            return f"错误: 读取好友列表失败: {resp}"
        friends = []
        for f in (resp.get("data") or []):
            friends.append({
                "user_id": str(f.get("user_id", "")),
                "nickname": f.get("nickname", ""),
                "remark": f.get("remark", ""),
            })
        return json.dumps(friends, ensure_ascii=False, indent=2)

    _register(
        "qq_get_friend_list",
        [],
        "读取机器人的 QQ 好友列表，返回每个好友的 QQ 号(user_id)、昵称(nickname)与备注(remark)。"
        "用于查找可以向谁私聊发送消息。",
        _get_friend_list,
    )

    async def _get_group_list() -> str:
        """读取 QQ 群列表"""
        _m, client = _mgr_online()
        if not client:
            return "错误: QQ 平台未连接，无法读取群列表"
        resp = await client.get_group_list(timeout=10)
        if not resp or resp.get("status") not in ("ok", "success"):
            return f"错误: 读取群列表失败: {resp}"
        groups = []
        for g in (resp.get("data") or []):
            groups.append({
                "group_id": str(g.get("group_id", "")),
                "group_name": g.get("group_name", ""),
            })
        return json.dumps(groups, ensure_ascii=False, indent=2)

    _register(
        "qq_get_group_list",
        [],
        "读取机器人所在的 QQ 群列表，返回每个群的群号(group_id)与群名(group_name)。"
        "用于查找可以向哪个群发送消息。",
        _get_group_list,
    )

    async def _get_group_member_list(group_id: Any = "") -> str:
        """读取指定群的成员列表"""
        _m, client = _mgr_online()
        if not client:
            return "错误: QQ 平台未连接，无法读取群成员列表"
        if not str(group_id or "").strip():
            return "错误: 缺少参数 group_id（群号）"
        resp = await client.call_api(
            "get_group_member_list",
            {"group_id": group_id},
            timeout=12,
        )
        if not resp or resp.get("status") not in ("ok", "success"):
            return f"错误: 读取群成员列表失败: {resp}"
        members = []
        for m in (resp.get("data") or []):
            members.append({
                "user_id": str(m.get("user_id", "")),
                "nickname": m.get("nickname", ""),
                "card": m.get("card", ""),
            })
        return json.dumps(members, ensure_ascii=False, indent=2)

    _register(
        "qq_get_group_member_list",
        [
            {"name": "group_id", "type": "string", "description": "QQ 群号"},
        ],
        "读取指定 QQ 群的成员列表，返回每个成员的 QQ 号(user_id)、昵称(nickname)与群名片(card)。"
        "用于在群聊中针对特定成员发送消息。",
        _get_group_member_list,
    )

    # ────────── 指定对象发消息 ──────────

    async def _send_private_msg(user_id: Any = "", message: str = "") -> str:
        """给指定 QQ 好友发送私聊消息"""
        _m, client = _mgr_online()
        if not client:
            return "错误: QQ 平台未连接，无法发送消息"
        if not str(user_id or "").strip():
            return "错误: 缺少参数 user_id（对方 QQ 号）"
        if not str(message or "").strip():
            return "错误: 缺少参数 message（消息内容）"

        from zhiyan_web.platform.manager import extract_media_markers, parse_at_markers
        media_segments, clean_text = extract_media_markers(str(message))
        at_segments, has_at = parse_at_markers(clean_text)
        if has_at or media_segments:
            payload: Any = []
            if has_at:
                payload.extend(at_segments)
            elif clean_text:
                payload.append({"type": "text", "data": {"text": clean_text}})
            payload.extend(media_segments)
        else:
            payload = clean_text

        await _m._pace_send(_get_qq_platform_cfg())
        err = await _send_payload_any(_m, client, "private", user_id, payload)
        if err:
            return err
        return f"已发送到 {user_id}: {clean_text or '(媒体)'}"

    _register(
        "qq_send_private_msg",
        [
            {"name": "user_id", "type": "string", "description": "对方 QQ 号（来自好友列表）"},
            {"name": "message", "type": "string", "description": "要发送的消息内容，支持 [图片:路径] / [语音:路径] / [视频:路径] / [文件:路径] 媒体标记"},
        ],
        "向指定 QQ 好友发送私聊消息。user_id 必须是好友列表中的 QQ 号，message 为消息正文。",
        _send_private_msg,
    )

    async def _send_group_msg(group_id: Any = "", message: str = "") -> str:
        """给指定 QQ 群发送消息"""
        _m, client = _mgr_online()
        if not client:
            return "错误: QQ 平台未连接，无法发送消息"
        if not str(group_id or "").strip():
            return "错误: 缺少参数 group_id（群号）"
        if not str(message or "").strip():
            return "错误: 缺少参数 message（消息内容）"

        from zhiyan_web.platform.manager import extract_media_markers, parse_at_markers
        media_segments, clean_text = extract_media_markers(str(message))
        at_segments, has_at = parse_at_markers(clean_text)
        if has_at or media_segments:
            payload: Any = []
            if has_at:
                payload.extend(at_segments)
            elif clean_text:
                payload.append({"type": "text", "data": {"text": clean_text}})
            payload.extend(media_segments)
        else:
            payload = clean_text

        await _m._pace_send(_get_qq_platform_cfg())
        err = await _send_payload_any(_m, client, "group", group_id, payload)
        if err:
            return err
        return f"已发送到群 {group_id}: {clean_text or '(媒体)'}"

    _register(
        "qq_send_group_msg",
        [
            {"name": "group_id", "type": "string", "description": "群号（来自群列表）"},
            {"name": "message", "type": "string", "description": "要发送的消息内容，支持 [图片:路径] / [语音:路径] / [视频:路径] 媒体标记，@QQ 请使用 @{qq} 标记"},
        ],
        "向指定 QQ 群发送消息。group_id 必须是群列表中的群号，message 为消息正文。",
        _send_group_msg,
    )

    # ────────── 指定对象发媒体 ──────────

    async def _send_media(target_type: str = "", target_id: Any = "",
                          media_type: str = "", file: str = "", text: str = "") -> str:
        """向指定对象发送媒体（图片/语音/视频/文件）"""
        _m, client = _mgr_online()
        if not client:
            return "错误: QQ 平台未连接，无法发送媒体"
        ttype = str(target_type or "").strip().lower()
        if ttype not in ("private", "group"):
            return "错误: target_type 必须为 private（好友）或 group（群）"
        if not str(target_id or "").strip():
            return "错误: 缺少参数 target_id（对方 QQ 号或群号）"
        mtype = str(media_type or "").strip().lower()
        if mtype not in ("image", "record", "video", "file"):
            return "错误: media_type 必须为 image（图片）/ record（语音）/ video（视频）/ file（文件）"
        if not str(file or "").strip():
            return "错误: 缺少参数 file（媒体文件路径或 URL）"

        # 普通文件走 NapCat 上传接口，其余媒体走消息段发送
        if mtype == "file":
            await _m._pace_send(_get_qq_platform_cfg())
            from zhiyan_web.platform.onebot_client import path_to_qq_file
            file_ref = _m._qq_upload_path(path_to_qq_file(str(file)))
            if ttype == "private":
                resp = await client.upload_private_file(target_id, file_ref, timeout=60)
            else:
                resp = await client.upload_group_file(target_id, file_ref, timeout=60)
            status = (resp or {}).get("status")
            if status in ("ok", "success"):
                return f"已向 {target_type} {target_id} 上传文件: {file_ref}"
            return f"错误: 文件上传失败: {resp}"

        from zhiyan_web.platform.onebot_client import build_media_segment, path_to_qq_file
        seg = build_media_segment(mtype, path_to_qq_file(str(file)))
        payload: Any = []
        if str(text or "").strip():
            payload.append({"type": "text", "data": {"text": str(text)}})
        payload.append(seg)

        await _m._pace_send(_get_qq_platform_cfg())
        if ttype == "private":
            resp = await client.send_private_msg(target_id, payload, timeout=30)
        else:
            resp = await client.send_group_msg(target_id, payload, timeout=30)
        status = (resp or {}).get("status")
        if status in ("ok", "success"):
            return f"已向 {target_type} {target_id} 发送 {mtype}: {text or '(无文本)'}"
        return f"错误: 发送媒体失败: {resp}"

    _register(
        "qq_send_media",
        [
            {"name": "target_type", "type": "string", "description": "目标类型：private（好友）或 group（群）"},
            {"name": "target_id", "type": "string", "description": "对方 QQ 号（private）或群号（group）"},
            {"name": "media_type", "type": "string", "description": "媒体类型：image（图片）/ record（语音）/ video（视频）/ file（文件）"},
            {"name": "file", "type": "string", "description": "媒体文件：本地绝对路径、http(s) URL 或 base64:// 前缀数据"},
            {"name": "text", "type": "string", "description": "可选，随媒体一并发送的文本消息"},
        ],
        "向指定 QQ 好友或群发送媒体文件（图片/语音/视频/文件）。",
        _send_media,
    )

    logger.info(f"QQ 平台 Agent 工具注册完成，共 {count} 个")
