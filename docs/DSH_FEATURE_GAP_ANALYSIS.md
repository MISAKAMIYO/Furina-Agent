# DSH 能力对比与功能缺口分析

> 版本：v1 · 2026-11-13
> 对比对象：DeepSeek Harness（dsh 0.1.0-rc.8 本体 + DSH Suite 套件，本地源码见 `../DSH-CyberWorkStation-main`）
> 对比对象（本软件）：Furina Agent Web（`frontend/` + `backend/zhiyan_web/`）
> 用途：作为后续 agent 内核能力建设的需求底稿，按 P0/P1/P2 排期。

## 一、结论

1. 产品广度上本软件已超过 DSH 官方 Web UI：24 个功能视图、桌面壳、插件/技能市场、QQ 与 BetterGI 托管、音乐/TTS/教辅/情感等，均是 DSH 本体没有的。
2. 差距集中在两处：
   - agent 内核运行时能力（会话恢复/分支、审批、沙箱、成本统计、多模态输入等）；
   - 「DSH 内核」接入完整度（会话不可续聊、无审批、无 MCP、改插件必须重启）。
3. DSH 本体也有明确短板，不需要盲目对齐（见第六节）。

## 二、对比范围与方法

- DSH 侧依据 `DSH-CyberWorkStation-main/core/docs/*`（architecture / agent-lifecycle / capability-seams / tool-catalog / config-catalog）、`plugins/`、`launcher/`、`README.zh.md`。
- 本软件侧依据 `frontend/index.html`、`frontend/dsh.html`、`backend/zhiyan_web/*`（dsh、mcp、computer、subagent、context_compress、core/llm 等）与 `docs/`。
- 只对比「能力是否存在及其形态」，不比较代码质量与性能。

## 三、已对齐或更强

| 能力 | 本软件 | DSH |
|---|---|---|
| 功能视图/场景 | 24 个视图（聊天、音乐、迷你播放器、TTS、教辅、授课、情感、好感度、约定、画像、主动交互、QQ、BetterGI、文件转换等） | 仅对话工作台 |
| IDE 形态 | Monaco 代码编辑器、文件树、命令提示符面板、内联终端输出 | 无编辑器/终端面板，仅工具卡片 |
| 插件与技能市场 | 扩展中心 + Skills 市场（openclaw/takoapi 双源回退） | 本体无市场（套件启动器另外提供） |
| 多供应商 | 17 家预设 + 自定义端点 | DeepSeek 直连 + pi-ai 目录化供应商 |
| 命令审批 | 请求→待审批→确认/取消→SSE 输出→审计历史，带白名单与风险分级 | 一次性允许/拒绝 |
| 桌面形态 | Rust/pywebview 无边框窗、自定义标题栏、挂件系统 | 浏览器 Web UI |
| 业务扩展 | QQ 平台、NapCat/Yunzai、BetterGI 托管、音乐库、TTS 双引擎 | 无 |

## 四、P0 缺口（影响核心体验，建议优先）

### P0-1 DSH 会话不可续聊、不可分支
- 现状：`backend/zhiyan_web/dsh/engine.py` 只维护单轮 SDK 调用；`dsh.html` 会话页仅列举/删除/清空文件，每次对话新建 `_dshSessionId`。
- DSH：`SessionEvent` 事件溯源，可回放/恢复/fork，JSONL 或 SQLite 持久化。
- 目标：会话续聊（按 session_id 恢复上下文）、会话内 fork 分支、会话列表展示消息数与最后更新时间。

### P0-2 DSH 接入没有审批与权限模式
- 现状：patch 固定 `DSH_PERMISSION_MODE=danger-full-access`，sandbox 亦为 full-access；无 ApprovalPanel。
- DSH：`ctx.approval` 一次性决策瀑布 + `permission-presets`（workspace-write / danger-full-access），无应答方即失败关闭。
- 目标：审批请求透传到主界面（复用现有命令审批 UI 形态）；Access 预设下拉；Full access 二次确认；拒绝时明确回传失败原因。

### P0-3 MCP 未打通 DSH
- 现状：`backend/zhiyan_web/mcp/` 仅服务内置 Agent 引擎，DSH 的 cordis patch 无 MCP 行。
- DSH：`mcp-client` 支持 stdio / SSE / Streamable HTTP、`mcp__server__tool` 命名空间、超时与重连。
- 目标：把已配置的 MCP server 注入 DSH patch，工具列表与主引擎共享同一份配置。

### P0-4 图片不是多模态
- 现状：`frontend/static/js/app.js` 的 `buildAttachContent` 只拼 `[img]名称|url[/img]` 文本哨兵；`core/llm/providers.py` 的 `_build_messages` 只产出字符串 content。
- DSH：粘贴/整页拖拽图片、数量与字节预检、灯箱预览、image 消息块。
- 目标：Pro 视觉模型走 `image_url` 结构化内容；非视觉模型保留文本哨兵降级；前端补多图预览与体积提示。

### P0-5 没有斜杠命令系统
- 现状：`chat/routers/chat.py` 的 `_COMMAND_PREFIXES = ["/", "!", "#"]` 只做前缀转管道（插件/miao），无内置命令注册表、补全、帮助。
- DSH：斜杠命令菜单（`/plan`、`/compact`、`/permission` 等）、`@` 文件引用。
- 目标：命令注册表 + 输入框补全 + `/help`；优先实现 `/compact`、`/model`、`/permission`、`/plan`。

### P0-6 沙箱只有策略级
- 现状：`computer/services/executor.py`（白名单/黑名单/工作区校验）+ `safety_manager.py` 三档安全模式，无进程级隔离；DSH 侧直接 full-access。
- DSH：`sandbox-local`（Linux bwrap/Landlock、macOS Seatbelt、Windows ACL），三模式 + 强制执行报告。
- 目标：主引擎接入最小可用沙箱（至少限制工作区外写入与网络）；DSH 侧从 full-access 收敛到可配置。

### P0-7 成本与用量统计缺失
- 现状：主引擎无价格表，无全局 token 用量页；仅 DSH `cost-meter` 账本可读（`/api/dsh/tokens`），且需自行安装插件。
- DSH 套件：多 API 余额、模型目录价、缓存命中率、GitHub 风热力图、连续天数、按模型分布。
- 目标：本地 token 记账（按会话/模型/日），价格表 + 余额查询，独立「用量」页。

## 五、P1 缺口（体验对齐）

1. 检查点与回滚：DSH 只有会话级 fork，无文件级回滚；建议先做「编辑上一条并重发」「会话级快照恢复」。
2. 工具调用可视化：按 terminal / diff / 搜索 / todo 分卡片渲染，支持查看原始输入输出与轨迹视图、产物 chips、上下文占用环。
3. plan mode / goal / jobs / subagent 编排视图：`backend/zhiyan_web/subagent/` 已有 spawn/delegate/board，缺前端计划条与任务视图。
4. 审批面板接管输入区 + Access 预设下拉 + busy 状态下的排队提示。
5. 输入队列与 steering：忙时消息入队、可插话、可取消；`busyEnter` 行为偏好。
6. 插件配置卡片：每个插件 manifest 声明 settings 后自动生成表单。
7. DSH 插件/技能热重载：当前安装后必须重启内核，目标是动态 reload cordis patch。
8. 皮肤体系：前端 CSS 注入、启动器皮肤、社区皮肤包转换。
9. hooks 桥接：兼容 claude-code / codex 的 hooks.json（本软件已有 `agent/hook_bus.py`，缺外部格式适配）。
10. 控制甲板式提示词工程：多条分级注入、正则脚本、世界书、采样覆盖的可视化编辑与热载（本软件已有系统提示词与工作区规则，缺 UI）。
11. 会话归档、会话内模型切换、断线重连提示。

## 六、DSH 明显偏弱、不必照搬

1. 无文件级检查点/回滚，只能 fork 会话。
2. 审批只有「允许一次/拒绝」，无「始终允许」。
3. 已发送的用户消息不可编辑；分支入口只在轮次末条 assistant。
4. 无会话删除与取消归档入口，无全文模糊搜索。
5. 不支持任意文件上传（附件仅 PNG/JPEG/WebP/GIF）。
6. MCP 只有客户端，无 server 模式。
7. 本体无插件市场、Skill 市场、费用统计、皮肤体系（均由套件外挂）。
8. Web UI 无 IDE 式文件编辑器与终端面板。
9. 沙箱策略只覆盖文件系统效果，不含网络与进程可见性；Windows ACL 为 partial。
10. 创建会话时不能选模型，subagent 也无独立模型选择。

## 七、建议落地顺序

| 阶段 | 内容 | 验收口径 |
|---|---|---|
| 第 1 步 | P0-1 DSH 会话续聊与 fork | 关掉软件重开能回到同一 DSH 会话；同一会话可开分支 |
| 第 2 步 | P0-2 DSH 审批与权限模式 | 危险命令在界面弹出审批；拒绝后 DSH 收到失败；默认不再是 full-access |
| 第 3 步 | P0-3 MCP 打通 DSH | 现有 MCP server 的工具有出现在 DSH 会话中，可调用 |
| 第 4 步 | P0-4 图片多模态 + P0-5 斜杠命令 | 粘贴图片可直接提问；`/compact`、`/model`、`/permission` 可用并有补全 |
| 第 5 步 | P0-6 沙箱 + P0-7 成本统计 | 工作区外写入被拒；用量页能按日/模型看到 token 与费用 |
| 之后 | P1 各项 | 按工具卡片 → plan/jobs → 队列 steering → 插件配置与热载 → 皮肤/hooks 排序 |

## 九、实施进度（2026-11-13）

| 条目 | 状态 | 说明 |
|---|---|---|
| P0-1 DSH 会话续聊 | 已完成（续聊）/ 未做 fork | `engine.py` 停用官方 `sdk-jsonrpc-server` 并插入自带实现 `zhiyan-sdk-resume.mjs`（写在内核根目录、用相对路径加载），先 `agents.resume()` 再回退新建；前端 `dshSessionId` 持久化，`dsh.html` 会话页新增「续聊 / 新建续聊会话 / 当前会话」标记。实测重启内核后同一 sessionId 可继续上下文。fork 分支未实现。 |
| P0-2 审批与权限模式 | 部分完成 | 新增 `dsh.permission_mode`（danger-full-access / workspace-write），同时驱动 sandbox-policy 与 `DSH_PERMISSION_MODE`；`dsh.html` 设置页新增下拉。**交互式审批面板未实现**：SDK 协议当前不支持 server→client 请求，workspace-write 下越权操作会 fail-closed 拒绝。 |
| P0-3 MCP 打通 DSH | 已完成 | `_mcp_plugin_rows()` 把主引擎已启用 MCP 服务映射为 `@deepseek-ai/dsh-mcp-client` 行（stdio / streamable-http，SSE 跳过），设置页新增「MCP 桥接」开关。改配置后需重启内核。 |
| P0-4 图片多模态 | 已完成 | 视觉模型（vl / gpt-4o / claude / gemini 等）下，`[img]名称|url[/img]` 会读取 `data/chat_images`、`data/chat_files` 转 base64 `image_url` 内容块；非视觉模型保持纯文本。DSH 侧将同一哨兵转成 SDK `image` 内容块。 |
| P0-5 斜杠命令系统 | 已完成（首版） | 前端命令注册表 + `/` 补全面板（↑↓ / Enter / Tab / Esc），支持 `/commands`、`/new`、`/clear`、`/compact`、`/session`、`/model`、`/permission`、`/agent`、`/dsh`、`/normal`；未命中注册表的 `/` 仍走原有插件管道。`@` 文件引用未实现。 |
| P0-6 沙箱 | 部分完成 | DSH 侧权限模式可收敛到 workspace-write；主引擎沿用既有 `workspace_manager.validate_path`（写操作默认限制在工作区，full_control 模式除外）。**未做进程级隔离与网络限制。** |
| P0-7 成本与用量 | 已完成（基础版） | 新增 `core/llm/usage_meter.py` 记录每次调用的 token（流式按字符估算并标记 estimated），内置价目表；新增 `/api/usage/summary`、`/api/usage/price` 与「用量统计」视图（总览卡、按日、按模型、费用估算）。 |

已知遗留：
1. 交互式审批（需要扩展 SDK 协议 + 审批应答回传）。
2. 会话 fork 分支。
3. `@` 文件引用与输入队列 / steering。
4. 进程级沙箱与网络限制。
5. 主引擎附件仍是「文本哨兵 + 可选图片块」，未做前端多图预览校验。

## 十、参考文件索引

- DSH 套件说明：`../DSH-CyberWorkStation-main/README.zh.md`
- DSH 架构与能力缝合：`../DSH-CyberWorkStation-main/core/docs/architecture.zh.md`、`capability-seams.zh.md`
- DSH agent 生命周期：`../DSH-CyberWorkStation-main/core/docs/agent-lifecycle.zh.md`
- DSH 工具目录：`../DSH-CyberWorkStation-main/core/docs/tool-catalog.zh.md`
- 本软件 DSH 接入：`backend/zhiyan_web/dsh/engine.py`、`routers.py`、`frontend/dsh.html`
- 本软件 MCP：`backend/zhiyan_web/mcp/manager.py`、`routers.py`
- 本软件命令审批：`backend/zhiyan_web/computer/services/command_prompt.py`、`safety_manager.py`、`executor.py`
- 本软件子代理：`backend/zhiyan_web/subagent/`
- 本软件上下文压缩：`backend/zhiyan_web/context_compress/`
- 本软件消息构造：`backend/zhiyan_web/core/llm/providers.py`、`frontend/static/js/app.js`（`buildAttachContent`）
