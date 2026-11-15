"""
内置管道阶段

阶段注册顺序:
1. WakingCheckStage    - 唤醒检查（检查是否被 @ 或唤醒前缀触发）
2. PreProcessStage     - 预处理（消息格式化、历史记录）
3. ProcessStage        - 核心处理（插件 Handler / 智能体）
4. ResultDecorateStage - 结果装饰（情感注入、TTS 触发）
5. RespondStage        - 响应发送

注意：必须导入各阶段模块以触发 register_stage 装饰器。
"""
from __future__ import annotations

# 导入阶段模块以触发注册
from . import waking_check
from . import pre_process
from . import process
from . import result_decorate
from . import respond