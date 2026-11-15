"""
ZhiYan Skills 插件系统（OpenClaw 兼容 · 增强版）

兼容 OpenClaw 两种技能格式：
1. Python 技能：skill.yaml / skill.json + main.py（可执行函数）
2. 指令技能：SKILL.md（frontmatter + 正文，内容型技能，供 Agent 读取调用）

增强能力：
- 启用/禁用、优先级、参数配置（config 覆盖默认值）、版本管理
- 状态持久化（skills_state.json）
- 多层安全：代码扫描、权限位、运行时监控、审核/白名单
- 完整日志：安装 / 更新 / 使用 / 安全事件
- 资源占用跟踪（执行次数、耗时、结果大小）
- 社区市场安装（openclaw / takoapi 源，安装后需审核确认）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import importlib.util
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable, Awaitable

import yaml

from . import security
from .marketplace import parse_skill_md

logger = logging.getLogger("ZhiYanWeb.Skills")


def _load_skill_contributes(skill_path: str) -> Dict[str, Any]:
    """读取技能目录下的 extension.json 声明式贡献清单。

    技能除了提供指令/函数，还可以声明自己贡献的工具分组、子代理类型、钩子，
    由 zhiyan_web/extensions/registry.py 统一加载（对齐 DSH 的声明式行配置）。
    缺省或不合法时返回空 dict，不影响技能本身可用。
    """
    path = os.path.join(skill_path, "extension.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        contributes = data.get("contributes") if isinstance(data, dict) else None
        return contributes if isinstance(contributes, dict) else {}
    except Exception as e:
        logger.debug(f"读取技能贡献清单失败 {path}: {e}")
        return {}


# ─── 数据模型 ───────────────────────────────

@dataclass
class SkillParameter:
    """技能参数定义"""
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None


@dataclass
class SkillMetadata:
    """技能元数据（兼容 OpenClaw skill.yaml / SKILL.md frontmatter）"""
    name: str
    description: str = ""
    author: str = ""
    version: str = "1.0.0"
    parameters: List[SkillParameter] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    requires_approval: bool = False
    permissions: List[str] = field(default_factory=list)
    path: str = ""
    entry_point: str = ""
    kind: str = "python"  # python | prompt
    # 何时该用这个技能（Agent Skills 约定）：注入系统提示词时给模型做选择依据
    when_to_use: str = ""
    # 声明式扩展贡献（extensions 清单，见 zhiyan_web/extensions/registry.py）
    contributes: Dict[str, Any] = field(default_factory=dict)


class Skill:
    """技能实例"""

    def __init__(self, metadata: SkillMetadata, execute_func: Callable[..., Awaitable[Any]]):
        self.metadata = metadata
        self._execute_func = execute_func
        self.loaded = True
        # 运行时/状态字段
        self.enabled: bool = True
        self.priority: int = 0
        self.config: Dict[str, Any] = {}
        self.review_status: str = "approved"     # approved / pending / rejected / whitelisted
        self.installed_from: str = "local"        # local / manual / openclaw / takoapi
        self.source_ref: str = ""
        self.security: Dict[str, Any] = {}        # {score, level, findings, timestamp}
        self.permit: Optional[security.SkillPermit] = None
        # 资源占用
        self.exec_count: int = 0
        self.total_exec_time: float = 0.0
        self.max_result_chars: int = 0
        self.last_executed_at: str = ""
        # 时间戳
        self.created_at: str = datetime.now().isoformat()
        self.updated_at: str = self.created_at

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description

    @property
    def active(self) -> bool:
        """是否对外可用：启用 且 （本地信任 或 已审核通过/白名单）"""
        if not self.enabled:
            return False
        if self.installed_from in ("local", "manual"):
            return True
        return self.review_status in ("approved", "whitelisted")

    @property
    def avg_exec_time(self) -> float:
        return round(self.total_exec_time / self.exec_count, 3) if self.exec_count else 0.0

    @property
    def security_level(self) -> str:
        return self.security.get("level", "A")

    async def execute(self, **kwargs) -> str:
        """执行技能（带运行时监控：耗时、结果大小、异常记录）"""
        start = time.time()
        try:
            effective = dict(self.config or {})
            effective.update({k: v for k, v in kwargs.items() if v is not None})
            result = await self._execute_func(**effective)
            output = result
            if isinstance(result, dict):
                output = json.dumps(result, ensure_ascii=False, indent=2)
            output = str(output)
        except Exception as e:
            self.exec_count += 1
            self.total_exec_time += time.time() - start
            self.last_executed_at = datetime.now().isoformat()
            logger.error(f"技能执行失败 {self.name}: {e}")
            return f"错误: {str(e)}"

        self.exec_count += 1
        self.total_exec_time += time.time() - start
        self.last_executed_at = datetime.now().isoformat()
        self.max_result_chars = max(self.max_result_chars, len(output))
        return output

    def to_openai_tool(self) -> Dict:
        """转换为 OpenAI 工具格式（仅使用声明参数）"""
        properties = {}
        required = []
        for param in self.metadata.parameters:
            prop = {"type": param.type, "description": param.description}
            if param.default is not None:
                prop["default"] = param.default
            if self.config and param.name in self.config:
                prop["default"] = self.config[param.name]
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def to_dict(self) -> Dict:
        """供前端展示的完整状态"""
        permit = self.permit.to_dict() if self.permit else []
        return {
            "name": self.name,
            "description": self.metadata.description,
            "author": self.metadata.author,
            "version": self.metadata.version,
            "tags": self.metadata.tags,
            "kind": self.metadata.kind,
            "path": self.metadata.path,
            "enabled": self.enabled,
            "priority": self.priority,
            "active": self.active,
            "requires_approval": self.metadata.requires_approval,
            "review_status": self.review_status,
            "installed_from": self.installed_from,
            "source_ref": self.source_ref,
            "parameters": [
                {"name": p.name, "type": p.type, "description": p.description,
                 "required": p.required, "default": p.default}
                for p in self.metadata.parameters
            ],
            "config": self.config,
            "security": self.security,
            "security_level": self.security_level,
            "permissions": self.metadata.permissions or (permit if isinstance(permit, list) else []),
            "resource": {
                "exec_count": self.exec_count,
                "avg_exec_time": self.avg_exec_time,
                "max_result_chars": self.max_result_chars,
                "last_executed_at": self.last_executed_at,
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SkillManager:
    """技能管理器（单例）"""

    _instance: Optional['SkillManager'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self._skills: Dict[str, Skill] = {}
        self._skills_dir: str = ""
        self._loaded = False
        self._state_file: str = ""
        self._log_file: str = ""
        self._meta = {
            security.FIRST_USE_FLAG: False,   # 是否已确认社区安全风险提示
            "whitelist": {},                  # {skill_name: reason}
        }

    # ════════ 初始化/持久化 ════════

    def set_skills_dir(self, path: str) -> None:
        """设置技能目录并建立状态/日志文件"""
        self._skills_dir = path
        self._loaded = False
        os.makedirs(path, exist_ok=True)
        # 状态与日志保存在 DATA_DIR 下
        data_dir = self._find_data_dir()
        logs_dir = os.path.join(data_dir, "logs")
        self._state_file = os.path.join(data_dir, "skills_state.json")
        self._log_file = os.path.join(data_dir, "logs", "skills.log")
        os.makedirs(os.path.dirname(self._log_file), exist_ok=True)
        self._load_state()

    def _find_data_dir(self) -> str:
        """定位数据目录（DATA_DIR/data）"""
        try:
            from zhiyan_web.config.settings import DATA_DIR
            return DATA_DIR
        except Exception:
            return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

    def _load_state(self) -> None:
        """加载持久化状态（meta + 每个技能状态）"""
        if not self._state_file or not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                state = json.load(f) or {}
            self._meta.update({k: v for k, v in state.get("__meta__", {}).items() if isinstance(k, str)})
            for sk in self._skills.values():
                s = state.get(sk.name)
                if not isinstance(s, dict):
                    continue
                sk.enabled = bool(s.get("enabled", True))
                sk.priority = int(s.get("priority", 0) or 0)
                sk.config = s.get("config") or {}
                sk.review_status = s.get("review_status", "approved" if sk.installed_from in ("local", "manual") else "pending")
                sk.installed_from = s.get("installed_from", sk.installed_from)
                sk.source_ref = s.get("source_ref", "")
                sk.security = s.get("security") or sk.security
                sk.permit = security_from_state(s.get("permissions"))
                sk.exec_count = int(s.get("exec_count", 0) or 0)
                sk.total_exec_time = float(s.get("total_exec_time", 0) or 0)
                sk.max_result_chars = int(s.get("max_result_chars", 0) or 0)
                sk.last_executed_at = s.get("last_executed_at", "")
                sk.created_at = s.get("created_at", sk.created_at)
                sk.updated_at = s.get("updated_at", sk.updated_at)
        except Exception as e:
            logger.warning(f"加载技能状态失败: {e}")

    def save_state(self) -> None:
        """保存持久化状态"""
        if not self._state_file:
            return
        state = {"__meta__": self._meta, "skills": {}}
        for sk in self._skills.values():
            state["skills"][sk.name] = {
                "enabled": sk.enabled, "priority": sk.priority, "config": sk.config,
                "review_status": sk.review_status, "installed_from": sk.installed_from,
                "source_ref": sk.source_ref, "security": sk.security,
                "permissions": sk.permit.to_dict() if sk.permit else [],
                "exec_count": sk.exec_count, "total_exec_time": sk.total_exec_time,
                "max_result_chars": sk.max_result_chars, "last_executed_at": sk.last_executed_at,
                "created_at": sk.created_at, "updated_at": sk.updated_at,
            }
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存技能状态失败: {e}")

    def log_event(self, event_type: str, skill_name: str, data: Any = None) -> None:
        """记录技能事件（install/update/use/security/enable/disable/priority/config）"""
        if not self._log_file:
            return
        try:
            record = {
                "ts": datetime.now().isoformat(),
                "event": event_type,
                "skill": skill_name,
                "data": data,
            }
            os.makedirs(os.path.dirname(self._log_file), exist_ok=True)
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"写入技能日志失败: {e}")

    def get_logs(self, limit: int = 200, skill: str = "") -> List[Dict]:
        """读取技能日志"""
        if not self._log_file or not os.path.exists(self._log_file):
            return []
        try:
            lines = []
            with open(self._log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if skill and rec.get("skill") != skill:
                            continue
                        lines.append(rec)
                    except Exception:
                        continue
            return lines[-limit:]
        except Exception:
            return []

    @property
    def community_consent(self) -> bool:
        return bool(self._meta.get(security.FIRST_USE_FLAG))

    def set_community_consent(self, value: bool = True) -> None:
        self._meta[security.FIRST_USE_FLAG] = bool(value)
        self.save_state()

    # ════════ 加载 ════════

    async def load_all(self) -> Dict[str, Any]:
        """加载所有技能"""
        if not self._skills_dir or not os.path.exists(self._skills_dir):
            return {"loaded": [], "failed": {}}
        loaded = []
        failed = {}
        for entry in os.listdir(self._skills_dir):
            skill_path = os.path.join(self._skills_dir, entry)
            if not os.path.isdir(skill_path):
                continue
            try:
                skill = self._load_skill(skill_path)
                if skill:
                    existed = skill.name in self._skills
                    self._skills[skill.name] = skill
                    self._apply_security(skill)
                    loaded.append(skill.name)
                    if not existed:
                        self.log_event("install", skill.name, {"source": skill.installed_from})
                    logger.info(f"加载技能: {skill.name} v{skill.metadata.version} [{skill.metadata.kind}]")
            except Exception as e:
                failed[entry] = str(e)
                logger.warning(f"加载技能失败 {entry}: {e}")

        # 加载状态（必须在技能对象建立后）
        self._load_state()
        self._loaded = True
        # 将活跃技能注册到 Agent 工具系统，确保可被读取和调用
        try:
            registered = self.register_to_func_tool_manager()
            logger.info(f"技能已注册到 Agent 工具系统: {registered} 个")
        except Exception as e:
            logger.warning(f"技能注册到工具系统失败: {e}")
        logger.info(f"技能加载完成: {len(loaded)} 个成功, {len(failed)} 个失败")
        return {"loaded": loaded, "failed": failed}

    def _apply_security(self, skill: Skill) -> None:
        """对已加载技能执行安全扫描（若尚未扫描）"""
        if skill.security and skill.security.get("timestamp"):
            # 已有扫描结果，仅重建权限
            skill.permit = security.SkillPermit()
            for p in skill.metadata.permissions:
                skill.permit.grant(p)
            return
        scan = security.scan_skill_code(skill.metadata.path or skill.metadata.entry_point or "")
        skill.security = scan
        skill.permit = security.SkillPermit.from_scan(scan.get("findings", []))
        for p in skill.metadata.permissions:
            skill.permit.grant(p)
        if scan.get("level") in ("C", "D"):
            self.log_event("security", skill.name, {"level": scan["level"], "score": scan["score"]})

    def _load_skill(self, skill_path: str) -> Optional[Skill]:
        """加载单个技能目录（支持 python / prompt 两种）"""
        # SKILL.md 指令型技能
        skill_md = os.path.join(skill_path, "SKILL.md")
        if os.path.exists(skill_md) and not os.path.exists(os.path.join(skill_path, "skill.yaml")) and not os.path.exists(os.path.join(skill_path, "skill.yml")):
            try:
                with open(skill_md, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                meta = parse_skill_md(content)
                if not meta["name"]:
                    return None
                md = SkillMetadata(
                    name=meta["name"], description=meta["description"],
                    author=meta.get("author", ""), version=meta.get("version", "1.0.0"),
                    tags=meta.get("tags", []), path=skill_path,
                    entry_point=skill_md, kind="prompt",
                    when_to_use=meta.get("when_to_use", ""),
                    contributes=_load_skill_contributes(skill_path),
                )
                async def _prompt_exec(**kwargs):
                    return meta["body"]
                return Skill(metadata=md, execute_func=_prompt_exec)
            except Exception as e:
                logger.warning(f"解析 SKILL.md 失败 {skill_path}: {e}")
                return None

        # 传统 Python 技能
        metadata = self._load_metadata(skill_path)
        if not metadata:
            return None
        entry_file = self._find_entry_file(skill_path)
        if not entry_file:
            logger.warning(f"技能 {metadata.name} 未找到入口文件")
            return None
        execute_func = self._load_execute_func(entry_file, metadata.name)
        if not execute_func:
            return None
        metadata.path = skill_path
        metadata.entry_point = entry_file
        metadata.kind = "python"
        return Skill(metadata=metadata, execute_func=execute_func)

    def _load_metadata(self, skill_path: str) -> Optional[SkillMetadata]:
        for fname in ("skill.yaml", "skill.json", "skill.yml"):
            fpath = os.path.join(skill_path, fname)
            if os.path.exists(fpath):
                return self._parse_metadata_file(fpath)
        return None

    def _parse_metadata_file(self, path: str) -> Optional[SkillMetadata]:
        try:
            if path.endswith(".json"):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                return None
            params = [SkillParameter(
                name=p.get("name", ""), type=p.get("type", "string"),
                description=p.get("description", ""), required=p.get("required", False),
                default=p.get("default", None),
            ) for p in (data.get("parameters") or []) if isinstance(p, dict)]
            perms = data.get("permissions") or data.get("requires_permissions") or []
            return SkillMetadata(
                name=data.get("name", ""), description=data.get("description", ""),
                author=data.get("author", ""), version=str(data.get("version", "1.0.0")),
                parameters=params, tags=data.get("tags", []),
                requires_approval=data.get("requires_approval", False),
                permissions=list(perms) if isinstance(perms, list) else [],
                when_to_use=(data.get("when-to-use") or data.get("when_to_use")
                             or data.get("description", "")),
                contributes=(data.get("contributes")
                             if isinstance(data.get("contributes"), dict) else {}),
            )
        except Exception as e:
            logger.warning(f"解析元数据失败 {path}: {e}")
            return None

    def _find_entry_file(self, skill_path: str) -> Optional[str]:
        for fname in ("main.py", f"{os.path.basename(skill_path)}.py", "__init__.py"):
            fpath = os.path.join(skill_path, fname)
            if os.path.exists(fpath):
                return fpath
        return None

    def _load_execute_func(self, entry_file: str, skill_name: str) -> Optional[Callable]:
        try:
            spec = importlib.util.spec_from_file_location(f"zhiyan_skill_{skill_name}_{int(time.time())}", entry_file)
            if not spec or not spec.loader:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "execute"):
                func = module.execute
                if asyncio.iscoroutinefunction(func):
                    return func
                async def _sync_wrapper(**kw):
                    return func(**kw)
                return _sync_wrapper
            if hasattr(module, "Skill"):
                inst = module.Skill()
                func = inst.execute
                if asyncio.iscoroutinefunction(func):
                    return func
                async def _sync_wrapper(**kw):
                    return inst.execute(**kw)
                return _sync_wrapper
            logger.warning(f"技能 {skill_name} 未找到 execute 函数或 Skill 类")
            return None
        except Exception as e:
            logger.error(f"加载技能执行函数失败 {skill_name}: {e}")
            return None

    # ════════ 查询 ════════

    def get_skill(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def get_all_skills(self) -> List[Skill]:
        return list(self._skills.values())

    def get_skill_list(self, include_inactive: bool = True) -> List[Dict]:
        out = []
        for s in self._skills.values():
            d = s.to_dict()
            if not include_inactive and not s.active:
                continue
            out.append(d)
        # 按优先级降序（优先级越高越靠前）
        out.sort(key=lambda x: (-x.get("priority", 0), x.get("name", "")))
        return out

    # ════════ 控制 ════════

    def set_enabled(self, name: str, enabled: bool) -> Dict:
        sk = self.get_skill(name)
        if not sk:
            return {"success": False, "error": f"技能 '{name}' 不存在"}
        sk.enabled = bool(enabled)
        sk.updated_at = datetime.now().isoformat()
        self.save_state()
        self.log_event("enable" if sk.enabled else "disable", name)
        self._sync_registration()
        return {"success": True, "enabled": sk.enabled, "active": sk.active}

    def set_priority(self, name: str, priority: int) -> Dict:
        sk = self.get_skill(name)
        if not sk:
            return {"success": False, "error": f"技能 '{name}' 不存在"}
        sk.priority = int(priority or 0)
        sk.updated_at = datetime.now().isoformat()
        self.save_state()
        self.log_event("priority", name, {"priority": sk.priority})
        return {"success": True, "priority": sk.priority}

    def set_config(self, name: str, config: Dict) -> Dict:
        sk = self.get_skill(name)
        if not sk:
            return {"success": False, "error": f"技能 '{name}' 不存在"}
        sk.config = {k: v for k, v in (config or {}).items()}
        sk.updated_at = datetime.now().isoformat()
        self.save_state()
        self.log_event("config", name, {"params": list(sk.config.keys())})
        return {"success": True, "config": sk.config}

    def set_review_status(self, name: str, status: str) -> Dict:
        sk = self.get_skill(name)
        if not sk:
            return {"success": False, "error": f"技能 '{name}' 不存在"}
        if status not in ("approved", "rejected", "pending", "whitelisted"):
            return {"success": False, "error": f"无效状态: {status}"}
        sk.review_status = status
        sk.updated_at = datetime.now().isoformat()
        self.save_state()
        self.log_event("review", name, {"status": status})
        self._sync_registration()
        return {"success": True, "review_status": sk.review_status, "active": sk.active}

    def set_whitelist(self, name: str, whitelist: bool, reason: str = "") -> Dict:
        sk = self.get_skill(name)
        if not sk:
            return {"success": False, "error": f"技能 '{name}' 不存在"}
        if whitelist:
            sk.review_status = "whitelisted"
            self._meta.setdefault("whitelist", {})[name] = reason or "手动加入白名单"
        else:
            sk.review_status = "pending"
            self._meta.get("whitelist", {}).pop(name, None)
        sk.updated_at = datetime.now().isoformat()
        self.save_state()
        self.log_event("whitelist", name, {"whitelist": whitelist, "reason": reason})
        self._sync_registration()
        return {"success": True, "whitelisted": whitelist, "review_status": sk.review_status}

    def rescan(self, name: str) -> Dict:
        sk = self.get_skill(name)
        if not sk:
            return {"success": False, "error": f"技能 '{name}' 不存在"}
        scan = security.scan_skill_code(sk.metadata.path or sk.metadata.entry_point or "")
        sk.security = scan
        sk.permit = security.SkillPermit.from_scan(scan.get("findings", []))
        for p in sk.metadata.permissions:
            sk.permit.grant(p)
        sk.updated_at = datetime.now().isoformat()
        self.save_state()
        self.log_event("security_scan", name, {"level": scan.get("level"), "score": scan.get("score")})
        return {"success": True, "security": sk.security, "permissions": self._safe_perms(sk)}

    def _safe_perms(self, sk) -> List:
        try:
            return sk.permit.to_dict() if sk.permit else []
        except Exception:
            return []

    def uninstall(self, name: str) -> Dict:
        sk = self.get_skill(name)
        if not sk:
            return {"success": False, "error": f"技能 '{name}' 不存在"}
        import shutil as _sh
        path = sk.metadata.path
        if path and os.path.isdir(path):
            try:
                _sh.rmtree(path)
            except Exception as e:
                return {"success": False, "error": f"删除技能目录失败: {e}"}
        self._skills.pop(name, None)
        self.save_state()
        self.log_event("uninstall", name)
        self._sync_registration()
        return {"success": True}

    # ════════ 执行 ════════

    async def execute_skill(self, name: str, **kwargs) -> str:
        skill = self.get_skill(name)
        if not skill:
            return f"错误: 技能 '{name}' 不存在"
        if not skill.enabled:
            return f"错误: 技能 '{name}' 已被禁用"
        if not skill.active:
            return f"错误: 技能 '{name}' 需要审核通过后才能启用（当前状态: {skill.review_status}）"
        if skill.metadata.requires_approval:
            self.log_event("approval_required", name)
        self.log_event("use", name)
        result = await skill.execute(**kwargs)
        self.save_state()
        return result

    # ════════ 安装（市场/手动） ════════

    def install_skill_dir(self, name: str, source_dir: str, installed_from: str = "manual",
                          source_ref: str = "", overwrite: bool = True) -> Dict:
        """从已有目录安装/更新技能到技能目录"""
        import shutil as _sh
        if not self._skills_dir or not os.path.isdir(source_dir):
            return {"success": False, "error": "技能目录不存在"}
        target = os.path.join(self._skills_dir, name)
        try:
            if os.path.exists(target):
                if not overwrite:
                    return {"success": False, "error": f"技能 '{name}' 已存在（如需覆盖请启用覆盖）"}
                _sh.rmtree(target)
            _sh.copytree(source_dir, target)
        except Exception as e:
            return {"success": False, "error": f"复制技能失败: {e}"}

        skill = self._load_skill(target)
        if not skill:
            _sh.rmtree(target, ignore_errors=True)
            return {"success": False, "error": "安装的技能格式无效（缺少 SKILL.md / skill.yaml + main.py）"}

        existed = skill.name in self._skills
        skill.installed_from = installed_from
        skill.source_ref = source_ref
        if installed_from in ("openclaw", "takoapi"):
            skill.review_status = "pending"  # 社区技能默认待审核
        self._skills[skill.name] = skill
        self._apply_security(skill)
        self.set_skills_dir(self._skills_dir)  # 确保状态文件就绪
        self.save_state()
        self.log_event("update" if existed else "install", skill.name,
                       {"source": installed_from, "source_ref": source_ref})
        self._sync_registration()
        return {"success": True, "name": skill.name, "exists": existed,
                "security": skill.security, "review_status": skill.review_status,
                "config": skill.to_dict()}

    def install_prompt_skill(self, name: str, content: str, meta: Dict, installed_from: str = "manual",
                             source_ref: str = "") -> Dict:
        """以 SKILL.md 形式安装指令型技能"""
        import shutil as _sh
        if not self._skills_dir:
            return {"success": False, "error": "技能目录未设置"}
        safe_name = "".join(c for c in name if c.isalnum() or c in "-_") or "skill"
        target = os.path.join(self._skills_dir, safe_name)
        os.makedirs(target, exist_ok=True)
        md_content = self._build_skill_md(safe_name, content, meta)
        try:
            with open(os.path.join(target, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(md_content)
        except Exception as e:
            return {"success": False, "error": f"写入 SKILL.md 失败: {e}"}

        skill = self._load_skill(target)
        if not skill:
            _sh.rmtree(target, ignore_errors=True)
            return {"success": False, "error": "技能格式无效"}

        existed = skill.name in self._skills
        skill.installed_from = installed_from
        skill.source_ref = source_ref
        if installed_from in ("openclaw", "takoapi"):
            skill.review_status = "pending"
        self._skills[skill.name] = skill
        self._apply_security(skill)
        self.save_state()
        self.log_event("update" if existed else "install", skill.name,
                       {"source": installed_from, "format": "SKILL.md"})
        self._sync_registration()
        return {"success": True, "name": skill.name, "exists": existed,
                "security": skill.security, "review_status": skill.review_status}

    def _build_skill_md(self, name: str, content: str, meta: Dict) -> str:
        front = [
            "---",
            f"name: {meta.get('name', name)}",
            f"description: {meta.get('description', '')}",
        ]
        if meta.get("author"):
            front.append(f"author: {meta['author']}")
        if meta.get("version"):
            front.append(f"version: {meta['version']}")
        if meta.get("tags"):
            front.append(f"tags: {', '.join(meta['tags']) if isinstance(meta['tags'], list) else meta['tags']}")
        front.append("---")
        body = content
        # 若 content 里已含 frontmatter，去掉旧的
        stripped = content.lstrip("\ufeff \t\r\n")
        if stripped.startswith("---"):
            end = stripped.find("\n---", 3)
            if end != -1:
                body = stripped[end + 4:].strip()
        return "\n".join(front) + "\n\n" + body + "\n"

    # ════════ 注册到 Agent 工具系统 ════════

    def _sync_registration(self) -> None:
        """重新同步启用且活跃的技能到 FunctionToolManager"""
        try:
            self.register_to_func_tool_manager()
        except Exception as e:
            logger.warning(f"同步技能注册失败: {e}")

    def register_to_func_tool_manager(self) -> int:
        try:
            from zhiyan_web.core import func_tool_manager
        except (ImportError, AttributeError):
            try:
                from zhiyan_web.provider.func_tool_manager import get_func_tool_manager
                func_tool_manager = get_func_tool_manager()
            except Exception:
                logger.warning("FunctionToolManager 不可用")
                return 0
        if func_tool_manager is None:
            return 0

        active = {s.name for s in self._skills.values() if s.active}
        # 先移除本管理器之前注册的技能，实现「启用/禁用热同步」
        self._unregister_from_func_tool_manager(func_tool_manager, active)

        count = 0
        for skill in self._skills.values():
            if not skill.active:
                continue
            # 指令型技能（SKILL.md）不再逐个注册成工具：它的正文由 load_skill
            # 按需拉取（渐进式披露）。若每个技能都注册一个工具，技能越多工具面
            # 越大，prompt 里光是工具描述就会显著膨胀。
            if getattr(skill.metadata, "kind", "python") == "prompt":
                continue
            try:
                func_args = [{"name": p.name, "type": p.type, "description": p.description}
                             for p in skill.metadata.parameters]
                target = skill

                async def _handler(_target=target, **kwargs):
                    return await _target.execute(**kwargs)

                func_tool_manager.add_func(
                    name=skill.name, func_args=func_args,
                    desc=skill.description, handler=_handler,
                    group="skills",
                )
                count += 1
                logger.debug(f"技能已注册到工具系统: {skill.name}")
            except Exception as e:
                logger.warning(f"注册技能失败 {skill.name}: {e}")
        logger.info(f"已将 {count} 个函数型技能注册到 FunctionToolManager"
                    f"（指令型技能由 load_skill 按需加载）")
        return count

    def _unregister_from_func_tool_manager(self, ftm, active: set) -> None:
        """移除已注册技能（提高优先级时也要保证一致性）"""
        remove = getattr(ftm, "remove_func", None)
        if not remove:
            return
        for skill in self._skills.values():
            if not skill.active and skill.name not in active:
                try:
                    remove(skill.name)
                except Exception:
                    pass
        # 也删除已卸载技能的旧注册
        known = {s.name for s in self._skills.values()}
        # FunctionToolManager 无枚举接口时跳过

    def to_openai_tools(self) -> List[Dict]:
        return [s.to_openai_tool() for s in self._skills.values() if s.active]

    # ════════ 统计/元信息 ════════

    def get_stats(self) -> Dict:
        skill_dicts = [s.to_dict() for s in self._skills.values()]
        total = len(skill_dicts)
        active = sum(1 for s in skill_dicts if s.get("active"))
        enabled = sum(1 for s in skill_dicts if s.get("enabled"))
        level_counter = {}
        for s in skill_dicts:
            lv = s.get("security_level", "A")
            level_counter[lv] = level_counter.get(lv, 0) + 1
        total_exec = sum(s.get("resource", {}).get("exec_count", 0) for s in skill_dicts)
        return {
            "total": total, "active": active, "enabled": enabled,
            "disabled": total - enabled,
            "pending_review": sum(1 for s in skill_dicts if s.get("review_status") == "pending"),
            "whitelisted": sum(1 for s in skill_dicts if s.get("review_status") == "whitelisted"),
            "security_levels": level_counter,
            "total_executions": total_exec,
            "community_consent": self.community_consent,
        }

    def get_meta_status(self) -> Dict:
        return {"community_first_use_confirmed": self.community_consent,
                "whitelist": self._meta.get("whitelist", {})}


# 辅助
def security_from_state(perms) -> Optional[security.SkillPermit]:
    p = security.SkillPermit()
    if isinstance(perms, list):
        for item in perms:
            p.grant(item)
    return p


# 全局单例
_skill_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
    return _skill_manager