# 开发记忆与经验汇总

> 整理时间：2026-11-14
> 覆盖范围：2026-07 ~ 2026-11 的工程记忆、踩坑记录与约定
> 数据来源：会话记忆 `project_memory.md`、工作区 `memory/*.md`、`docs/` 下的架构与规范文档、`研究日志.md`

这份文档把散落在多处记忆里的「硬约束、踩过的坑、可复用的排查路径」集中到一页，方便新会话快速进入状态，也方便你检查还有哪些经验没有沉淀。

---

## 一、项目与架构硬约束

### 1.1 语言职责边界（已锁定）

| 层 | 负责 | 约束 |
| --- | --- | --- |
| Rust（`rust-app`） | 桌面壳：拉起/回收 Python、WebView2 窗口、托盘、端口登记、探活 | 依赖只用 std + serde_json |
| Python（`backend/`） | 全部业务：LLM/Agent/插件/媒体/记忆/工作区、REST + WS | 新功能默认进 Python |
| Node（按需） | 仅 miao / Yunzai / NapCat 游戏链路 | 由 Python 拉起，不由壳管理 |

- 端口段：Python 后端 `8001-8020`（被占满才随机），Node 链路 `2536-2540`。
- 端口一律通过环境变量 `ZHIYAN_PORT` 下传，壳/业务都不得硬编码。
- `logs/runtime.json` 是进程间「目录服务」（schema_version=1，先写临时文件再改名覆盖）。
- `desktop.py`（pywebview）仍是当前默认 GUI 入口，`rust-app` 是未来入口，两者并行拉起同一个 `backend/main.py`。

### 1.2 打包与兼容

- 便携式打包：冻结模式 `PROJECT_ROOT = exe 同级目录`，`data/logs` 全部相对 exe；整个 `dist/FurinaAgent` 可任意移动。
- 依赖、动态库、`frontend/`、`backend/` 必须**平铺**在 exe 同级（`build_exe.spec` 的 `contents_directory='.'`），不要 `_internal` 子目录。
- 内置插件必须打包进 `backend/zhiyan_web/plugins/builtin_plugins/`（含 `main.py` + `metadata.yaml`）。
- Win7 + Python 3.8：所有 `.py` 带 `from __future__ import annotations`；Pydantic 字段禁用 `list[str]` / `X | None`，必须 `typing.List/Optional/Union`；3.8 无 `asyncio.to_thread`（已在 `zhiyan_web/__init__.py` 回填）。
- 3.8 打包命令：`py -3.8 -m PyInstaller build_exe.spec --noconfirm --distpath dist38 --workpath build38`。
- 密钥加密 `_get_machine_key()` 依赖驱动器盘符（splitdrive）：**跨盘/跨机复制后 API Key 解不开**，同盘移动无影响。

---

## 二、运行、调试与验收习惯

1. 后端改动必须**重启后端进程**才生效；前端改动配合缓存参数 + `Ctrl+F5`。
2. `index.html` 的静态资源带版本号（形如 `?v=20261114demo`），每次改 CSS/JS 都要同步提升，否则浏览器吃旧文件。
3. 后端启动慢（要加载 70+ 工具、插件、TTS、数据库），验收前等日志出现 `Application startup complete`。
4. 改完先做静态检查：Python `python -m py_compile`，前端 `node --check app.js`（app.js 上百万字符，语法错会整页白屏）。
5. 起独立测试端口（如 `8131`）跑 uvicorn，不要干扰用户平时用的 8001。
6. 桥接/Node 进程改代码后必须 `Stop-Process` 杀掉旧进程再启动，否则 `EADDRINUSE` 或旧代码继续工作（老坑）。
7. 测试聊天链路时注意别污染真实会话：`data/chat_sessions/*.json` 是真实历史；需要隔离时把 `ChatManager.sessions_dir` 指向临时目录。
8. 纯样式验收可以脱离业务：复制 `style.css` + 复刻 DOM 结构到临时 HTML，本地 `http.server` + 浏览器截图，比每次跑完整 Agent 快得多（本次 DSH 行式改造就用这个办法）。

---

## 三、前端经验

### 3.1 全站设计系统（LOCKED，2026-11-13）

- 唯一规范：`docs/UI_STYLE_SYSTEM.md`；实现为 `style.css` 末尾「全站统一设计系统（LOCKED）」段。
- 统一类：`.view / .view-header / .settings-section / .btn / .form-group / .input-lg` 等；统一令牌 `--ui-radius-* / --ui-control-h / --ui-card-*`。
- 颜色只用语义变量：`--ink / --ink-2 / --ink-3`、`--surface / --surface-2`、`--accent*`、`--hairline`、`--danger / --success / --warning`；浅色水蓝、`html.dark` 自动切深蓝+金。
- 禁止：写死 `#fff/#000` 当底色、单页另写按钮/输入框/卡片样式、改全局令牌、覆盖 `#chat-view` 的 `--chat-*`。
- 每次改动要同时看浅色和深色。

### 3.2 聊天页结构（DSH 主页式）

- 三栏：会话侧栏 280（常驻、可收起 `sidebar-collapsed`）+ 阅读列 780 + 右侧 dock；顶栏只剩会话标题与「⋯」菜单。
- AI 纯文本无气泡；用户气泡 18px 无尾翼；头像隐藏；meta/操作条 hover 才显示；Composer 24px 圆角。
- `#chat-view` 内把 `--chat-*` 映射到主题令牌，不动 `:root`。

### 3.3 Agent 对话过程区（2026-11-14 重做）

- 工具行是**扁平行**不是卡片：`28px` 行高、`12.5px` 字、透明底、hover 才出 `--surface-1`；结构为「细线图标 + 标签 + `·` + 摘要（+ 统计 + 徽标）」，箭头仅 hover 可见。
- 失败行：图标、标题、摘要一起变红，并追加暖色「退出码 N」胶囊；路径摘要用细下划线；写入行显示 `1KB +8 -0`。
- 思考行统一「思考 · 摘要」，方框叉图标，**一步一行、完成自动折回单行**。
- 审批卡片：暖色描边浅底 + 琥珀圆点标题 + 等宽斜体详情 + `拒绝 / 允许一次`。
- 已删除的旧元素：工作执行摘要头、emoji 步骤时间线、思考计时器、「★ 总结 ★」分隔线（DSH 没有）。

### 3.4 前端踩坑清单

| 症状 | 根因 | 处理 |
| --- | --- | --- |
| 按钮定位异常 | `.chat-main > *` 的 `position: relative` 盖住 `.scroll-to-bottom-btn` 的 `absolute` | 提高选择器权重或改结构 |
| 整个页面报错 | `renderMarkdown` 末尾引用了未声明的 `fileRefs` | 开头声明局部并调用 `_extractFileRefs` |
| 改了没生效 | 静态资源缓存参数没变 | 提升 `?v=`（日期+主题词） |
| 同名函数行为诡异 | `app.js` 里重复定义 `addMessage/formatFileSize/escapeHtml`，前一处是死代码 | 清理重复定义 |
| 任意位置拖动窗口 | pywebview frameless 默认 `easy_drag=True` 全局 mousedown | 设 `easy_drag=False`，只给 `.pywebview-drag-region` 拖动 |
| 挂件点击 A 抬升 B | 每个挂件各自绑焦点 | 改在 `#widget-layer` 上做 pointerdown 事件委托 |
| 深色下代码块看不清 | 代码块硬编码深底深字 | 浅/深各一套 token |

---

## 四、Agent / LLM 引擎经验

### 4.1 真流式

- `providers.chat_stream_events()` 是唯一正确入口：边收边 yield `reasoning/text`，结束时汇总 `tool_calls`（按 index 拼接 delta）。
- `chat_stream()` 只是它的薄封装；`chat_with_tools_stream` 必须用它，别退回非流式 `chat()` + `sleep` 伪造（会变成「全输出完再打字机」）。
- 验收标准：首 chunk 时间明显早于 done；记录块数与总耗时。

### 4.2 DeepSeek 400 的四类根因（已全部修复）

1. 思考模式下 assistant 消息必须回传 `reasoning_content`（历史丢失即 400）→ `_build_messages` 统一补 `reasoning_content=""`。
2. `max_tokens=0` 非法 → 仅在 `>0` 时下发。
3. 孤儿 `tool` 消息 → `_sanitize_tool_messages()`：合法组保留，未回应的 tool_calls 降级为文本，孤儿结果转 user，空内容丢弃。
4. DSML 标记泄漏（`<｜｜DSML｜｜...>`）被当正文写进历史 → `_strip_dsml` / `_extract_dsml_tool_calls` 双向清洗。

### 4.3 会话与上下文

- 空白会话不入库：`create_conversation` 不落盘，`save_conversation` 遇到空白删除文件，`_load_conversations` 清理历史空文件，`latest_conversation_id` 只选有内容的。
- 系统提示词的安全边界注入点在 `_build_agent_prompt()`（不是别处），普通模式必须给工作区绝对路径 + 写入边界。
- 子代理并发：同一条回复里多个派发类调用会先并发启动，再按顺序回收；事件通过 sink 队列边等边推。

### 4.4 工具与审批

- 工具循环是 Plan-Observe-Act；工具结果统一走 `_execute_tool_with_retry`。
- 审批（2026-11-14 新增）：`approval_broker.evaluate()` 判定越权 → `open_request()` → SSE `approval_request` → 前端卡片 → `POST /api/chat/approval-decision` → `wait()` 恢复。
- 放行用 `safety_manager.set_approval_bypass()`（contextvar）包住**单次**工具协程，`file_ops._check_workspace` 跳过 `validate_path`，执行完立刻复位。
- 触发范围：仅普通模式下「写出工作区」；高权限安全模式仍硬拦截；完全控制不弹。
- 预设/演示模式：`core/llm/demo_agent.py` 按真实事件序列播放完整任务（含审批），不调 LLM、不耗 token；模拟模式自动触发，或聊天里发送「演示」。

---

## 五、安全与权限

- 三种模式：`normal`（写仅限工作区，读全盘）、`high_security`（禁系统关键路径/可执行文件，写仅限工作区）、`full_control`（全盘，重启自动回落）。
- 四层：工具注册层 → SafetyManager 策略（风险等级/审批）→ 工作区 `validate_path` → 执行器命令白名单。命令执行必须带 `CREATE_NO_WINDOW`。
- 命令输出必须内联在对话的工具块里（`.cmd-inline`），禁止弹独立 cmd 窗口/黑框。
- 危险命令（format/diskpart/reg delete/taskkill /f/shutdown…）永远禁止，不提供「允许一次」。

---

## 六、平台与外部集成经验

### 6.1 QQ / NapCat / Yunzai / miao

- 群聊必须 @ 机器人才能触发插件；私聊可直接 `/` 命令。
- `miao_plugin.mode=yunzai` 时 `#` 命令完全交给 Yunzai（静默截停），`/`、`!` 归本软件插件；只有平台名匹配 `qq|onebot|napcat` 才截停，网页端改为友好提示。
- `_route_to_engine` 里 `is_miao_cmd` 必须同时作用于 `use_agent`、`fallback_agent_on_empty` 和异常回退，否则 # 命令会双重回复。
- 权限：`owner_only_llm`（仅主人调 LLM）、`plugin_use_permission`（all/master/whitelist）。
- 保存配置时忽略含 `****` 的脱敏 token，避免把真实 token 覆盖掉。
- 桥接错误对象键名历史坑：Node 返回 `{type:'text',text}` 而 Python 读 `content` → 错误被吞 → 静默截停；现双向兼容。
- QQ 音乐/网易云：EAPI 必须 hex 加密、路径带 `/api`、带 pyncm 风格 Cookie；网易云 `weapi` 已失效。

### 6.2 BetterGI

- CLI 依据 `CommandLineOptions.cs`；GUI 运行中不接受任务参数，需先结束进程。
- 通知优先 Webhook：写 `<BGI>/User/config.json` 的 `notificationConfig`（写前自动 `.zhiyan-bak-时间戳` 备份），需重启 BGI 生效。
- 端口漂移：桌面壳端口随机，`ZHIYAN_PORT` 下传 + 启动钩子幂等 `sync_notification_endpoint`。
- 卡死判定：事件/任务/启动/日志 mtime 取最大值为参考，超时进图像识别兜底（OCR → 规则点击 → vision 决策）。
- OCR：3.13+ 用 `rapidocr[onnxruntime]`，否则 `rapidocr-onnxruntime==1.4.4`；模型缓存在 `data/py_deps/ocr`。
- 定时任务按 `id+日期` 去重落盘；开机自启写 HKCU Run 并与配置双向对齐；任务成功且 `auto_shutdown` 才关机（可取消）。

### 6.3 DSH 内核

- 配置文件实际叫 `misaka.cordis.yml`（曾按 `zhiyan.cordis.yml` 找导致读不到）。
- Windows 上 npm 是 `npm.cmd`，`create_subprocess_exec` 不解析 PATHEXT → 必须 `_npm_cmd()`（node + npm-cli.js 优先）。
- `reasoning_effort` 只接受 `off/low/high/max`，`medium/min` 会让 boot 失败；Windows 无 bash，用官方 `dsh-pwsh-local`。
- 续聊：cordis patch 不能改 `name`，只能 replace config/切 disabled；要 insert 新行指向 `./zhiyan-sdk-resume.mjs`（相对路径）。
- SDK 没有 server→client 请求，所以 DSH 侧交互式审批未实现；主引擎侧已用 `approval_broker` 补齐。

### 6.4 Skills 市场 / TTS / 其他

- openclaw 主源已死，必须自动回退 takoapi；前端卡片 `data-src` 要用 `s.source` 而不是下拉当前值。
- takoapi 下载需先 `/api/skills/{slug}` 拿 githubUrl，再从 `raw.githubusercontent.com/.../HEAD/SKILL.md` 拉取。
- GPT-SoVITS：v1/v2 端点与参数名不同（`/` vs `/tts`、`text_language` vs `text_lang`），合成超时给 120s；极端参数会生成近义词而不是原文。
- 外部歌词：LRC 解析用 `parse_lrc_text()`，位置毫秒要除 rate 再转秒；网络搜索失败必须回滚本地歌词，并用请求 ID 防旧响应覆盖新响应。

---

## 七、打包与发布

- `build_exe.spec` 必须显式收集：`cv2`（模板匹配）、`rapidocr/onnxruntime`、`frontend/`、`backend/`。
- 打包产物平铺在 exe 同级（无 `_internal`）；`data` 不随包，首启自动创建空目录。
- 冻结模式不要保留硬编码回退根目录，否则换盘/换路径即崩。
- 版本改动同时更新 `README` / `DEVELOPMENT` 中的端点与功能列表。

---

## 八、问题速查表（症状 → 根因 → 位置）

| 症状 | 根因 | 修复位置 |
| --- | --- | --- |
| AI 全部输出完才「打字机」 | 用了非流式 `provider.chat()` | `chat_manager` 改用 `chat_stream_events` |
| DeepSeek 400 | reasoning_content 缺失 / max_tokens=0 / 孤儿 tool / DSML 泄漏 | `providers._build_messages` 等 |
| 重启后多一个空对话 | 新建会话立即落盘 | `Conversation.has_content()` + 保存/加载过滤 |
| DSH 更新 `[WinError 2]` | npm 实为 `npm.cmd` | `updater._npm_cmd()` |
| DSH boot 失败 | `reasoning_effort=medium` | `_build_composition()` 映射合法值 |
| 窗口任意位置可拖 | pywebview `easy_drag` 默认开启 | `desktop.py` 设 False + 官方拖拽区 |
| 挂件焦点错乱 | 各挂件独立绑事件 | `#widget-layer` 事件委托 |
| 图标设置静默失败 | pythonnet 句柄类型转换 | 走 Win32 兜底/捕获异常 |
| 桥接改了不生效 | 旧 Node 进程占着 8200 | 先 Stop-Process 再启动 |
| 测试污染真实历史 | 直接实例化 ChatManager | 重定向 `sessions_dir` 到临时目录 |

---

## 九、尚未实现 / 待办风险

- DSH 内核侧交互式审批（SDK 无 server→client 请求）；主引擎侧已有。
- 进程级沙箱：目前只有策略级（工作区 + 命令白名单），没有网络/文件系统级隔离。
- QQ 官方机器人入站消息只做缓存与展示，尚未完全接入命令系统。
- 图片多模态依赖模型名匹配（vl/gpt-4o/claude/gemini 等），未覆盖全部视觉模型。
- 上下文压缩三阶段在长会话下仍需观察是否有信息丢失。

---

## 十、文档地图

| 文档 | 内容 |
| --- | --- |
| `docs/ARCHITECTURE.md` | Rust 壳 + Python 后端架构基线、端口、runtime.json 协议、阶段路线图 |
| `docs/UI_STYLE_SYSTEM.md` | 全站 UI 令牌/组件/禁止事项（LOCKED） |
| `docs/DSH_FEATURE_GAP_ANALYSIS.md` | 与 DeepSeek Harness 的能力对比、P0/P1 缺口与实施进度 |
| `docs/BETTERGI_DEV_GUIDE.md` | BetterGI 托管 A~I 方案、实现索引、运维速查、打包注意 |
| `docs/TEST_STANDARDS.md` | 模型配置 / TTS 的功能、性能、体验验收标准 |
| `docs/DEVELOPMENT.md` | 功能清单、API 端点、数据库表、部署说明 |
| `研究日志.md` | 五阶段研发过程 + 架构/管道/链路/流程图（对外汇报用） |
| `memory/YYYY-MM-DD.md` | 按天记录的实现细节与当次验证数据 |

---

## 附：本次会话新增（2026-11-14）

1. Agent 过程区改为 DSH 行式（工具行/思考行/失败行/统计/审批卡），删除旧摘要头与时间线。
2. 真审批链路：`approval_broker` + `POST /api/chat/approval-decision` + 一次性放行上下文。
3. 预设模式演示任务：`demo_agent.py`，零 token 复现完整轨迹（含审批），发送「演示」即可触发。
4. 验收方式沉淀：真实 `style.css` + 复刻 DOM 的静态预览页截图比对；独立端口起服务做端到端浏览器验证。
