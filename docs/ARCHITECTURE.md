# ZhiYan Agent 多语言协同架构

> 版本：v1 · 阶段 0 架构基线
> 目标：将现有「pywebview 单层桌面应用」改造为「Rust 桌面壳 + Python 后端」多语言协同架构，同时保持现有运行路径不破坏。

## 一、背景与目标

现状：`desktop.py`（pywebview）把「进程管理 + 窗口渲染 + Python-JS 桥接 + 全部业务逻辑」堆在一层，窗口能力（自定义标题栏、托盘、沉浸式集成）受限于 pywebview。

重构方向：使用多语言协同，各语言做自己最擅长的事——Rust 做桌面壳与系统集成，Python 继续承载全部业务，特定游戏链路继续由 Node 服务。

阶段 0 只产出本架构基线；阶段 1 产出 Rust 壳骨架（端口登记 + 拉起 + 探活）；后续阶段逐步补齐窗口、桥接、打包。

## 二、语言职责边界

动作：任何新功能默认进 Python。只有涉及「进程 / 窗口 / 系统级集成」的才允许进 Rust，且需在此处登记，避免边界漂移。

- Rust（桌面壳）
  - "zhiyan-agent-shell"：进程管理（拉起/回收 Python 与 Node 子进程）
  - 桌面窗口：WebView2 渲染、自定义标题栏、托盘图标、系统集成
  - 端口登记：维护 runtime.json 作为进程间通信的"目录服务"
  - 探活自检：TCP 轮询后端端口，报告就绪/失败
  - 约束：依赖极简（std + serde_json），避免 gnu/msvc 工具链差异带来的坑
- Python（业务主体）
  - LLM 对话、Agent 编排、插件系统、媒体（TTS/音乐）、记忆、文件工作区等全部业务
  - 自托管 Web UI 与 REST/WS API（uvicorn）
  - Node 链路如需 Node 能力，由 Python 按需拉起并管理
- Node（游戏链路，专用）
  - 仅 miao / Yunzai / NapCat 相关桥接服务（New Chat 游戏玩法链路）
  - 不承担任何常规业务

## 三、进程树

```
zhiyan-agent-shell（Rust 壳，父进程）
└── python backend/main.py          # 后端主服务（端口段 8001-8020）
    └── uvicorn：Web UI / REST / WS
    └── 内部业务模块（LLM/TTS/插件/媒体/记忆…）
Node 链路（端口段 2536-2540，按需由 Python 拉起）
├── miao-bridge
├── Yunzai
└── NapCat（QQ 协议侧）
```

明确说明：

- Rust 壳直接拉起 Python 主服务；Node 链路由 Python 业务侧按需拉起，不直接由壳管理。
- 壳退出即回收 Python（或利用控制台信号让 Python 自行退出）；Python 退出即回收其拉起的 Node 子进程。
- 阶段 1 的壳仅做「命令行入口 + 端口登记 + 拉起 Python + 探活」，不创建窗口。

## 四、端口段约定

- Python 后端主服务：`8001-8020`，启动时在该段内取首个空闲端口；段内全部占用才回退到系统随机空闲端口。
- Node（Yunzai/miao 桥）：`2536-2540`。
- 端口由壳统一分配后，通过环境变量 `ZHIYAN_PORT` 下传给 Python；壳绝不硬编码业务进程端口。
- desktop.py 现有自动选端口逻辑保持不变（兼容旧路径）。

## 五、runtime.json 端口登记协议

路径：`<PROJECT_ROOT>/logs/runtime.json`

用途：进程间"目录服务"——壳把各子进程的实际 PID / 端口 / 状态登记到磁盘，供后续阶段（窗口加载地址、托盘菜单、调试面板）与诊断工具读取。

结构（schema_version = 1）：

```json
{
  "schema_version": 1,
  "updated_at": "2026-09-13T08:00:00Z",
  "processes": {
    "backend": {
      "pid": 12345,
      "port": 8001,
      "status": "running",
      "started_at": "2026-09-13T08:00:00Z",
      "error": null
    }
  }
}
```

字段说明：

- `schema_version`：协议版本，不兼容变更时递增。
- `updated_at`：本次写入时间（ISO8601 UTC）。
- `processes.backend.status`：`starting` → `running` → `failed`；后端退出视为 `failed` 并附 `error` 说明。
- `processes.backend.error`：可选，仅在失败时写原因，成功时可缺省或为 null。

写入时机（每次状态变化都全量覆盖写）：

- 拉起子进程成功后：`starting`
- 探活通过后：`running`
- 探活超时 / 进程提前退出：`failed` 并附错误信息
- 后端退出（壳正常退出路径）：`failed`，error 说明"后端进程已退出"

写入方式：先写临时文件再改名覆盖，保证读取方不会看到半截 JSON。

## 六、后端探活与自检

- 后端不依赖专门的 `/health` 路由（现状 app.py 无健康检查接口），壳以 TCP 连接 `127.0.0.1:port` 判断就绪，行为对齐 desktop.py 的 `_wait_for_server`：
  - 轮询间隔 0.3s 起步，指数退避至上限 2s
  - 总超时 60s
- 探活期间若子进程已退出，立即判定失败并清理。
- 阶段 2 起可增加"导入 app 即失败"的自检上报，作为探活之外的首启错误捕获手段。

## 七、现有运行路径保持不变

- `desktop.py` 仍是当前默认 GUI 入口（pywebview），行为不回归。
- `rust-app` 是新增的未来默认入口，与 desktop.py 并存，均拉起同一个 `backend/main.py`。
- `backend/main.py` 默认端口仍为 8001，仅新增对 `ZHIYAN_PORT` 环境变量的支持（兼容旧路径）。

## 八、阶段路线图

- 阶段 0：架构基线（本文档）
- 阶段 1：Rust 壳骨架——CLI（--debug/--port/--help）、空闲端口分配、runtime.json 端口登记、spawn Python 后端、TCP 探活（本期交付）
- 阶段 2：WebView2 窗口、生命周期、托盘
- 阶段 3：Python↔JS 桥接迁移（迁移 pywebview 桥接到 WebView2）
- 阶段 4：打包流程替换 build_exe.spec（Rust 壳产物 + 后端平铺）
- 阶段 5：收尾——移除 pywebview 依赖、清理旧入口

## 九、开发约定

- Rust 依赖极简（std + serde_json），避免 gnu/msvc 工具链差异。
- 后端端口一律通过环境变量 `ZHIYAN_PORT` 下传，禁止壳内硬编码。
- 代码与注释统一中文。
- 阶段 1 的 CLI 形态下，Python 子进程直接继承控制台输出（日志可见、Ctrl+C 可传递）；阶段 3 引入 WebView2 窗口后再切换为 CREATE_NO_WINDOW + 日志重定向。