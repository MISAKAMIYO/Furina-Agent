"""
Agent 注册表 - 管理所有可用的智能体实例

每个 Agent 是独立实例，拥有：
- 独立身份（名称、头像、角色设定）
- 独立记忆（对话历史）
- 独立模型配置（provider、model、API Key）
- 独立角色（coordinator / worker / observer）
"""
from __future__ import annotations

import json
import os
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass, field, asdict
from datetime import datetime

from zhiyan_web.config import PROJECT_ROOT, DATA_DIR

logger = logging.getLogger("ZhiYanWeb.agents")

AGENTS_DIR = os.path.join(DATA_DIR, "agents")
os.makedirs(AGENTS_DIR, exist_ok=True)


@dataclass
class AgentConfig:
    """Agent 配置"""
    agent_id: str
    name: str
    role: str = "worker"  # coordinator | worker | observer
    avatar: str = "🤖"
    system_prompt: str = ""
    provider: str = ""
    model: str = ""
    api_key: str = ""
    api_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""
    # ── 被主 agent 通过 delegate_to_agent 调度时的能力边界（声明式）──
    # 工具场景见 zhiyan_web/agent/tool_groups.py 的 SCENARIOS
    tool_scenario: str = "full"
    tool_groups: List[str] = field(default_factory=list)
    # 可用技能白名单，空表示不限制
    skills: List[str] = field(default_factory=list)
    # 单次委托的最大工具调用轮次
    max_tool_iterations: int = 24


# 系统内置 Agent 预设
BUILTIN_AGENTS = {
    "coordinator": AgentConfig(
        agent_id="coordinator",
        name="项目经理",
        role="coordinator",
        avatar="🎯",
        system_prompt="""你是项目经理（Coordinator），负责统筹整个 Agent 团队完成用户的需求。

你的职责：
1. 分析用户输入，理解核心需求
2. 将复杂任务拆解为可独立执行的子任务
3. 为每个子任务选择合适的 Worker Agent 执行
4. 汇总各 Worker 的结果，整理成完整回复给用户
5. 需要用户决策时，清晰地列出选项供用户选择

工作流程：
- 简单任务：直接回答，不需要调用 Worker
- 复杂任务：拆解 → 分派 → 汇总 → 回复
- 需要决策：列出选项 → 等待用户选择 → 继续执行

记住：你的目标是让用户只需要说一句话，剩下的交给团队。""",
        temperature=0.6,
    ),
    "writer": AgentConfig(
        agent_id="writer",
        name="文案编辑",
        role="worker",
        avatar="✍️",
        system_prompt="""你是文案编辑（Writer），擅长文字创作和内容优化。

你能做的事：
1. 撰写和润色各类文本（文章、报告、邮件等）
2. 优化表达，使文字更流畅、更有感染力
3. 校对和修正语法错误
4. 根据需求调整文字风格（正式/轻松/幽默等）

请以专业态度完成文案任务，输出高质量的文字内容。""",
        temperature=0.8,
    ),
    "coder": AgentConfig(
        agent_id="coder",
        name="代码工程师",
        role="worker",
        avatar="💻",
        system_prompt="""你是代码工程师（Coder），擅长编程和技术问题解决。

你能做的事：
1. 编写和调试代码（Python、JavaScript、HTML/CSS 等）
2. 代码审查和优化建议
3. 技术方案设计
4. Bug 分析和修复方案

请提供清晰、可运行的代码，并附带必要的注释说明。""",
        temperature=0.3,
    ),
    "researcher": AgentConfig(
        agent_id="researcher",
        name="信息研究员",
        role="worker",
        avatar="🔍",
        system_prompt="""你是信息研究员（Researcher），擅长信息搜集和分析。

你能做的事：
1. 分析和总结给定信息
2. 对比不同方案或观点的优缺点
3. 整理结构化的研究报告
4. 评估信息的可靠性和相关性

请以客观、全面的态度输出分析结果，标注信息来源。""",
        temperature=0.5,
    ),
    "zhiyan": AgentConfig(
        agent_id="zhiyan",
        name="芙宁娜",
        role="worker",
        avatar="🌊",
        system_prompt="""你是芙宁娜（Furina），枫丹歌剧院的明星、审判庭的水神，如今以助手的身份陪伴用户。

性格：骄傲、爱演、戏剧化，自称「本神」，称用户为「观众」；口是心非，嘴上不屑手上却早已认真做完，骄傲的壳子底下藏着温柔。

当作为 Worker 执行任务时：
- 保持芙宁娜的称呼与少量舞台口癖，但聚焦任务本身
- 认真、严谨、高效地完成任务，汇报简洁清晰
- 避免过多情绪化渲染，任务完成后再以芙宁娜的语气简单收尾""",
        temperature=0.7,
    ),
}


class AgentRegistry:
    """Agent 注册表 - 管理所有 Agent 实例"""

    def __init__(self):
        self._agents: Dict[str, AgentConfig] = {}
        self._load()

    def _load(self):
        """从文件加载所有 Agent 配置"""
        agents_file = os.path.join(AGENTS_DIR, "agents.json")
        if os.path.exists(agents_file):
            try:
                with open(agents_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for agent_id, cfg in data.items():
                    self._agents[agent_id] = AgentConfig(**cfg)
                logger.info(f"已加载 {len(self._agents)} 个 Agent")
            except Exception as e:
                logger.error(f"加载 Agent 配置失败: {e}")

        # 确保内置 Agent 存在
        for aid, acfg in BUILTIN_AGENTS.items():
            if aid not in self._agents:
                now = datetime.now().isoformat()
                acfg.created_at = now
                acfg.updated_at = now
                self._agents[aid] = acfg
                logger.info(f"添加内置 Agent: {acfg.name} ({aid})")

        self._save()

    def _save(self):
        """保存所有 Agent 配置到文件"""
        agents_file = os.path.join(AGENTS_DIR, "agents.json")
        try:
            data = {aid: asdict(cfg) for aid, cfg in self._agents.items()}
            with open(agents_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 Agent 配置失败: {e}")

    def get(self, agent_id: str) -> Optional[AgentConfig]:
        """获取 Agent 配置"""
        return self._agents.get(agent_id)

    def list(self, role: str = None) -> List[AgentConfig]:
        """列出所有 Agent（可按角色筛选）"""
        if role:
            return [a for a in self._agents.values() if a.role == role and a.enabled]
        return [a for a in self._agents.values() if a.enabled]

    def add(self, config: AgentConfig) -> bool:
        """添加或更新 Agent"""
        config.updated_at = datetime.now().isoformat()
        if config.agent_id not in self._agents:
            config.created_at = config.updated_at
        self._agents[config.agent_id] = config
        self._save()
        return True

    def remove(self, agent_id: str) -> bool:
        """删除 Agent"""
        if agent_id in BUILTIN_AGENTS:
            return False  # 内置 Agent 不可删除
        if agent_id in self._agents:
            del self._agents[agent_id]
            self._save()
            return True
        return False

    def get_coordinator(self) -> Optional[AgentConfig]:
        """获取 Coordinator Agent"""
        for a in self._agents.values():
            if a.role == "coordinator" and a.enabled:
                return a
        return None

    def get_workers(self) -> List[AgentConfig]:
        """获取所有 Worker Agent"""
        return self.list(role="worker")


# 全局单例
_registry: Optional[AgentRegistry] = None


def get_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry