# BetterGI 托管子系统 · 开发指南

> 版本：v3 · 依据手写开发文档两页（2026-11-01 / 2026-11-13 拍照稿）整理，2026-11-13 完成 A~I 全部阶段
> 用途：后续开发的功能蓝图 + 现有实现索引 + 接口契约，避免重复沟通与边界漂移
> 代码根：`backend/zhiyan_web/bettergi/`

## 一、文档定位

本文是「原神 / BetterGI 自动化托管」这一子系统的唯一开发基准。

1. 第一章到第三章：手写文档的还原、当前实现对照、目标架构。
2. 第四章到第五章：接口契约与状态机，改动前必须对齐。
3. 第六章：功能方案与实现记录；第七章：端到端功能流程。
4. 第八章起：开发规范、扩展手册、验收清单、已知坑、实现索引。

手写原件里字迹模糊处，下文以「（原稿：…）」标注原意推测，开发前需与需求方确认。2026-11-13 拍照稿的第 11 条只写了「11. 声明」标题，正文未写完，暂不纳入需求。

## 二、手写文档要点还原

### R1 进程监控与通讯

1. 启动后按间隔轮询 BetterGI 进程存活情况。
2. 通过 WebSocket 接收 BetterGI 推送的任务结果，包含完成 / 结束 / 开始 / 异常。
3. 通过命令行参数控制 BetterGI 的运行（启动模式、一条龙、配置组、任务进度）。

### R2 QQ 平台机器人

1. 通过已构建的 QQ 开放平台机器人通道，提供 AppID 与 AppKey 搭建机器人网关。
2. 承担两类职责：主动推送任务状态；接收指令回调。

### R3 UI 面板

1. 各功能开关统一使用滑块控件，特殊功能才使用其他样式。
2. 功能总控开关放在页面中心，使用较大的圆头滑块按钮。
3. 圆头按钮内图标随状态变化：关闭显示「X」，开启显示「√」。

### R4 图像识别

1. 需要识别中英文与图案；除 Paddle 外可自选方案，可参考 BetterGI 源码使用同类算法或 OCR。
2. 支持局部 / 指定区域识别，不必每次全屏。
3. 识别时用红框在屏幕上实时标出识别位置。
4. 能按屏幕比例换算出文本 / 图案在屏幕上的绝对坐标，回传给软件用于点击。

### R5 任务计划与更新流程

1. 设定指定时间唤醒 AI 执行任务；到点向 AI 下发更新信息提示词，覆盖 BetterGI 更新、原神更新、更新时间查询。
2. 提示词可固定，也可由用户自行填写，内容要告知 AI 详细的操作过程；AI 可调用图像识别引擎（截屏识别或实时识别）。
3. 原神更新流程（原稿步骤，逐条保留）：
   1. AI 接收任务 → 打开米哈游启动器（固定路径，或由 AI 查找）。
   2. 默认无模型状态下截图，识别「更新游戏」按钮位置。
   3. 按识别出的坐标参数点击按钮 → 点击「更新游戏」。
   4. 静默等待几秒 → 识别按钮状态 / 流程是否自动运行，出现异常时调用 AI 兜底。
   5. 循环检查是否出现「开始游戏」或「启动游戏」；是则判定更新完成，否则回报 QQ 平台。
   6. 更新完成后启动预设的一条龙脚本。
4. BetterGI 更新流程（原稿步骤，逐条保留）：
   1. 接收任务 → 打开 BetterGI。
   2. 若自动弹出更新提示，直接按提示更新；若未弹出，则走图像识别：点击右下角「设置」→ 划动页面 → 「检查更新」。
   3. 无更新则停止操作；有更新则点击「更新软件」。
   4. 更新页面出现 → 点击更新按钮 → 等待更新完成。
   5. 点击「确认」或「启动」→ 确认更新完成（原稿：看左上角版本号变化）。
   6. 更新完成后启动预设的一条龙脚本。
5. 红线：更新期间禁止自动关机；操作启动器前必须确认原神已退出。
6. 落地说明：当前用「声明式步骤机」实现上述两条流程（`update_flow.py`），提示词字段保留给后续「让 AI 按自然语言编排步骤」用，尚未接入。

### R6 自动续跑与异常判定

1. 启动后若 BetterGI 正常运行，但任务异常中止或结束时，自动再次启动任务计划（原稿：「单面启动后…自动启动任务计划」）。
2. 异常判定：BetterGI 未向软件发送信息、游戏进程 / 窗口未启动。
3. 开机自动计划：电脑启动后，BetterGI 未正常运行且并非「任务异常中止进程」时，自动启动任务计划检查更新。

### R7 异常处理链

1. 进程未启动完成：记录并 QQ 通知，同时对游戏窗口截图发到 QQ。
2. 检查游戏是否运行，未运行则重新拉起游戏。
3. 检查游戏窗口是否卡死 / 未响应，卡死则重启。
4. 检查网络连接，异常则重启网络。
5. 以上均无效：重试 2 次后仍失败，返回 QQ 通知报告异常。

### R8 检查清单法

1. 逐项检测 / 验证状态。
2. 对异常项执行处理：暂停 / 取消 / 结束 / 失败，并按次数与间隔重试。
3. 仍未解决则持续 QQ 通知，直到人工介入。

### R9 任务异常情况判定与处理（2026-11-13 稿）

四种异常入口，处理动作各不相同，不能合并成一条「统一重启」：

1. BetterGI 主动回报错误：记录错误；同一天内累计达到 3 次，判定任务异常，重启一条龙副本。
2. BetterGI 没有回报信息，但进程存活、原神游戏进程已死：判定异常，回报 QQ 平台，不做继续处理（不自动重启，等人工）。
3. 在设定时间段内 BetterGI 未启动、原神也未启动：判定异常，回报 QQ 平台，并自动启动一条龙副本。
4. 任务执行超时：任务异常退出而原神进程仍存活超过规定时长（原稿：12 小时），软件执行自动关机，并回报 QQ 平台。

### R10 监测模式选择

1. 严格模式：一报错就回报 QQ；缩短监测间隔；超过监测间隔仍无回应，即抛出异常。
2. 宽松模式：报错只记录不回报；放宽监测间隔；连续 20～30 次监测间隔无回应才抛异常（原稿：20/30）。
3. 抛出异常后，按异常类型走 R9 对应的处理动作，而不是一律重启。

### R11 声明

原稿仅写「11. 声明」标题，正文未完成。实现时若涉及免责声明 / 使用条款，等需求补充后再落位。

### 补充（与 11-01 稿重复或细化的部分）

1. WebSocket 承担双向职责：接收任务完成 / 结束 / 开始 / 异常通知，也是进程存活轮询的通讯通道。
2. QQ 平台机器人由用户手动提供 AppID 与 AppKey，软件侧需自建一个「QQ 机器人网关」承接回调。
3. UI 除滑块外，特殊功能按钮允许使用其他样式；总控仍固定为页面中心的大圆头滑块（✕ → ✓）。
4. 图像识别引擎不限定 OCR：需同时支持中英文文本与图案，可参考 BetterGI 的做法（模板匹配 + OCR 混合，而非纯 OCR）。

## 三、现有实现对照

| 需求 | 状态 | 落点 |
| --- | --- | --- |
| R1.1 进程轮询 | 已实现 | `service.py` `_watch_loop` / `_tick`，间隔取自 `check_interval` |
| R1.2 事件接收 | 已实现 | `routers.py` `/event` 与 `/ws`；BetterGI 侧 `notify_transport` 可选 webhook 或 websocket |
| R1.3 命令行控制 | 已实现 | `build_args`，支持 normal / onedragon / groups / task_progress |
| R2 官方机器人 | 已实现 | `platform/qq_official.py`，WebSocket 收消息 + 命令系统 + msg_id 缓存；网关支持状态查询、单独重启与凭据校验（`/gateway/*`、`/validate`） |
| R3 滑块 UI | 已实现 | `.bgi-switch` 组件（`style.css`）+ 页面中心 `.bgi-switch-lg` 总开关；关闭显示 ✕，开启显示 ✓，总开关关闭时其余开关 `.bgi-dim` 置灰 |
| R4.1 OCR 中英文 | 已实现 | `vision.py` RapidOCR 文本 + OpenCV 模板匹配（多尺度），统一成 `items`（`kind=text` / `kind=template`） |
| R4.2 区域识别 | 已实现 | `capture_window(region=…)` 支持 0~1 比例区域，`offset` 直接是区域左上角屏幕坐标；规则支持 `region` 过滤与 `threshold` |
| R4.3 红框叠加 | 已实现 | `bettergi/overlay.py`：tkinter 无边框置顶窗口 + 色键透明 + WS_EX_TRANSPARENT 点击穿透；`overlay_enabled` / `overlay_duration` / `overlay_scale` 三项配置，接口 `/overlay/status`、`/overlay/preview`、`/overlay/reset` |
| R4.4 坐标回传 | 已实现 | OCR 结果含 `box` / `cx` / `cy`，点击时叠加窗口偏移 |
| R5.1 定时任务 | 已实现 | `schedule_status` / `_schedule_loop`，支持 HH:MM + 星期 |
| R5.2 AI 提示词 | 部分实现 | 步骤机可用内置流程或用户自定义 JSON；「自然语言提示词交给 AI 编排」尚未接入 |
| R5.3 AI 调视觉 | 部分实现 | 规则未命中时调 LLM；更新流程目前走步骤机，未把每步观察交给 AI |
| R5.4 原神更新 | 已实现 | `update_flow.GAME_FLOW` 五步：打开启动器 → 等就绪 → 点「更新游戏」→ 等更新开始 → 等「开始游戏/启动游戏」 |
| R5.5 BetterGI 更新 | 已实现 | `update_flow.BGI_FLOW` 十一步：打开 → 弹窗直更 → 设置 → 划动 → 检查更新 → 判定 → 更新软件 → 更新 → 等版本号变化 → 确认启动 |
| R6.1 自动续跑 | 已实现 | `_maybe_relaunch` + `_relaunch_runner`：按 `relaunch_modes` 命中结果排队，延迟后先结束再按 `launch_mode` 重启；`relaunch_max` 封顶，与定时任务共用 `_launch_lock` |
| R6.2 异常判定 | 已实现 | 事件超时 + 日志 mtime + `health.py` 五项检查（BetterGI 进程 / 游戏进程 / 窗口响应 / 日志活性 / 网络） |
| R6.3 开机检查更新 | 已实现 | `boot_task_plan` + `_run_boot_plan`：延迟后检查 BGI 是否在跑、上次退出是否 `task_error`，满足则按 `boot_plan_targets` 执行更新流程，每天最多一次 |
| R7 异常处理链 | 已实现 | `service.run_health_chain`：重启 BetterGI → 拉起游戏 → 关无响应窗口 → 刷 DNS → 放弃；失败截图推送 NapCat 与官方机器人富媒体 |
| R8 清单法 | 已实现 | `health.run_checks` 流水线，单项异常不影响其他项，每项返回 `key / name / ok / detail` |
| R9.1 错误日计数 3 次 | 已实现 | `errors.DailyErrorCounter` 落盘 `data/bettergi/error_count.json`；达到 `daily_error_limit` 判定任务异常并重启一条龙 |
| R9.2 进程活但游戏死 | 已实现 | `_guard_tick` 检出后分发 `game_dead`（默认 `report_only`），只回报并置 `alert`，不重启 |
| R9.3 时间段内双未启动 | 已实现 | `active_window_start/end` + `_guard_tick`，命中 `not_started`（默认 `relaunch`） |
| R9.4 12 小时超时关机 | 已实现 | `health.game_process_info()` 取游戏 `create_time`，存活超过 `game_alive_shutdown_hours` 分发 `task_timeout`（默认 `shutdown`，需同时开 `auto_shutdown`） |
| R10 严格 / 宽松模式 | 已实现 | `monitor_mode` + `strict_miss_limit` / `loose_miss_limit`；严格模式轮询自动收紧到 15 秒，连续无响应触发 `unresponsive`（默认 `self_heal`） |
| R11 声明 | 未实现 | 原稿正文缺失，等需求补充 |

## 四、目标架构

### 模块职责

1. `service.py`：唯一状态源。进程探测、启动 / 结束、事件状态机、卡死判定、定时、开机自启、备份恢复、自愈链与续跑调度。
2. `vision.py`：只做「看图」——截图、OCR、模板匹配、规则匹配、点击 / 按键。不感知业务语义。
3. `health.py`：只做「体检」——进程 / 窗口 / 日志 / 网络探测与轻量自愈动作（关窗口、刷 DNS），不做业务决策。
4. `errors.py`：异常类型、动作映射、按自然日计数与时间段判断，只决定「做什么」，不执行。
5. `update_flow.py`：更新步骤机，只描述步骤与判定点，观察与动作全部复用 `vision.py`。
6. `routers.py`：HTTP / WS 出口，只做参数校验与转调，禁止写业务逻辑。
7. `register_tools.py`：暴露给 Agent 的工具，等同 MCP 风格能力，命名 `bettergi_*`。
8. `platform/qq_official.py`：官方机器人通道，含入站命令分发。
9. 前端：`frontend/index.html` 的 `bettergi-view` + `app.js` 的 `bgi*` 函数。

### 数据流

1. 下行控制：UI / QQ 命令 / 定时器 → `BetterGiManager.launch / kill` → 子进程命令行。
2. 上行事件：BetterGI → webhook 或 WS → `handle_event` → 任务状态与事件环形缓冲 → UI 轮询 + QQ 推送。
3. 兜底闭环：`_tick` 发现卡死 → `_maybe_vision_recover` → `vision.recover_once` → 规则命中则操作，未命中则交 AI → 结果写回事件流。
4. 健康闭环：`handle_event` / 视觉耗尽 → `health.run_checks` → `run_health_chain` → 重启 / 拉起 / 关窗 / 刷 DNS → 截图推送 QQ。
5. 续跑闭环：任务结束 → `_maybe_relaunch` → 延迟排队 → `_relaunch_runner`（持 `_launch_lock`）→ 结束旧进程 → 按 `launch_mode` 重启。
6. 更新闭环：定时 / 开机计划 → `update_flow` 步骤机 → `vision` 观察 → 点击 / 滚动 / 等待 → 校验判定点 → 启动一条龙。

### 新增模块的准入

涉及「窗口截图、鼠标键盘、屏幕叠加」的代码统一放 `bettergi/` 下的独立文件，不得散落到 `computer/` 或前端。GUI 叠加必须可一键关闭，默认关闭。

## 五、接口契约

### 配置

默认值集中在 `DEFAULT_BETTERGI_CONFIG`（`service.py`）。新增键必须同时做三件事：加默认值、在 `config/settings.py` 的 `bettergi` 段落加同样键、前端 `bgiLoadConfig / bgiCollectConfig` 成对读写。缺任何一处都会导致「保存后被默认值覆盖」。

现有关键键：

1. `enabled` 总开关，关闭后不监控也不允许脚本启动。
2. `check_interval` 轮询间隔，秒。
3. `auto_restart` / `max_restarts` / `restart_reset_after` 重启策略。
4. `notify_transport` 事件通道，`webhook` 或 `websocket`。
5. `auto_shutdown` / `shutdown_delay` 自动关机。
6. `image_fallback` / `vision_stall_timeout` / `vision_max_attempts` / `vision_ai_enabled` / `vision_rules` 视觉兜底。
7. `schedule_enabled` / `schedule_kill_running` / `schedules` 定时任务。
8. `auto_start_on_boot` 开机自启。
9. `overlay_enabled` / `overlay_duration` / `overlay_scale` 红框叠加。
10. `health_check` / `retry_max` / `retry_interval` / `network_recover` / `notify_screenshot` / `game_launcher` 健康自愈。
11. `auto_relaunch_after_task` / `relaunch_modes` / `relaunch_delay` / `relaunch_max` 自动续跑。
12. `error_guard` / `monitor_mode` / `strict_miss_limit` / `loose_miss_limit` / `daily_error_limit` / `error_action_map` / `active_window_start` / `active_window_end` / `game_alive_shutdown_hours` 异常判定与模式。
13. `update_flow_game` / `update_flow_bgi` / `update_launch_onedragon` 更新流程。
14. `boot_task_plan` / `boot_task_delay` / `boot_plan_targets` 开机任务计划。

### 事件

`handle_event` 接受 BetterGI 的原始 JSON，字段至少 `event`，可选 `result` / `message`。

1. 开始类：`dragon.start`、`group.start`、`leyline.start`。
2. 结束类：`dragon.end`、`group.end`、`leyline.end`。
3. 异常类：`task.error`、`js.error`、`album.error`。
4. 取消类：`task.cancel`。

新增事件类型必须同时更新 `_TASK_START_EVENTS / _TASK_END_EVENTS / _TASK_ERROR_EVENTS` 与 `webhook_events` 默认订阅串，否则 BetterGI 不会推送。

### 状态

1. 进程态：`idle`、`monitoring`、`starting`、`running`、`restarting`、`alert`、`stopped`。
2. 任务态：`idle`、`running`、`done`、`error`、`canceled`。
3. 卡死判定参考时刻取四者最大值：任务开始、最近事件、本次启动、日志 mtime。**任何新加的「活动信号」都要并入这一处计算**，否则会误判卡死。
4. 异常类型（R9 定名，后续动作按类型分发）：`bgi_error`（BetterGI 主动回报错误）、`game_dead`（BGI 活、游戏死）、`not_started`（时间段内双未启动）、`task_timeout`（任务超时 / 游戏存活超 12 小时）、`unresponsive`（连续监测间隔无事件、日志不更新，由 R10 模式决定阈值）。
5. 模式影响阈值：`strict` 用 `strict_miss_limit`（默认 1 次），`loose` 用 `loose_miss_limit`（默认 25 次）；两者共用同一个「连续无响应计数」，切换模式只换阈值，不清计数。
6. 动作枚举：`relaunch`（结束进程后重启一条龙）、`report_only`（只回报并置 `alert`）、`shutdown`（关机，需 `auto_shutdown` 同时开启）、`self_heal`（走 `run_health_chain`）。

### 路由

1. 状态与控制：`/status`、`/config`、`/monitor`、`/detect`、`/launch`、`/kill`。
2. 事件：`/event`、`/ws`、`/notify-config`、`/setup-notify`。
3. 视觉与健康：`/vision/status`、`/vision/test`、`/vision/ensure`、`/image-recover`、`/overlay/status`、`/overlay/preview`、`/overlay/reset`、`/health/status`、`/health/check`、`/health/recover`、`/errors/status`、`/errors/reset`。
4. 计划与系统：`/schedule/status`、`/schedule/toggle`、`/relaunch/status`、`/relaunch/cancel`、`/autostart`、`/update/status|backup|restore|restart`、`/update/flows`、`/update/flow`、`/boot-plan/status`、`/boot-plan/run`、`/cancel-shutdown`。
5. QQ 官方机器人（前缀 `/api/qq-official`）：`/status`、`/config`、`/test`、`/ws/start|stop`、`/gateway/status`、`/gateway/restart`、`/validate`、`/messages`、`/commands`。

## 六、功能方案与实现记录

实现顺序（均已完成）：**A（UI 滑块）→ C（红框叠加）→ D（异常处理链）→ E（自动续跑）→ F（图案/区域识别）→ G（异常矩阵）→ B（更新流程）→ H（开机计划）→ I（QQ 网关）**。选这个顺序的原因：F 是 B 的前置，G 决定 B 出错时怎么收场，H 依赖 B，I 独立可插队。

状态：A / B / C / D / E / F / G / H / I 全部完成；仅 R5.2（自然语言提示词编排）与 R11（声明正文）未落位。

### 阶段 A：UI 滑块体系（R3）— 已完成

实现要点：`style.css` 提供 `.bgi-switch`（普通）与 `.bgi-switch-lg`（总控）两种规格，圆钮图标由 CSS `content` 在 ✕/✓ 间切换；`bgiApplyMasterState()` 负责总开关联动：`enabled=false` 时其余开关加 `.bgi-dim` 置灰但可读取，总控下方显示当前状态文案。

页面结构：总控放页面中心且尺寸明显更大；其余功能开关逐项替换复选框，特殊按钮（重启、备份、体检等）保留普通按钮样式。`bgiCollectConfig` 仍按 `checked` 取值，对外 JSON 结构不变，新增开关只需补三处（HTML、`bgiLoadConfig`、`bgiCollectConfig`）。

验收结果：实测 17 个滑块（10 个 ✕、7 个 ✓）、大总控居中、15 个标签文字无裁剪重叠；总控关闭后所有自动化开关置灰。

### 阶段 B：AI 更新流程（R5）— 已完成

实现要点：新增 `bettergi/update_flow.py` 步骤机，步骤字段为 `name / action / expect / template / target / key / times / seconds / region / timeout / interval / on_fail / optional / skip_if`；支持动作 `launch`、`wait`、`click_text`、`click_template`、`key`、`scroll`、`sleep`、`finish_if`、`wait_version_change`。每步的观察统一走 `vision.capture_window` + `recognize_scene`，点击坐标全部来自识别结果。

两套内置流程与手写稿逐条对应：`GAME_FLOW` 五步（打开启动器 → 等就绪 → 点「更新游戏」（已更新时按 `skip_if` 跳过）→ 等更新开始 → 等「开始游戏/启动游戏」）；`BGI_FLOW` 十一步（打开 → 弹窗直更 → 设置 → 划动 → 检查更新 → 读结果 → 已最新则 `finish_if` 结束 → 更新软件 → 更新 → 等左上角版本号变化 → 确认/启动/重启）。用户可在 `update_flow_game` / `update_flow_bgi` 里用 JSON 覆盖步骤，非法 JSON 自动回退内置。

`service.run_update(target)` 是唯一入口：置位 `_updating`（期间屏蔽自动关机、自动续跑与异常判定）→ 执行流程 → 结果写事件流并推 QQ → 成功且 `update_launch_onedragon` 开启时调用 `_force_restart_task` 启动预设一条龙。

接口：`GET /update/flows`、`POST /update/flow`；前端「更新与维护」新增流程状态卡与两个执行按钮，执行前二次确认，执行后逐步回显结果。

验收结果：打桩实测原神流程 5 步全部通过、点击坐标带偏移；已更新场景「更新游戏」步骤被正确跳过；`finish_if` 能提前结束；目标不出现时在超时后中止；`wait_version_change` 能识别 `1.2.3 -> 1.3.0`；接口返回原神 5 步 / BetterGI 11 步。

### 阶段 C：屏幕红框叠加（R4.3）— 已完成

实现要点：`bettergi/overlay.py` 单例工作线程（`_OverlayWorker`），tkinter `Toplevel` + `overrideredirect` + `topmost` + `-transparentcolor #010203` 做透明，Win32 追加 `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE` 实现点击穿透与不抢焦点；队列容量 16、满了丢旧帧；坐标统一为「屏幕绝对坐标 + 物理像素」，渲染时按 `scale` 与虚拟屏幕原点换算；`scale=0` 自动按 `LOGPIXELSX/96` 推算（进程非 DPI 感知时有效），高 DPI 偏移可手动填 1.25 之类。

调用链：`vision.recover_once` 只产出 `boxes`（`_screen_boxes` 已加窗口偏移）→ `BetterGiManager.vision_recover_once` 在 `overlay_enabled` 为真时调 `show_overlay` → 接口 `/vision/test` 也会返回 `boxes_drawn`。

验收结果：`/overlay/status`、`/overlay/preview`、`/overlay/reset` 均 200；`/vision/test` 实测返回 `boxes_drawn=40`，`show_boxes` 20 帧渲染后 `overlay_status().running=true`，到时自动 withdraw。

### 阶段 D：异常处理链（R7 / R8）— 已完成

实现要点：`bettergi/health.py` 提供五项检查与两个轻量动作（`close_hung_windows` 只发 WM_CLOSE、`flush_dns` 只跑 `ipconfig /flushdns`）；`service.run_health_chain` 按「重启 BetterGI → 拉起游戏 → 关无响应窗口 → 刷 DNS → 放弃」顺序执行，触发点有两处——`task.error` 事件，以及图像兜底次数耗尽；重试受 `retry_max` 与 `retry_interval` 双重约束，任务开始 / 结束时清零。

配置：`health_check`（默认关）、`retry_max`（2）、`retry_interval`（60 秒）、`network_recover`（默认关）、`notify_screenshot`（默认开）、`game_launcher`（留空跳过拉起游戏）。

说明：`retry_max` 是任务级重试上限，`max_restarts` 是进程级重启上限，两者不要混用；网络恢复默认关闭，且只做 DNS 刷新，不会动网卡。

验收结果：`/health/status`、`/health/check`、`/health/recover` 均 200；检查项返回 `key/name/ok/detail`；自愈链在打桩环境下按顺序产出三条 step，紧接着再调用被冷却拦截（还需 59 秒）。

### 阶段 E：自动续跑（R6）— 已完成

实现要点：`_maybe_relaunch` 在三个结束分支（正常结束 / `task.error` / `task.cancel`）被调用，按 `relaunch_modes` 过滤结果；延迟 `relaunch_delay` 秒后 `_relaunch_runner` 先结束现有进程再按 `launch_mode` 重启（任务参数沿用当前配置）。`relaunch_max` 封顶，达到上限后只告警不再排队；自动关机已排队时直接跳过续跑。

取消时机：新任务开始、手动 `/kill`、停止监控、关闭续跑开关、重启监控，以及页面 `/relaunch/cancel`。续跑计数只在续跑自身的启动里递增，其它来源（手动 / 定时）启动会把计数归零，避免误判成死循环。

互斥：`_get_launch_lock()` 同时被 `_run_schedule` 与 `_relaunch_runner` 持有，同一时刻只允许一个启动来源；续跑如果发现定时任务正在启动，会主动让路。

验收结果：`/relaunch/status`、`/relaunch/cancel` 均 200；打桩实测三次连续结束只有前两次真正启动，第三次命中上限；`task.cancel` 在 `modes` 之外不排队；新任务开始可撤销排队；手动取消返回成功。

### 阶段 F：图案识别 + 区域识别（R4.1 / R4.2）— 已完成

实现要点：`vision.py` 新增 `templates_dir` / `list_templates` / `match_template` / `recognize_scene` 与 `_region_rect`；模板用 OpenCV `matchTemplate`（灰度 + `TM_CCOEFF_NORMED`），对同一模板尝试 1.0 / 0.8 / 1.25 三种缩放，模板名支持省略扩展名与 `@125`、`_2x` 多倍图后缀。`match_template` 返回四点框、中心点与相似度，低于阈值时 `ok=False` 但仍回传分数，方便调阈值。

区域识别：`capture_window(save_dir, region=None)` 的 `region` 为相对窗口的 0~1 比例（也兼容像素），截图后 `offset` 直接就是区域左上角的屏幕坐标，所以「区域局部 → 窗口 → 屏幕」只需一次加法；规则支持 `region` 字段，命中判断按识别结果中心是否落在区域内过滤。

统一识别入口：`recognize_scene(image_path, rules)` 先跑 OCR 文本，再按规则跑模板匹配，两类结果统一成 `items`（`kind=text` / `kind=template`），这样红框叠加、点击、AI 决策都只看同一份结构。模板缺失或 OpenCV 不可用时自动跳过图案规则，只保留 OCR，不报错。

接口：`POST /vision/test` 现在接受 `{"region": [x1,y1,x2,y2]}`，返回 `templates`（命中的模板名）与 `template_rules`（规则里的图案规则数）；`engine_status` 增加 `opencv` / `templates` / `templates_dir`。前端视觉区块新增 4 个区域输入与模板状态行，规则占位示例也换成了带 `template` / `region` / `threshold` 的写法。

验收结果：截 1440×900 画面后裁一块 160×60 当模板，`match_template` 相似度 1.0、中心与裁剪位置误差 0；区域截图 `[0.1,0.1,0.5,0.5]` 得到 576×360、offset 与理论值一致；`recognize_scene` 合并出 120 条文本 + 1 条模板；`match_rule` 的区域过滤能正确拦截区域外命中；`/vision/test` 带区域请求返回 720×450、23 个红框、模板名正确。

### 阶段 G：异常判定与处理矩阵（R9 / R10）— 已完成

实现要点：新增 `bettergi/errors.py`，定义五种异常类型（`bgi_error` / `game_dead` / `not_started` / `task_timeout` / `unresponsive`）、四种动作（`relaunch` / `report_only` / `shutdown` / `self_heal`）与默认映射，并提供 `DailyErrorCounter`（落盘 `data/bettergi/error_count.json`，按自然日滚动）与 `in_active_window`（支持跨午夜）。

`service._guard_tick` 在每轮监测后执行判定：`game_dead` 要求「BGI 活 + 游戏死 + 任务进行中」；`not_started` 要求「两边都没启动 + 当前落在规定时间段」；`task_timeout` 用 `health.game_process_info()` 取游戏进程 `create_time`，存活超过 `game_alive_shutdown_hours` 且任务态为 `error` 才触发；`unresponsive` 用「事件/日志活动信号是否变化」累计连续无响应次数，阈值由模式决定。

`_dispatch_error` 是唯一分发入口：先查冷却（同类异常 60 秒，`not_started` 30 分钟）→ 查 `error_action_map` → 执行动作 → 记录事件并发 QQ。`bgi_error` 走 `_handle_bgi_error` 先按日计数，达到 `daily_error_limit` 才判定异常；严格模式下未达上限也即时回报。

安全约束：`error_guard` 默认关闭；`shutdown` 动作还要求 `auto_shutdown` 同时开启，否则退化为告警；`relaunch` 与定时任务、自动续跑共用 `_get_launch_lock()`。

接口：`GET /errors/status`、`POST /errors/reset`；`get_status` 增加 `errors` 段；前端新增「异常判定与模式」区块（开关、模式选择、两个无响应阈值、日错误上限、游戏存活上限、规定时间段、状态卡与重置按钮）。

验收结果：时间段判断（含跨午夜）与日计数滚动正确；日上限 2 次时第 2 次触发一次重启、冷却能拦住紧接着的重复触发；`game_dead` 只置 `alert` 不重启；`auto_shutdown` 关闭时 `task_timeout` 转为告警；严格模式连续 1 次无响应、宽松模式连续 3 次无响应均正确分发 `self_heal`；两个接口均 200。

### 阶段 H：开机任务计划（R6.3）— 已完成

实现要点：`boot_task_plan`（默认关）在 `app.py` 的 bettergi startup 钩子里调用 `boot_task_plan_startup()`，延迟 `boot_task_delay` 秒后跑 `_run_boot_plan`。判定条件：BetterGI 未运行、上次退出原因不是 `task_error`、当天未执行过；满足则按 `boot_plan_targets` 依次执行 `run_update`，结果写事件流并推 QQ，最后写入 `boot_plan_date`。

退出原因落盘在 `data/bettergi/boot_state.json`：手动结束记为 `manual`、任务完成记 `done`、任务异常记 `task_error`、取消记 `canceled`，同时保留 `last_exit_at` 与 `boot_plan_date`。

接口：`GET /boot-plan/status`、`POST /boot-plan/run`（调试用，仍受「每天一次」限制）；前端「更新与维护」新增开机计划开关、延迟、执行项与状态展示。

验收结果：上游为 `task_error` 时正确跳过；正常情况执行一次并把日期写入 state；同一天再次执行被跳过。

### 阶段 I：QQ 机器人网关（R2 细化）— 已完成

实现要点：`qq_official` 新增 `gateway_status()`（配置 + token + WS 连接 + 入站/命令计数）、`validate_config()`（真正换一次 token）与 `restart_gateway()`（只重启 WS 接收，不动配置）。

保存配置时，只要 patch 里涉及 `app_id` / `app_secret` / `target_id` / `target_type` / `enabled`，就顺带校验一次 token，失败会在返回消息里提示「配置已保存，但校验未通过」。

接口：`/api/qq-official/gateway/status`、`/gateway/restart`、`/validate`；前端「QQ 官方机器人」增加「重启网关」「校验凭据」按钮，状态行追加网关运行与入站计数。

验收结果：三个接口均 200；未启用时校验返回明确提示「QQ 官方机器人未启用」。

## 七、功能流程

下面这些流程是运行时真相，改代码前先对照，避免「实现了一套、文档写的是另一套」。

### 识别流水线

```
触发（定时 / 卡死兜底 / 手动测试）
  → capture_window(save_dir, region?)
        区域为 0~1 比例：像素框 = offset + 比例 × 窗口宽高
  → recognize_scene(path, rules)
        ├─ OCR：RapidOCR → items(kind=text)
        └─ 模板：规则里的 template → match_template
              ├─ 1.0 / 0.8 / 1.25 多尺度 → 取最高分
              └─ score ≥ threshold → items(kind=template)
  → match_rule(items, rules, image_size)
        文本规则比 text，图案规则比 template；带 region 的按中心点过滤
  → 命中：click_at(cx + offset_x, cy + offset_y) / press_key / wait
  → 落空：AI 决策（有 key）或直接返回
  → boxes = items 的四点框 + offset → overlay.show_boxes（受 overlay_enabled 控制）
```

### 主监控循环

```
后端启动
  └─ 读配置 → enabled?
        ├─ 否：idle，仅保留手动接口
        └─ 是：auto_monitor? → start_monitor
              └─ _watch_loop（每 check_interval 秒）
                    ├─ _tick：进程存在？
                    │     ├─ 是 → running，刷新 last_log_mtime
                    │     │      ├─ 卡死？→ _maybe_vision_recover → OCR/模板 → 动作
                    │     │      └─ 健康异常？→ run_health_chain
                    │     └─ 否 → starting 超时 / 重启 / alert
                    └─ 定时任务到点？→ _run_schedule（持 _launch_lock）
```

### 事件处理

```
BetterGI webhook / ws
  └─ handle_event
        ├─ dragon.start / group.start / leyline.start → task=running，清视觉与自愈计数，取消待续跑
        ├─ dragon.end / group.end / leyline.end
        │     ├─ 成功 → auto_shutdown? → 排队关机
        │     └─ 结果异常 → 通知
        │     └─ 统一出口：_maybe_relaunch（按 relaunch_modes）
        ├─ task.error / js.error / album.error → health_check? → run_health_chain；_maybe_relaunch("error")
        └─ task.cancel → _maybe_relaunch("canceled")
```

### 异常判定与分发（阶段 G 已实现）

```
每次 tick / 事件
  └─ 采信号：BGI 进程、游戏进程、窗口响应、日志 mtime、网络
        └─ 归成异常类型
              ├─ bgi_error    → 日计数+1；≥daily_error_limit → 重启一条龙
              ├─ game_dead    → 只回报 QQ，alert 等人工
              ├─ not_started  → active_window 区间内 → 回报并启动一条龙
              ├─ task_timeout → 游戏存活 > game_alive_shutdown_hours → 回报 + 关机（需 auto_shutdown）
              └─ unresponsive → 连续无响应达阈值 → 自愈链（strict 1 次 / loose 25 次）
        └─ 分发：冷却检查 → error_action_map → 执行 → 事件流 + QQ
```

### 原神更新流程（R5.3，阶段 B 已实现）

```
AI 接收任务
  → 打开米哈游启动器（固定路径 / AI 查找）
  → 截图识别「更新游戏」
  → 点击（坐标来自识别结果）
  → 静默等待几秒
  → 识别按钮状态 / 流程是否自动运行 ── 异常 → AI 兜底
  → 循环：出现「开始游戏」或「启动游戏」？
        ├─ 是 → 更新完成 → 启动预设一条龙
        └─ 否 → 继续等待（受 timeout 约束）→ 超时 → 回报 QQ 并进入异常链
```

### BetterGI 更新流程（R5.4，阶段 B 已实现）

```
接收任务 → 打开 BetterGI
  → 自动弹更新提示？
        ├─ 是 → 直接更新
        └─ 否 → 图像识别：设置 → 划动页面 → 检查更新
                ├─ 无更新 → 结束
                └─ 有更新 → 更新软件 → 更新页面 → 点击更新
  → 等待更新完成 → 点击「确认 / 启动」
  → 校验左上角版本号变化
  → 更新完成 → 启动预设一条龙
```

### 自愈、续跑、定时三者的互斥关系

```
定时任务 ─┐
自动续跑 ─┼─ 共用 _get_launch_lock() ─→ 同一时刻只有一个能启动 BetterGI
自愈链   ─┘（自愈链的重启走 restart_bgi，不占用该锁，但会更新状态机）

自动关机 pending = true 时：跳过续跑；更新流程进行中：跳过关机与续跑
```

## 八、开发规范

1. Python 3.8 语法兼容：不使用 `match`、PEP 604 联合类型（`int | None`）、`list[str]` 之类内置泛型注解。新文件首行加 `from __future__ import annotations`。
2. 阻塞操作（截图、OCR、压缩、文件遍历）一律 `asyncio.to_thread`（项目已为 3.8 做了回填，见 `zhiyan_web/__init__.py`），不要直接在主线程跑。
3. 不新增全局 pip 依赖；OCR 依赖安装到 `data/py_deps/ocr` 并在 `build_exe.spec` 中收集，保证与主程序一起打包。
4. 所有对外接口返回 `{"status": "success|error", "message": "...", "data": {...}}`，前端只依赖这三段。
5. 危险操作（关机、重启、结束进程、恢复备份、网络重置）必须二次确认，默认关闭，并在事件流留痕。
6. QQ 写操作命令必须经过 `command_allowed_groups` / `command_allowed_users` 白名单；只读命令才可放开。
7. 日志统一走 `logger = logging.getLogger("ZhiYanWeb.bettergi")`，禁止 `print`。

## 九、扩展操作手册

### 新增一个 OCR 规则

1. 在 `vision.py` 的 `DEFAULT_RULES` 或页面「自定义规则 JSON」增加一项。
2. 字段：`text`（匹配关键词，空串表示按键规则）、`action`（`click` / `key` / `wait`）、`key`（仅 key 规则使用，白名单 Escape / Space / Enter / Tab / F / E）。
3. 规则按数组顺序优先匹配，越靠前优先级越高。

### 新增一个 QQ 命令

1. 在 `platform/qq_official.py` 的 `_run_command` 增加分支，命令名同时写中文与英文别名。
2. 写操作分支自动受白名单保护，无需额外判断。
3. 更新 `bgi帮助` 文案与前端命令提示，保持三处一致。

### 新增一个 Agent 工具

1. 在 `register_tools.py` 用 `_register` 注册，命名 `bettergi_xxx`。
2. 参数描述写清枚举值与默认值，返回值统一 `json.dumps(..., ensure_ascii=False)`。
3. 工具内部只调 `get_bettergi_manager()`，不直接操作进程或文件。

### 新增一个图案模板

1. 截图裁出目标元素，保存为 `data/bettergi/templates/<场景>_<元素>.png`，只保留元素本身，不要带背景。
2. 同一元素准备 1x 与 1.25x 两种尺寸，匹配时两种都试，取相似度高的一次。
3. 规则里写 `{"template": "dialog_confirm", "action": "click", "threshold": 0.85}`，不要写死坐标。
4. 模板缺失时该规则自动跳过，不影响 OCR 规则。

### 新增一个异常类型

1. 在 `bettergi/errors.py` 的枚举里加类型，并补 `error_action_map` 默认动作（`relaunch` / `report_only` / `shutdown`）。
2. 在分发处写清「如何从信号判定成该类型」，判定条件必须可单测。
3. 同步更新本文 R9 与「功能流程」里的分发图，避免类型只存在于代码里。

### 新增或修改一个更新步骤

1. 步骤写在 `update_flow.py` 的 `GAME_FLOW` / `BGI_FLOW`，或在页面配置 `update_flow_game` / `update_flow_bgi` 里用 JSON 覆盖；非法 JSON 会自动回退内置。
2. 只用已支持的动作：`launch`、`wait`、`click_text`、`click_template`、`key`、`scroll`、`sleep`、`finish_if`、`wait_version_change`；需要新动作时在 `_run_step` 加分支，并补本文动作列表。
3. `expect` 里放实际界面文案，先用页面的「测试 OCR」确认识别结果，不要把猜测写进去；`skip_if` 用于「已经处于终态」的场景。
4. 调试时把 `timeout` 改小（如 10 秒），确认流程走通后再恢复正式值。
5. 改动步骤后同步更新第七章「原神更新流程 / BetterGI 更新流程」两张图，避免文档与代码不一致。

## 十、验收清单

1. 关闭总开关后，监控、定时、命令启动全部拒绝执行。
2. BetterGI 正在写日志时不判定卡死；日志停更且无事件超过阈值才触发兜底。
3. 事件通道切到 webhook 或 websocket 后，重启 BetterGI 能收到 `dragon.start` / `dragon.end`。
4. QQ 只读命令无需白名单；写命令在白名单外返回未授权。
5. 无网络、无 OCR 引擎、无 AI Key 三种降级场景都有明确提示，不阻塞主流程。
6. 备份能恢复，路径穿越输入被拒绝。
7. Python 3.8 语法检查通过，`build_exe.spec` 能打出带 OCR 模型的包。
8. 图像识别能同时命中文本与模板图案；模板缺失时自动降级为纯 OCR，不报错。
9. 严格模式下首次无响应即告警；宽松模式达到 `loose_miss_limit` 才告警；两者共享同一个连续无响应计数。
10. 五类异常各走各的动作：`bgi_error` 重启一条龙、`game_dead` 只回报、`not_started` 自动拉起、`task_timeout` 关机（需 `auto_shutdown`）、`unresponsive` 自愈；互不串味。
11. 严格模式 1 次无响应即触发、宽松模式累计到阈值才触发；切换模式不清空连续无响应计数。
12. 日错误计数跨软件重启保留，第二自然日自动归零。
13. 更新流程进行中不触发自动关机 / 自动续跑；更新完成后能接上预设一条龙。
14. 开机任务计划只在「BetterGI 未运行且上次非异常中止」时执行，且每天最多一次。
15. 红框叠加与区域识别同时开启时，红框位置与点击坐标一致（同一套换算）。
16. 原神流程在「已更新」状态下自动跳过「更新游戏」；BetterGI 流程在「已是最新版本」时提前结束。
17. 更新流程失败会中止并推送原因，且不会把「启动器还没拉起游戏」误判为 game_dead。

## 十一、已知坑

1. BetterGI 的 WebSocket 通知是「一条事件建一次连接、发完即断」，不能用长连接思路设计。
2. 群与 C2C 的官方主动消息依赖入站 `msg_id`，冷启动时发不出去；此时应优先推送频道或走 NapCat。
3. 自动关机必须绑定「明确的任务完成事件」，不能只靠图像判断。
4. 模拟点击易受分辨率与窗口缩放影响，优先走 BetterGI 自身能力；确需点击时先固定窗口尺寸。
5. `dist/` 中的产物不随源码更新，验证新功能必须重新打包或用源码模式运行。
6. RapidOCR 返回的 `box` 是 numpy 数组，写 `item.get("box") or []` 会抛 ValueError（数组真值歧义），必须先判 `is None`。
7. tkinter 窗口只能在创建它的线程里操作，叠加层所有渲染都通过队列回到工作线程执行，主线程只做 `put`。
8. 自愈链会真的重启 BetterGI / 拉起游戏，联调时务必先打桩 `restart_bgi` 与 `_launch_game`，否则会在本机反复启动进程。
9. `run_health_chain` 的冷却与重试计数是「任务级」的，任务开始 / 结束事件会清零；不要把它当成进程级 `max_restarts` 使用。
10. 续跑计数不能在「任务开始」事件里清零——续跑会触发新的任务开始，清零后就会无限循环；只在非续跑来源的 `launch()` 里归零。
11. 原稿的「12 小时」约束的是**游戏进程存活时长**（R9.4），不是任务时长；计时起点取游戏进程的 `create_time`，不要用任务开始时间。
12. 日错误计数必须落盘并带日期键（`{日期: 次数}`），否则软件重启后清零，永远到不了 3 次。
13. 严格 / 宽松模式只换阈值，**不要清空「连续无响应计数」**，否则切换模式会顺手绕过一次告警。
14. `game_dead` 要求「只回报、不继续处理」，与自愈链「拉起游戏」直接冲突；分发必须先判异常类型，再决定是否进自愈链，否则两条逻辑会互相抢跑。
15. 更新流程的按钮位置随版本变化，模板与坐标都不能写死；每次更新结束都应重新截图校准一次判定点（原神看「开始/启动游戏」，BetterGI 看左上角版本号）。
16. 模板匹配对「同分辨率、同窗口尺寸」最准；模板务必在目标机同分辨率下截取，换分辨率后靠 0.8 / 1.25 多尺度兜底，偏差仍大就重新截模板。
17. `/vision/test` 是手动测试入口，会无视 `overlay_enabled` 直接画红框；自动兜底仍受该开关控制，两者不要混用。
18. `error_guard` 与 `health_check` 同时开启时，同一份 `task.error` 会同时触发异常矩阵和自愈链，可能重复重启；生产上建议只开其中一个，或把 `error_action_map` 里的类型改走 `report_only`。
19. 「无响应」的判定只比「事件 ts + 日志 mtime」是否变化，不包含窗口画面变化；游戏在加载但日志不写时会误判，宽松模式默认 25 次就是为了兜这个场景。
20. 内置更新流程的超时很长（原神等待完成 3600 秒、BetterGI 等版本号 1800 秒）；调试时务必用自定义短流程覆盖 `update_flow_game` / `update_flow_bgi`，否则一次跑错就要等很久。
21. 步骤机依赖 OCR 关键词，版本或语言不同可能识别不到（如「更新游戏」变成「获取游戏」）；上线前先用「测试 OCR」确认实际文案，再改 `expect` 列表。
22. `boot_state.json` 的 `boot_plan_date` 会在开机计划执行后写入当天日期；手工测试后记得清空，否则当天真实开机不会再执行。
23. 打包风险：`vision._load_cv2()` 是函数内懒加载，`build_exe.spec` 只显式收集了 `rapidocr` / `onnxruntime`，没有收集 `cv2`；打包后若模板匹配报「缺少 opencv-python」，需要在 spec 里补 `collect_submodules("cv2")` 与 `collect_dynamic_libs("cv2")`。

## 十二、实现索引与运维速查

### 代码索引

| 文件 | 职责 | 关键入口 |
| --- | --- | --- |
| `bettergi/service.py` | 唯一状态源、事件状态机、自愈/续跑/异常/更新/开机计划调度 | `BetterGiManager`、`handle_event`、`_tick`、`run_health_chain`、`_maybe_relaunch`、`_dispatch_error`、`run_update`、`_run_boot_plan` |
| `bettergi/vision.py` | 截图、OCR、模板匹配、区域裁剪、点击/按键/滚动 | `capture_window`、`recognize_scene`、`match_template`、`recover_once`、`click_at`、`scroll` |
| `bettergi/health.py` | 五项健康检查与轻量自愈动作 | `run_checks`、`game_process_info`、`close_hung_windows`、`flush_dns` |
| `bettergi/errors.py` | 异常类型、动作映射、日计数、时间段判断 | `DailyErrorCounter`、`normalize_action_map`、`in_active_window` |
| `bettergi/overlay.py` | 屏幕红框叠加 | `show_boxes`、`overlay_status`、`reset_overlay` |
| `bettergi/update_flow.py` | 更新步骤机 | `GAME_FLOW`、`BGI_FLOW`、`run_flow`、`load_flow` |
| `bettergi/routers.py` | HTTP/WS 出口 | `/api/bettergi/*` |
| `bettergi/register_tools.py` | Agent 工具注册 | `bettergi_*` |
| `platform/qq_official.py` | 官方机器人发送/接收/网关 | `send_text`、`send_image`、`gateway_status`、`restart_gateway` |
| `frontend/index.html` + `static/js/app.js` | 托管页全部交互 | `bgi*` 函数 |

### 运行时数据文件

1. `data/settings.json`：全部配置（`bettergi` 段），保存即生效。
2. `data/bettergi/error_count.json`：当日错误计数 `{date, counts}`。
3. `data/bettergi/boot_state.json`：`last_exit_reason` / `last_exit_at` / `boot_plan_date`。
4. `data/bettergi/templates/`：图案模板（用户资产，需随包或手动拷贝）。
5. `data/bettergi/vision/`：识别截图（体积会增长，可定期清理）。
6. `data/bettergi/backups/`：配置备份。
7. `data/py_deps/ocr/`：本地 OCR 依赖与模型。

### 排障顺序

1. `GET /api/bettergi/status`：先看 `state`、`task_state`、`last_error`、`stall_seconds`。
2. 看页面「事件日志」或 `status.events`：确认最后一次事件时间与内容。
3. 事件不通：`/notify-config` 看 webhook/WS 是否写入，必要时 `/setup-notify` 重写。
4. 识别不对：`POST /vision/test`（可带 `region`）确认 OCR 文本与模板命中；先校准关键词再做规则。
5. 异常处理不对：`/errors/status` 看模式、阈值、当日计数与动作映射；必要时 `/errors/reset`。
6. 更新流程卡住：看事件流里的「第 N 步」记录，用自定义短流程复现；确认目标文案是否变化。
7. 开机计划没跑：`/boot-plan/status` 看 `last_exit_reason` 与 `plan_date`。

### 上线前检查

1. `python -m py_compile` 覆盖 `bettergi/*.py`、`platform/qq_official.py`、`config/settings.py`；`node --check frontend/static/js/app.js`。
2. 源码模式跑一次：状态卡、事件流、视觉测试、异常状态、更新流程状态、网关状态六块都能正常渲染。
3. 重新打包 `dist/`、`dist38/`；打包后重点验证 OCR、模板匹配（cv2）、叠加层、pywin32 相关检查。
4. 危险开关（`auto_shutdown`、`health_check`、`error_guard`、`auto_relaunch_after_task`、`network_recover`、`boot_task_plan`）保持默认关闭，交付说明里写清开启后果。
