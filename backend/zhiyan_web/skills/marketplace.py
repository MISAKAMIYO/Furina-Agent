"""
OpenClawSkills 社区集成模块

对接开源 openclawskills 社区作为技能获取来源，支持多个市场：
1. openclaw 社区公开 API（xiaolongxia 开源技能管理器）
   - GET /api/public/skills      列表/搜索
   - GET /api/public/skills/<id>/download   下载技能（content 返回 SKILL.md 文本）
   - 注意：该源为个人 FRP 隧道，可能离线（404），上层需自动回退到备用源
2. takoapi 开源 OpenClaw 技能市场（备用源，无需密钥）
   - GET /api/agent?format=json  搜索（返回含 github 仓库地址）
   - GET /api/skills/{slug}      详情（githubUrl）
   - SKILL.md 内容托管在 GitHub 仓库，需从 raw.githubusercontent.com 拉取

能力：自动检索、预览（仅元数据/说明）、安装（下载并保存到技能目录）
安全：下载后不自动激活，由上层进行代码扫描 + 审核确认
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional, Any

import httpx

from zhiyan_web.github_proxy.routers import github_fetch

logger = logging.getLogger("ZhiYanWeb.Skills.Marketplace")

# 用户首次使用社区获取技能需要确认安全风险的标记（持久化在 skills 状态的 __meta__ 里）
FIRST_USE_FLAG = "community_first_use_confirmed"


class OpenClawSkillsClient:
    """开源 openclawskills 社区客户端（含多市场源）"""

    # 主要市场：开源 xiaolongxia 技能管理器（http，需容忍 /api 前缀）
    OPENCLAW_API = "http://xiaolongxia-skill-manager.frp.ptteng.com"
    # 备用：takoapi OpenClaw 技能市场
    TAKO_API = "https://takoapi.com"

    def __init__(self, timeout: float = 12.0):
        self._timeout = timeout

    async def _fetch(self, url: str, **kwargs) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
            resp = await client.get(url, **kwargs)
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                return {}

    # ═══ OpenClaw 社区 ═══

    async def search_openclaw(self, keyword: str = "", limit: int = 50) -> List[Dict]:
        """检索 openclaw 社区技能"""
        url = f"{self.OPENCLAW_API}/api/public/skills"
        params = {}
        if keyword:
            params["search"] = keyword
        try:
            data = await self._fetch(url, params=params)
        except Exception as e:
            logger.warning(f"openclaw 社区检索失败: {e}")
            return []
        skills = data.get("skills") if isinstance(data, dict) else data
        if not isinstance(skills, list):
            return []
        out = []
        for s in skills[:limit]:
            if not isinstance(s, dict):
                continue
            sid = s.get("id") or s.get("_id") or s.get("slug") or s.get("name")
            out.append({
                "id": str(sid),
                "name": s.get("name") or s.get("title", ""),
                "description": s.get("description", "") or s.get("summary", ""),
                "author": s.get("author", "openclaw 社区"),
                "category": s.get("category", ""),
                "version": s.get("version", "1.0.0"),
                "downloads": s.get("downloads") or s.get("download_count", 0),
                "rating": s.get("rating") or s.get("points", 0),
                "source": "openclaw",
            })
        return out

    async def download_openclaw(self, skill_id: str) -> Optional[str]:
        """下载 openclaw 社区技能的 SKILL.md 文本内容"""
        url = f"{self.OPENCLAW_API}/api/public/skills/{skill_id}/download"
        try:
            data = await self._fetch(url)
        except Exception as e:
            logger.warning(f"openclaw 社区下载失败({skill_id}): {e}")
            return None
        content = data.get("content") if isinstance(data, dict) else None
        if not content and isinstance(data, dict):
            content = data.get("skill")
        if not content and isinstance(data, dict):
            # 可能直接返回 skill 对象内的 content
            sk = data.get("skill") or {}
            content = sk.get("content") if isinstance(sk, dict) else None
        if not content:
            return None
        # content 可能是 JSON 字符串
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and parsed.get("content"):
                    content = parsed["content"]
                elif isinstance(parsed, str):
                    content = parsed
            except Exception:
                pass
        return content if isinstance(content, str) else None

    # ═══ TakoAPI 备用市场 ═══

    async def search_takoapi(self, query: str = "", limit: int = 50) -> List[Dict]:
        """检索 takoapi 市场"""
        url = f"{self.TAKO_API}/api/agent"
        try:
            data = await self._fetch(url, params={"q": query, "format": "json"})
        except Exception as e:
            logger.warning(f"takoapi 检索失败: {e}")
            return []
        results = None
        if isinstance(data, dict):
            results = data.get("results") or data.get("skills") or data.get("data") or []
        elif isinstance(data, list):
            results = data
        if not isinstance(results, list):
            return []
        out = []
        for s in results[:limit]:
            if not isinstance(s, dict):
                continue
            slug = s.get("slug") or s.get("name") or s.get("id")
            out.append({
                "id": str(slug), "name": s.get("name", ""),
                "description": s.get("description", ""),
                "author": s.get("author") or s.get("publisher", ""),
                "category": s.get("category", ""),
                "version": s.get("version", "1.0.0"),
                "downloads": s.get("downloads", 0),
                "rating": s.get("rating", 0),
                "source": "takoapi",
            })
        return out

    async def download_takoapi(self, slug: str) -> Optional[str]:
        """下载 takoapi 技能。

        takoapi 技能内容托管在 GitHub（SKILL.md 位于仓库根目录）：
        1. 先通过详情接口获取 githubUrl（部分返回 content 字段直接兜底）
        2. 再从 GitHub raw 拉取 SKILL.md 文本
        """
        github_url = None
        try:
            data = await self._fetch(f"{self.TAKO_API}/api/skills/{slug}")
            if isinstance(data, dict):
                github_url = data.get("githubUrl") or data.get("github")
                # 兼容直接返回内容字段的情况
                content = data.get("content") or data.get("skill") or data.get("instructions")
                if isinstance(content, dict):
                    content = content.get("content")
                if isinstance(content, list):
                    content = "\n".join(str(x) for x in content)
                if isinstance(content, str) and content.strip():
                    return content
        except Exception as e:
            logger.warning(f"takoapi 详情获取失败({slug}): {e}")

        # 从 GitHub raw 下载 SKILL.md（HEAD/main/master 依次尝试）
        if github_url:
            content = await self._fetch_github_skill(github_url)
            if content:
                return content
        return None

    async def _fetch_github_skill(self, github_url: str) -> Optional[str]:
        """从 GitHub 仓库根目录拉取 SKILL.md 文本（优先走 GitHub 加速代理）"""
        if not github_url:
            return None
        m = re.match(r"https?://github\.com/([^/]+)/([^/]+)", github_url)
        if not m:
            return None
        owner, repo = m.group(1), m.group(2).rstrip("/")
        for branch in ("HEAD", "main", "master"):
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/SKILL.md"
            try:
                resp = await github_fetch(raw_url, timeout=self._timeout)
                if resp is not None and resp.status_code == 200 and resp.text.strip():
                    return resp.text
            except Exception as e:
                logger.debug(f"GitHub raw 获取失败({raw_url}): {e}")
        return None


def parse_skill_md(content: str) -> Dict[str, Any]:
    """解析 SKILL.md：提取 frontmatter（name/description/when-to-use/version/author/tags）+ 正文

    Returns:
        {name, description, when_to_use, version, author, tags, body}
    """
    front = {}
    body = content
    stripped = content.lstrip("\ufeff \t\r\n")
    if stripped.startswith("---"):
        end = stripped.find("\n---", 3)
        if end != -1:
            raw_front = stripped[3:end].strip()
            body = stripped[end + 4:].strip()
            for line in raw_front.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    front[k.strip().strip('"').strip("'")] = v.strip().strip('"').strip("'")
    m = front
    tags = m.get("tags", "")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "name": (m.get("name") or _slug_from_body(body)).strip(),
        "description": m.get("description", ""),
        # Agent Skills 约定里 when-to-use 决定模型何时该加载这个技能，
        # 缺失时按 DSH / OpenClaw 的写法回退到 description。
        "when_to_use": (m.get("when-to-use") or m.get("when_to_use")
                        or m.get("description", "")),
        "version": m.get("version", "1.0.0"),
        "author": m.get("author", ""),
        "tags": tags if isinstance(tags, list) else [],
        "body": body,
    }


def _slug_from_body(body: str) -> str:
    """无法解析名称时从正文首行推断"""
    first = ""
    for line in (body or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            first = line
            break
    return first.lower().replace(" ", "-")[:30] or "untitled-skill"