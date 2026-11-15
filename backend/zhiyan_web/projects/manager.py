"""
项目管理 - 每个项目 = 一个群聊

功能：
- 创建/删除项目
- 为项目分配 Agent
- 项目数据隔离（对话历史、知识库）
- 项目切换
"""
from __future__ import annotations

import json
import os
import logging
import uuid
from typing import Dict, Optional, List
from dataclasses import dataclass, field, asdict
from datetime import datetime

from zhiyan_web.config import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.projects")

PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)


@dataclass
class Project:
    """项目 = 群聊"""
    project_id: str
    name: str
    description: str = ""
    agents: List[str] = field(default_factory=lambda: ["coordinator", "zhiyan"])
    created_at: str = ""
    updated_at: str = ""
    is_active: bool = True


class ProjectManager:
    """项目管理器"""

    def __init__(self):
        self._projects: Dict[str, Project] = {}
        self._current_project_id: Optional[str] = None
        self._load()

    def _load(self):
        """加载所有项目"""
        projects_file = os.path.join(PROJECTS_DIR, "projects.json")
        if os.path.exists(projects_file):
            try:
                with open(projects_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for pid, pdata in data.items():
                    self._projects[pid] = Project(**pdata)
                logger.info(f"已加载 {len(self._projects)} 个项目")
            except Exception as e:
                logger.error(f"加载项目失败: {e}")

        # 确保默认项目存在
        if "default" not in self._projects:
            now = datetime.now().isoformat()
            self._projects["default"] = Project(
                project_id="default",
                name="默认项目",
                description="默认项目群聊",
                agents=["coordinator", "zhiyan", "writer", "coder", "researcher"],
                created_at=now,
                updated_at=now,
            )
            self._save()
            logger.info("创建默认项目")

        # 设置当前项目
        if not self._current_project_id:
            self._current_project_id = "default"

        # 确保项目目录存在
        for pid in self._projects:
            pdir = os.path.join(PROJECTS_DIR, pid)
            os.makedirs(pdir, exist_ok=True)

    def _save(self):
        """保存所有项目"""
        projects_file = os.path.join(PROJECTS_DIR, "projects.json")
        try:
            data = {pid: asdict(p) for pid, p in self._projects.items()}
            with open(projects_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存项目失败: {e}")

    def create(self, name: str, description: str = "", agents: List[str] = None) -> Project:
        """创建新项目"""
        project_id = f"proj_{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat()
        project = Project(
            project_id=project_id,
            name=name,
            description=description,
            agents=agents or ["coordinator", "zhiyan"],
            created_at=now,
            updated_at=now,
        )
        self._projects[project_id] = project
        os.makedirs(os.path.join(PROJECTS_DIR, project_id), exist_ok=True)
        self._save()
        return project

    def delete(self, project_id: str) -> bool:
        """删除项目"""
        if project_id == "default":
            return False  # 默认项目不可删除
        if project_id in self._projects:
            del self._projects[project_id]
            self._save()
            return True
        return False

    def get(self, project_id: str) -> Optional[Project]:
        """获取项目"""
        return self._projects.get(project_id)

    def list(self) -> List[Project]:
        """列出所有项目"""
        return list(self._projects.values())

    def set_current(self, project_id: str) -> bool:
        """设置当前项目"""
        if project_id in self._projects:
            self._current_project_id = project_id
            return True
        return False

    @property
    def current(self) -> Project:
        """获取当前项目"""
        return self._projects.get(self._current_project_id, self._projects["default"])

    def add_agent(self, project_id: str, agent_id: str) -> bool:
        """为项目添加 Agent"""
        project = self._projects.get(project_id)
        if not project:
            return False
        if agent_id not in project.agents:
            project.agents.append(agent_id)
            project.updated_at = datetime.now().isoformat()
            self._save()
        return True

    def remove_agent(self, project_id: str, agent_id: str) -> bool:
        """从项目移除 Agent"""
        project = self._projects.get(project_id)
        if not project or agent_id == "coordinator":
            return False
        if agent_id in project.agents:
            project.agents.remove(agent_id)
            project.updated_at = datetime.now().isoformat()
            self._save()
        return True

    def get_project_history(self, project_id: str) -> list:
        """获取项目对话历史"""
        history_file = os.path.join(PROJECTS_DIR, project_id, "chat_history.json")
        if os.path.exists(history_file):
            try:
                with open(history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def save_project_history(self, project_id: str, messages: list):
        """保存项目对话历史"""
        history_file = os.path.join(PROJECTS_DIR, project_id, "chat_history.json")
        try:
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存项目历史失败: {e}")


# 全局单例
_manager: Optional[ProjectManager] = None


def get_project_manager() -> ProjectManager:
    global _manager
    if _manager is None:
        _manager = ProjectManager()
    return _manager