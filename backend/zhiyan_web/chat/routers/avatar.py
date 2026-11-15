"""
头像 API 路由
提供头像相关的 API 接口

接口列表:
- GET /api/avatar/zhiyan    - 获取芙宁娜头像
- GET /api/avatar/user      - 获取用户头像
"""
from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import Response, FileResponse

from zhiyan_web.config.settings import FRONTEND_DIR

router = APIRouter(prefix="/api/avatar", tags=["avatar"])

# 自定义头像图（存在则优先使用，替换内置矢量头像）
_AVATAR_IMAGE = os.path.join(FRONTEND_DIR, "static", "assets", "avatar.jpg")

# 内置矢量头像（不依赖外部图片素材，避免第三方肖像/著作权问题）
# 深夜蓝舞台配色：深藏蓝底 + 金环 + 「芙」字
_AVATAR_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0%" stop-color="#16224a"/><stop offset="100%" stop-color="#0b1026"/>
</linearGradient></defs>
<circle cx="64" cy="64" r="64" fill="url(#g)"/>
<circle cx="64" cy="64" r="55" fill="none" stroke="#e6c15a" stroke-width="2" opacity="0.55"/>
<text x="64" y="64" dy="0.35em" text-anchor="middle" font-family="Microsoft YaHei, PingFang SC, sans-serif"
 font-size="60" font-weight="600" fill="#e6c15a">芙</text>
</svg>"""


@router.get("/zhiyan")
async def get_zhiyan_avatar():
    """
    获取芙宁娜头像（优先使用自定义头像图，缺省回退内置矢量头像）

    返回:
        FileResponse/Response: 头像图片
    """
    if os.path.exists(_AVATAR_IMAGE):
        return FileResponse(_AVATAR_IMAGE, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})
    return Response(content=_AVATAR_SVG, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/user")
async def get_user_avatar():
    """
    获取用户头像（返回默认头像）
    
    返回:
        dict: 包含 success 和 avatar_url 字段
    """
    return {"success": True, "avatar_url": "/static/assets/user_avatar.png"}
