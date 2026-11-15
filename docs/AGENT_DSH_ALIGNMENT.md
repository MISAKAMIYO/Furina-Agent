# Agent 过程区 / 输入区：对齐 DeepSeek Harness

> 目标：芙宁娜主题配色不变（`docs/UI_STYLE_SYSTEM.md` LOCKED），结构、信息密度、交互按 DeepSeek Harness（DSH）重做。
> 更新：2026-11-14（第 1 轮）

## 已完成（第 1 轮）

### 后端事件协议（`backend/zhiyan_web/core/llm/chat_manager.py`）

| 改动 | 位置 | 说明 |
| --- | --- | --- |
| `tool_call` 带 `call_id` | 工具循环入口 | 用 LLM 的 tool_call_id，缺失时生成 `call_<hex>`，前端可精确定位工具行 |
| `tool_result` 带 `call_id / output / duration_ms / exit_code` | 工具执行完 | `output` 最多 8000 字符（原 300），`duration_ms` 为真实耗时，`exit_code` 从 `returncode/exit_code` 提取 |
| 新增 `usage` 事件 | LLM 返回后 | `{usage, model}`，前端累计成 meta 行（↑输入 ↓输出） |
| `_extract_exit_code()` | 工具结果辅助 | 只认 `exit_code/exitCode/returncode`，不误取业务 `code` |

### 前端过程区（`frontend/static/js/app.js`）

| 改动 | 位置 | 说明 |
| --- | --- | --- |
| 修复正文被抹掉 | `case 'thinking'` | 原来每步 `innerHTML=''`，DSH 内核每步都发 thinking → 最终空白；现只在没有正文时更新占位 |
| `done` 兜底正文 | `case 'done'` | 没有 chunk 时用 `data.content` 补回正文 |
| 工具行按 `call_id` 回填 | `case 'tool_result'` | 原来固定「最后一张卡」，并发/乱序会错配；无 call_id 时仍退回最后一张 |
| 工具行耗时徽标 | `_updateToolCardResult` | 右上角 `1.2s / 830ms`（`_formatDurationMs`） |
| 退出码徽标 | 同上 | 优先用后端 `exit_code`，否则回退文本解析 |
| 完整输出 | 同上 | OUT 展示 6000 字符，超出标注「已截断，共 N 字符」，`.dsh-tool-output` 限高 320px 可滚动 |
| 思考计时器复活 | `_createStreamMessage` | 新增 `.message-elapsed` 节点（原来 `stream.timerEl` 从未赋值，计时函数是死代码） |
| token / 模型 meta | `_renderStreamUsage` | hover 消息时显示 `deepseek-chat · ↑1200 ↓340` |
| 发送键复位 | `_restoreSendButton` | 复位 `title=发送`、`disabled=false`（原来 tooltip 永远停在「停止生成」） |
| 流收尾复位 | `sendAgentMessage` 末尾 | 终止/报错时也把 `_currentAgentStream` 置空，否则后续消息会永远排队 |

### 前端输入区（Composer）

| 改动 | 位置 | 说明 |
| --- | --- | --- |
| 忙时排队 | `sendMessage()` + `_pendingMessages` | 回合未结束时 Enter 入队，`done/error/终止` 后自动续发 |
| 队列条 | `#chat-queue`（`index.html`） | 「排队中 N 条」+ 每条「插话 / ×」 |
| 插话 | `_interruptWithQueued` | 中止当前回答并立刻改发该条（DSH steering 的等价实现） |
| 草稿保护 | `_flushPendingQueue` | 自动续发时不清掉用户正在输入的新草稿 |

### 样式（`frontend/static/css/style.css` 末尾增量段）

`.chat-queue*`、`.message-elapsed`、`.message-usage`、`.dsh-tool-duration`、`.dsh-tool-output*`；
深色主题下聊天仍用亮色壁纸，浅色文字会糊，故 `html.dark` 下给工具行/思考行加 66% 表面、工具体加 55% 表面（浅色主题不变）。

## 验证

- `node --check frontend/static/js/app.js`、`python -m py_compile chat_manager.py` 通过。
- Playwright + 系统 Edge 无头跑真实页面，注入 `tool_call/tool_result/usage/done` 事件后断言：2 个工具行、1 个失败行、`退出码 1`、耗时 `['1.2s','830ms']`、`↑1200 ↓340`、`思考耗时 6.4s`、队列 2 条、2 个 OUT 区，无 pageerror。
- 截图：`.dsh-acl-repair/agent-dsh-light.png`、`agent-dsh-dark.png`。


## 第 2 轮（2026-11-14）

### `@` 文件引用（新增）

- 后端新增 `GET /api/chat/files?q=&limit=`（`chat/routers/chat.py` 末尾）：有界遍历工作区（跳过 .git/node_modules/dist/build/data/logs/备份，深度 <= 6，上限 4000 个文件），2 分钟缓存，按「文件名前缀命中 > 路径长度」排序。
- 前端 `index.html` 新增 `#at-palette`（复用 `.cmd-palette` 样式）；`app.js` 新增 `_onAtInput/_onAtKeydown/_pickAtPalette/_renderAtPalette/_fetchAtFiles`，输入 `@xxx` 弹层、↑↓ 选择、Enter/Tab 插入、Esc 关闭，旧响应用序号丢弃。
- 发送前 `_expandAtFileRefs()` 把 `@路径` 汇总成「📎 引用工作区文件」清单追加到正文，模型据此用 read_file 精读。
- 弹层标题显示索引根目录名（如「引用工作区文件 · agent测试」），避免用户误以为在搜项目源码。

### 工具行分组（新增）

- 同一回合连续同类工具合并成一行：`×N` 徽标、每条 IN 追加、每条 OUT 追加（不互相覆盖）；`chunk/reasoning/interim` 会打断分组。
- 新增 `.dsh-tool-count` 样式。

### 验证（第 2 轮）

- `GET /api/chat/files?q=py` 返回 4 条命中（总 10）。
- Playwright：输入 `@py` → 弹层可见、4 个候选；回车插入 `@hello.py `（不再误发消息）；`_expandAtFileRefs` 产出引用清单。
- 注入 3 次 `read_file` → 1 行、`×3`、3 个 IN、3 个 OUT，无 pageerror。截图 `.dsh-acl-repair/agent-dsh-group.png`。

### 第 2 轮踩坑

- `_renderAtPalette` 里误用了不存在的 `_formatFileSize`（真实函数名是 `formatFileSize`），异常被 `.catch` 吞掉导致弹层永远不出现。E2E 必须先断言「弹层可见」，否则只会看到输入被清空。
- 端到端测试不要往真实 `#message-input` 里回车：没有候选时 Enter 会直接发送，把测试消息写进用户的真实会话（本次已从 `data/chat_sessions/*.json` 清理并复原到 19 条）。


## 第 3 轮（2026-11-14）

| 改动 | 位置 | 说明 |
| --- | --- | --- |
| 上下文用量环 | `index.html` Composer 工具条 + `_renderContextRing` | SVG 环 + 百分比，hover 显示「上下文 12.3k / 65.5k（18.8%）」；>=75% 转警告色、>=90% 转危险色；首屏与每轮结束各刷新一次 |
| 审批停靠区 | `#chat-approval-dock` + `_showApprovalDock/_hideApprovalDock` | 审批同时出现在输入区上方，滚动到历史也看得见；`_resolveApprovalCard` 现在同时处理消息流卡与停靠卡，done/error 自动清除 |
| 图片预检 | `addFiles()` | 单文件 20MB、图片最多 6 张，超限弹提示并跳过 |
| 图片灯箱 | `_openAttachLightbox` | 点缩略图看大图（原来只有「点击查看大图」的假提示） |

验证（Playwright）：
- 环：12345/65536 → 19%，title 正确；0 token → 0%；60000/65536 → danger。
- 审批：停靠区可见、2 个按钮，允许后停靠区消失且消息流卡显示「已允许本次操作」，done 后停靠区清空。
- 图片：21MB 文件被拒（附件数 0）；PNG 缩略图渲染、点击开灯箱、再点关闭。


## 第 4 轮：打磨（2026-11-14）

| 改动 | 位置 | 说明 |
| --- | --- | --- |
| 计划块进消息流 | `_syncPlanToStream()` | `todo_list` 事件除了顶部 dock，还在当前回合工具区最上方渲染「计划 · N 完成 · N 进行中」；全部完成自动收成一行（`.dsh-plan-card.collapsed`） |
| 富渲染接线 | `_renderRichToolBody()` | `read_file` → 带行号的 `.dsh-read-block` 卡片；`write_file/edit_file` → 路径 + 体积卡；其它工具退回纯文本 OUT。`renderReadCard` 等死代码终于有调用点 |
| 编辑并重发 | `editUserMessage()` + `.msg-edit-box` | 用户气泡操作条新增「编辑」，就地编辑后保存：删掉原消息（界面 + `/api/chat/history/delete-message`），再以新内容重发；草稿不受影响 |

验证（Playwright，`e2e_polish.py`）：
- 计划块：消息流内 1 个 `.dsh-plan-card`、3 项、标题「计划 · 1 完成 · 1 进行中 · 1 待处理」，全完成后 `collapsed`。
- 富渲染：`read_file` 结果渲染出 `.dsh-read-block` 与 3 行带行号内容。
- 编辑重发：编辑器打开 → 改成新文本 → 保存后旧消息消失、新消息出现，无 pageerror。

## 第 4 轮踩坑

- 测试点「编辑」时用了 `.message.user` 的第一个匹配，结果编辑的是会话历史里的真实消息（因为页面会加载历史）。改成取最后一个用户气泡。删除接口在测试里被 stub，未影响后端数据。
- 已完成会话里若有 `assistant` 空内容消息属正常（纯工具调用轮），不是丢消息。


## 第 5 轮：思考过程落盘与启动回读（2026-11-14）

问题：`reasoning_content` 其实一直写在 `data/chat_sessions/*.json` 里（`Conversation.to_dict` 有），但 `display_messages()` 只回吐 `content/time/think_ms`，所以重启后思考过程在界面上消失。

改动：
1. 后端 `chat_manager.py:display_messages()`：assistant 消息带 `reasoning_content` 时一并回吐（工具中间态仍不展示）。
2. 前端 `app.js`：`addMessage()` 对带 `reasoning_content` 的历史消息渲染 `_historyReasoningRowHtml()` —— 折叠的「思考 · 首行摘要 · 耗时」行，点开看完整思考；有思考行时不再重复显示「思考耗时」小字。
3. 样式：`.dsh-reasoning-row.history` 在浅色壁纸上加 52% 表面（深色 72%），否则浅灰字看不清。

验证：
- `GET /api/chat/history` 返回 9 条界面消息，其中 3 条带 `reasoning_content`。
- Playwright：页面加载出 3 个 `.dsh-reasoning-row.history`，摘要与耗时（22.0s / 33.6s / 19.7s）正确，点击后 `body.open` 展开。
- 回归：工具行/失败行/usage/队列测试仍全绿（`e2e_agent_ui`）。

注意：只对「之后新产生的消息」有完整思考文本；历史里没有 `reasoning_content` 的旧消息仍然只显示内容。


## 第 6 轮：字体颜色自定义 + 思考文本复制/批量折叠（2026-11-14）

1. 设置 → 高级配置新增「聊天文字颜色」区块：正文色 / 次要文字色两个取色器 + 十六进制输入，浅色与深色各存一份（`localStorage.zhiyan_font_colors`），切换主题自动套用。
2. 应用方式：只覆盖 `--ink` / `--ink-2` / `--ink-3` 三个文字令牌，不动背景与按钮；`_applyFontColors()` 在启动、切主题、改色时执行。
3. 一键加深 / 立即预览 / 恢复默认三个快捷操作。
4. 思考行新增 hover 复制的按钮（实时行取累积文本，历史行取 DOM），以及聊天「⋯」菜单里的「展开全部思考 / 折叠全部思考」。

验证：
- 取色器存在；`_setFontColor('ink', '#123456')` 后 `--ink` 立即变、写入 localStorage；切深色用深色那份、切回浅色恢复 #123456。
- 3 条历史思考行：批量展开后 3 个 `.open`，折叠后 0；点复制按钮后剪贴板拿到思考文本。
- 回归：工具行/失败行/usage 测试仍全绿。


## 第 7 轮：新增 10 个 Agent 工具 + 工具卡片富渲染（2026-11-14）

### 新增工具（`computer/tools/extra_ops.py`，全部注册进 `core` 分组）

| 工具 | 类型 | 说明 |
| --- | --- | --- |
| `read_file_lines` | 读 | 按行段读大文件，返回起止行/总行数/是否还有更多（单次上限 2000 行） |
| `grep_files` | 读 | 正则检索文件内容，返回文件/行号/整行（跳过二进制与 >2MB 文件） |
| `file_hash` | 读 | md5/sha1/sha256 指纹 + 体积 |
| `diff_files` | 读 | 两文件 unified diff + 新增/删除行数 |
| `list_tree` | 读 | 目录树（深度/条数受限，跳过 .git/dist/node_modules 等） |
| `csv_preview` | 读 | 表头 + 前 N 行 + 总行数 |
| `zip_list` | 读 | zip 清单 |
| `append_file` | 写 | 追加写入（不存在则创建） |
| `unzip_file` | 写 | 解压到工作区，跳过目录穿越条目 |
| `delete_file` | 写 | 移入回收站（`dsh.trash`，可还原），**恒需审批**一次 |

- 路径统一走 `file_ops._check_workspace`：读全盘可读，写限工作区或经审批放行。
- `approval_broker._WRITE_TOOLS` 补 `append_file` / `unzip_file`；`delete_file` 在任何非完全控制模式下都弹审批（删除是破坏性操作）。
- 注册后电脑操控工具 73 → 83 个，全部落在 `core` 分组，chat/coding 场景都能用。

### 卡片打磨

1. 富渲染新增：`grep_files`（命中清单，文件名:行号 + 代码行）、`diff_files`（+/- 着色 diff 块）、`read_file_lines`（带行号读取卡）、`list_tree`（缩进树）、`file_hash`（指纹块）。
2. 图标/中文别名补齐：检索内容 / 文件对比 / 按行读取 / 目录树 / 文件指纹 / CSV 预览 / 压缩包清单 / 追加写入 / 解压 / 删除到回收站。
3. **工具行展开后的 IN 不再截断 60 字**：长参数完整展示在限高滚动框（`.tool-arg-long-full`），超 4000 字符时才截断并标注总长度。

### 验证

- 单元测试 `test_extra_tools.py`：10 项全 PASS（读行、正则检索、指纹、diff、目录树、CSV、zip 清单、追加、解压防穿越、回收站删除）。
- 注册验证：脚本内新建 `FunctionToolManager` 注册后共 102 个工具，10 个新工具全部在列；异步 handler 实调返回 JSON。
- 真机启动：日志「已注册 83 个电脑操控工具」，无异常。
- Playwright：注入 5 类新工具事件 → `.dsh-grep-line`×2、diff add/del 各 1、`.dsh-read-line`×3、`.dsh-tree-line`×2、`.dsh-hash` 64 字符；写入行展开后 `.tool-arg-long-full` 长度 300（未截断），无 pageerror。


## 第 8 轮：终端卡片 / 计划项手动操作 / 会话分支 / 文件检查点（2026-11-14）

### 1. 终端卡片对齐 DSH terminal
命令类工具的内联输出块重做头部：`$ 命令` + 右侧状态胶囊（完成 / 失败 / 超时 / 已取消，带 exit code），圆角统一到 `--ui-radius-md`；输出区仍是等宽字体 + 限高滚动，stderr 红色。`_handleCmdToolResult` 现在优先读后端 `tool_result.output`（原来只读 `preview`，完整输出被丢掉）。

### 2. 计划项手动勾选 / 编辑
- 计划项左侧图标可点：切换完成状态（`_toggleTodoItem`），dock 与消息流内的计划块同步刷新。
- 计划文本双击进入编辑：`contenteditable`，回车保存、Esc 取消（`_editTodoItem`）。
- 有活跃回合时以消息流内的计划块为准，自动隐藏顶部 dock，避免同一份计划出现两次。

### 3. 会话分支（fork）
- 后端 `Conversation.visible_cut_index()` / `fork_messages()` + `ChatManager.fork_conversation()`：按「可见消息条数」截断，深拷贝消息（含工具中间态），新会话标题「原标题 · 分支」。
- 新接口 `POST /api/chat/sessions/{id}/fork`，参数 `{keep_display, title?}`，返回新 session_id 与会话列表。
- 前端：助手消息操作条新增「分支」按钮 → 确认 → 创建并自动切过去。

### 4. 文件级检查点（回滚）
- 新模块 `backend/zhiyan_web/agent/checkpoints.py`：写操作前把目标文件快照到 `data/checkpoints/<conv>/<id>/`（单文件上限 5MB，超限标记 skipped；目录只记存在性）。
- 在 `chat_manager` 工具执行前自动快照（失败只记 debug，不阻塞）。
- 接口：`GET /api/chat/checkpoints?conv_id=`、`POST /api/chat/checkpoints/{id}/restore`、`DELETE /api/chat/checkpoints/{id}`。
- 回滚语义：原本存在的文件写回快照内容；原本不存在的文件/目录移入回收站删除（可再还原）。
- 前端：聊天「⋯」菜单 →「文件检查点」→ 列表（工具、时间、路径）→ 回滚 / 删除快照。

### 验证
- `test_fork_ckpt.py`：12 项全 PASS（可见消息截断、深拷贝、快照存在/不存在、列表、回滚内容、回滚清理新建文件、丢弃、非写工具不快照）。
- `test_fork_ckpt_api.py`：12 项全 PASS（fork 接口成功/会话不存在、检查点列表/回滚/删除接口、终端 `$` 与命令文本、计划项点击勾选、分支按钮存在、检查点对话框）。
- 副作用：测试产生的空会话不落盘；`data/checkpoints/selftest_conv` 是一个空目录，无快照残留。


## 第 9 轮：音乐播放器 v2（2026-11-14）

参考项目（只取设计语言，跨技术栈未复用代码）：
- `Linsxyx/KugouMusic.NET`（Avalonia）：底栏三段式播放条（BottomPlaybackControl）、队列列表（PlaybackQueueControl）、分段式 Tab（SegmentedStyles）、圆形封面 + 主题切换。
- `chthollyphile/folia-major`（React/Electron，"Lyrics Reimagine"）：歌词优先的沉浸式布局、封面模糊背景、当前行大字高亮。

改动：
1. **布局**：`#music-view` 变成「封面 + 歌词」左区与「队列」右区，底部固定播放条（当前曲目缩略图/标题/歌手 + 传输控制 + 音量）。浮动窗口用**容器查询**（`container-type: inline-size`）适配——媒体查询看不到浮动窗口尺寸，这是关键坑。
2. **底栏**：新增 `.np-bar-info`（封面缩略图 + 歌名 + 歌手），通过 MutationObserver 与主封面/歌名同步；进度条压成 4px 细线置于底栏顶部。
3. **歌词**：上下渐隐遮罩、当前行放大加粗 + 光晕、相邻行提亮；空态显示引导文案与「获取歌词」。
4. **队列**：Tab 改胶囊分段；曲目行统一编号列（复用原有 `.song-index`，不再叠加 CSS counter）、激活行左侧主色描边 + 均衡器图标；hover 高亮。
5. **背景**：`#player-bg` 加深模糊与饱和度，叠主题径向渐变，避免亮壁纸把文字冲淡。

验证：Playwright 打开音乐视图 → 加载 180 首播放列表 → 选首曲，封面 190×255、歌词区 255 高、底栏 96 高，三段都在窗口内；底栏标题/封面与主封面同步；浅色/深色各截图；无 pageerror。


## 第 10 轮：音乐播放器 v2 收尾（频谱 / 拖拽排序 / 封面墙，2026-11-14）

1. **实时频谱**：新增 `#np-visualizer` canvas + Web Audio `AnalyserNode` 接在全局 `audioPlayer` 上（`createMediaElementSource` 只能建一次，且必须 `connect(destination)` 否则会静音）；48 根圆角柱、主题色渐变、DPR 自适应；`play`/`pause` 事件驱动启停，`suspended` 状态下自动 resume。
2. **队列拖拽排序**：`.playlist-item` 设 `draggable`，`#playlist` 上做事件委托（dragstart/dragover/drop/dragend），drop 时把歌曲从原位置移到目标位置、切到「自定义顺序」、`POST /api/music/playlist/save` 落盘。
3. **封面墙**：工具栏新增切换按钮，`#playlist.cover-wall` 变 3 列网格（封面 + 标题 + 歌手），偏好存 `localStorage.musicPlaylistWall`，激活项描边高亮。
4. 排序下拉补「自定义顺序」选项。

验证（Playwright）：
- 拖拽：第一首从全局 index 174 移到 index 1，`sortBy` 变 `custom`，随后 `POST /api/music/playlist/save` 返回 200。
- 频谱：选曲后 `AudioContext` 状态 `running`，画布尺寸 617×108（DPR 缩放生效）。
- 封面墙：`grid-template-columns` 为 3 列。
- 无 pageerror。

副作用说明：测试中调用了一次保存接口，`data/playlist.json` 被重写为 180 首的「已存字段」版本（内容不变，字段按 `filename/title/artist/path` 规整）。


## 第 11 轮：逐字卡拉 OK + 桌面歌词浮窗（2026-11-14）

### 逐字歌词
1. 后端 `lyrics_parser.parse_lrc_text()` 支持增强型 LRC（A2）的 `<mm:ss.xx>` 词级时间戳：输出每行额外带 `words: [{t, text}]`，纯文本/普通 LRC 行为不变。
2. 前端 `renderLyrics()`：有 `words` 时按词渲染（`data-t`），否则退回逐字切分。
3. `_updateKaraoke()`：按词时间点亮（`highlighted`），无词级时间时按「行起点 → 下一行起点」线性比例点亮；缓存已点亮下标，避免每次 timeupdate 全量改 DOM。
4. 样式：已唱词用主色 + 柔光，未唱词保持低饱和。

### 桌面歌词浮窗
1. 后端 `POST/GET /api/music/now-playing`：音乐页上报 `{title, artist, playing, time, line:{text,words}, next}`，内存保存。
2. 音乐页 `_postNowPlaying()` 节流 600ms，在 `timeupdate` / play / pause 时上报。
3. `frontend/static/lyrics_overlay.html`：每 350ms 轮询，当前行逐词点亮、下一行预览、顶部歌名与播放状态；`pywebview-drag-region` 支持拖动。
4. `desktop.py` 新增 `open_lyrics_overlay(url)` / `close_lyrics_overlay()`：无边框 + 置顶 + 透明 + `easy_drag=False`，900×170。
5. 音乐页歌词栏新增「桌面歌词」按钮：桌面壳内走 `pywebview.api.open_lyrics_overlay`，浏览器里退回 `window.open`（浏览器窗口无法置顶）。

验证：`e2e_karaoke_overlay.py` 8/8 PASS —— now-playing 上报/读取、浮窗渲染与逐词点亮（5.6s 时点亮 2 个词）、下一行预览、主界面逐词点亮（1.7s→2 个、2.5s→3 个），无 pageerror。


## 第 12 轮：接入酷狗音乐 + 修网易云二维码（2026-11-14）

### 酷狗音乐接入（参考 KugouMusic.NET 的签名实现）
读 `src/Libraries/KuGou.Net/util/KGSigner.cs` + `Http/Handlers/SignatureHandler.cs` 还原出协议：
- 通用签名 = `md5(salt + 按 key 排序的 k=v + body + salt)`，salt=`LnT6xpN3khm36zse0QzvmgTZ3waWdRSA`
- 默认参数 `appid=3116 / clientver=11440 / dfid="-" / mid=十进制(md5(installGuid)) / uuid=md5(dfid+mid) / userid=0 / clienttime`
- `/v5/url` 额外 `key = md5(hash + V5KeySalt + appid + mid + userid)`
- 请求头要带 `dfid / mid / clienttime / kg-rc / kg-thash / kg-rec / kg-rf`，路由头 `x-router`

实现：
- `music/services/kugou_webapi.py`：搜索（complexsearch v3）、取播放地址（trackercdn v5）、酷狗歌词库；搜索 90s 缓存、URL 10 分钟缓存。
- `music/routers/kugou.py`：`/api/kugou/search`、`/url`、`/lyrics`、`/stream`（后端代理音频 + Range 透传，绕开 CDN 的 Referer 校验）。
- 前端：右栏新增「酷狗音乐」Tab（搜索 → 结果列表 → 点播），播放走 `/api/kugou/stream`，封面/歌词/底栏同步复用现有播放器。

已知限制：VIP/付费歌曲返回 `fail_process=["pkg","buy"]`，无播放地址，前端提示「需要酷狗会员」；搜索接口本身 3~8 秒，已加缓存与“较慢”提示。

### 网易云二维码修不好？其实是容器类型错了
- 根因：`new QRCode(...)` 的容器是 `<canvas>`，qrcodejs 会往容器里 `appendChild(<canvas>/<img>)`，而 canvas 的子节点不会被渲染 → 二维码永远空白。
- 修复：容器改成 `<div id="netease-qr-canvas">`；同时把 `qrcodejs` 从 cdnjs 换成本地 `static/js/qrcode.min.js`（离线可用），并加“组件未加载时给登录链接”的兜底。

验证：Playwright 点击网易云 Tab，9 秒内出现 `canvas+img` 两个节点，状态变「请使用网易云音乐 App 扫描二维码」；酷狗搜索 20 条、点播后 `audioPlayer.src` 指向 `/api/kugou/stream?hash=...`；全程无 pageerror。


## 第 13 轮：酷狗登录 + 读取用户歌单（2026-11-14）

协议（照 `KuGou.Net/Protocol/Raw/RawLoginApi.cs` + `RawUserApi.cs` + `RawPlaylistApi.cs`）：
- 取二维码：`GET https://login-user.kugou.com/v2/qrcode`，Web 签名盐 `NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt`，参数含 `appid=1001/clientver=11040/srcappid=2919/type=1/plat=4/qrcode_txt`，且必须带 `dfid/mid/uuid/userid/clienttime` 默认参数；返回 `data.qrcode` 与 `data.qrcode_img`（base64 PNG）。
- 轮询：`GET /v2/get_userinfo_qrcode?qrcode=key`，用 `data.status` 判状态（0 过期 / 1 等待 / 2 已扫码 / 4 确认并带 token）。
- 我的歌单：`POST https://cloudlist.service.kugou.com/v7/get_all_list`，body `{userid,token,total_ver:979,type:2,page,pagesize}`，签名 = `md5(salt + sorted(k=v) + body + salt)`。
- 歌单歌曲：`POST /v4/get_list_all_file`，body `{listid,userid,token,page,pagesize,...}`。

实现：
- `kugou_webapi.py` 增加 `qr_key / qr_check / user_playlists / playlist_songs / load_session / save_session / clear_session`，登录态落盘 `data/kugou_session.json`，进程重启自动恢复。
- 路由新增 `/api/kugou/{session,login/qr,login/qr/check,logout,playlists,playlist}`。
- 前端酷狗 Tab 顶部账号条：未登录显示「登录酷狗」，弹出二维码卡片并每 2s 轮询；登录后显示昵称 + 「我的歌单」+ 退出；歌单点进去看歌曲、可直接点播（复用 `/api/kugou/stream`）。

验证：`/api/kugou/session` 返回未登录；`/api/kugou/login/qr` 返回 key + 1.2KB base64 图；页面点「登录酷狗」5 秒内二维码渲染（naturalWidth 387），提示「请用酷狗音乐 App 扫码」；未登录点「我的歌单」提示「请先登录酷狗」；无 pageerror。
**需要你把手机扫码这一步走完**，登录态才会写盘、歌单接口才会真正返回数据。


## 第 14 轮：酷狗歌单修通 + 清理遗留按钮（2026-11-14）

### 歌单读取失败的两个根因
1. **证书主机名不匹配**：`cloudlist.service.kugou.com` 的证书与主机名不符（`SSL: CERTIFICATE_VERIFY_FAILED ... Hostname mismatch`）。酷狗自己的证书配置问题，客户端 `httpx.Client(verify=False)` 解决；token 仍走 TLS 加密传输。
2. **字段映射错**：歌单歌曲接口（`/v4/get_list_all_file`）返回的是 `name / singerinfo[] / timelen(ms) / media_pay_type`，不是搜索接口那套 `FileName / SingerName / Duration`。已单独解析：标题按 `" - "` 拆歌手和歌名、去掉 `.mp3` 后缀、`timelen/1000` 转秒、`media_pay_type` 当付费标记。

验证（用你已登录的 Minecraft 账号）：`/api/kugou/playlists` 返回 13 个歌单（默认收藏 / 我喜欢 548 首 / 星之启航G喜欢的音乐 966 首 …）；`/api/kugou/playlist?id=2` 返回歌曲，标题/歌手/时长正确；页面显示「已登录：Minecraft + 我的歌单」，列表 13 项。

### 清理遗留按钮
- 音乐窗口头部 `<div class="view-actions">`（清空 / 返回）整块删除——窗口自己有最小化/最大化/关闭。
- 新增 `_cleanupRedundantViewButtons()`：初始化时把所有**挂件视图**页头里 `switchView('chat')` 的「返回」按钮移除。当前残留数：0。
- 说明：非挂件视图（整页切换的）不受影响，仍保留返回入口。

注意：登录态保存在 `data/kugou_session.json`（含 token），不要提交到仓库或发给别人。


## 第 15 轮：音乐播放器扩版面（2026-11-14）

问题：音乐窗口沿用的是「通用挂件」尺寸（约 960×625），放不下封面 + 歌词 + 队列。

改动：
1. 挂件目录里把 `music` 标记为 `big: true`，默认尺寸走 0.86×桌面。
2. `_applyWidgetGeom()` 对音乐窗口强制下限：`max(旧宽, min(1320, 桌面宽-32))` × `max(旧高, min(840, 桌面高-32))`——**已保存过小尺寸的用户也会被自动放大**。
3. 新增 `@container (min-width: 1200px)` 宽版面：封面 322、歌词区占满剩余宽度、队列 404、播放键 52px、底栏缩略图 52px。
4. 修掉内容把底栏顶出窗口的问题：`.widget-body / .view / .music-player / .player-body / .player-left` 统一 `min-height:0; overflow:hidden`。

验证：Playwright 在 1584×961 桌面下打开音乐窗口 → 1320×824；`player-controls` 底边 958 ≤ 窗口底边 959（完全在窗口内）；酷狗歌单 13 个、打开歌单 60 首正常渲染；无 pageerror。

> 参考项目（folia / KugouMusic.NET / Rhine-Music-Database）本轮只用于版面与信息层级参考。QQ 音乐（qq-music-api）、网易云增强 API（NeteaseCloudMusicApiEnhanced）、LyricX/Lyrimuse 的接入**本轮未做**，属于后续独立任务。


## 第 16 轮：音乐播放器全新 UI（独立应用页，2026-11-14）

不再修补旧挂件，直接重写了一个独立的音乐播放器页面 `frontend/music.html`（951 行，自包含 HTML/CSS/JS），
由后端 `/music` 路由提供；挂件启动器里的「音乐播放」磁贴改为 `href: '/music'`，点开即进入新播放器。

布局（参考 folia 的歌词优先 / Kugou 的三栏 + 底栏 / Rhine 的资料库列表）：
- 左侧 76px 图标栏：本地 / 酷狗 / 网易云 / 返回。
- 中间内容区：列表（编号、封面、标题、歌手、时长、行内播放）+ 顶部搜索框 + 账号状态。
- 右侧 Now Playing：大圆封面（加载失败回退 ♪）、标题/歌手、逐行/逐字歌词（当前行放大、已唱词高亮）。
- 最右队列面板：200 首完整队列，当前项高亮，点击跳播。
- 底部播放栏：可视化频谱、缩略图/曲名、播放模式/上一首/播放/下一首/收藏、时间、音量。

功能：本地曲库播放、搜索过滤、酷狗扫码登录 + 歌单 + 歌单歌曲 + 搜索、网易云扫码登录 + 歌单 + 搜索 + 播放地址/歌词、
播放模式（顺序/循环/单曲/随机）、进度拖动、音量、逐字歌词、Web Audio 频谱。

验证：Playwright 1584×961 → 本地 180 行、搜索 6 条、酷狗歌单 13 个、打开「我喜欢」200 首、
播放后 `audio.src=/api/music/stream/...`、`paused=false`、队列 200、封面正常显示；无 pageerror。

**尚未迁移到新 UI 的旧功能**：音乐下载页、收藏管理/歌单编辑、迷你播放器与桌面歌词浮窗、网易云每日推荐、
播放列表分页/排序。这些旧实现在原 `#music-view` 挂件里仍然保留（可从代码/直达视图访问）。


## 第 17 轮：播放页主题统一 + 功能补充（2026-11-14）

### 主题统一
- 整套配色改为读取主软件的设计令牌：浅色 `--canvas #F6F9FE / --surface 白玻璃 / --accent #2F7FD6`，
  深色 `--canvas #070B1A / --accent #F0D27A`（金色，与主软件深色主题一致）。
- 布局改成和主软件一致的玻璃卡片：左侧竖条、主列表、Now Playing、队列、底栏各自是 16px 圆角 + 1px 描边 + 柔阴影 + 背景模糊。
- 深浅色跟随 `localStorage.darkMode`，左侧新增「主题」按钮可即时切换。

### 新增功能
1. **收藏**：底栏 ♡ 按钮调用 `POST /api/music/favorite`（仅本地歌曲），状态与列表同步。
2. **沉浸歌词**：点封面或按 `F` 全屏歌词（大字号高亮、背景模糊），`Esc` 退出。
3. **快捷键**：空格播放/暂停、`←/→` 快退/快进 5s、`N/B` 下一首/上一首、`F` 沉浸、`Esc` 退出。
4. 主题切换按钮（rail 底部）。

验证：Playwright 1584×961 加载 `/music` → 180 行列表、播放后 `audio.src` 正确且暂停键变为播放键、队列 180、浅色/深色/沉浸三张截图正常、无 pageerror。


## 第 18 轮：播放器补收藏与下载（2026-11-14）

1. 左栏新增「收藏」：`GET /api/music/favorites` → 可播放列表（39 首），支持搜索过滤。
2. 左栏新增「下载」：平台切换 chips（netease / qq / kugou / kuwo / bilibili）→ `GET /api/music/download/search` → 结果列表带「下载」按钮；点击走 WebSocket `/api/music/download/ws`，进度条实时更新，完成后提示文件名。
3. 修掉一个并发 bug：各来源的异步加载没有守卫，慢请求会覆盖当前视图（例如本地列表把下载页顶掉）。现在每个加载器都先判断 `state.source` 仍是自己才渲染。
4. 下载完成提示改用后端返回的 `title/filename`（原来读 `message`，为空）。

验证：收藏 39 行；下载搜索 20 条；点第一首下载 → 进度 → 完成，`data/Songs` 新增 `The truth that you leave - Pianoboy高至豪.mp3`（8.9MB，测试产物）；无 pageerror。


## 第 19 轮：歌单编辑（新建/重命名/删除/拖拽排序）（2026-11-14）

左栏新增「歌单」：
- 列表：名称、曲目数、当前标记；每行「打开 / 重命名 / 删除」。
- 新建：右上角「＋ 新建歌单」→ 自定义弹窗输入名称 → `POST /api/music/playlist/create`。
- 重命名：`POST /api/music/playlist/rename`（filepath + name）。
- 删除：二次确认后 `POST /api/music/playlist/delete`（只删歌单文件，不动歌曲）。
- 打开：`POST /api/music/playlist/switch` 后读取该歌单歌曲。

拖拽排序：本地/歌单里的行可拖动（`draggable`），drop 后重排内存中的 `rawLocal` 并 `POST /api/music/playlist/save` 持久化；拖动中有虚线落点提示。

顺带修：`loadLocal` 拆成 `loadLocal()` + `loadLocalSongs()`，切换歌单后标题与列表能正确刷新。

验证：歌单页 1 个默认歌单 → 新建「测试歌单ZZ」→ 重命名「测试歌单ZZ2」→ 删除，均成功且列表即时刷新；
本地 181 首拖拽第 1 行到第 3 位，顺序变化并保存成功，随后用原始顺序回写复原（已确认首曲恢复为 #тамада）。无 pageerror。


## 第 20 轮：桌面歌词 / 迷你模式 / 每日推荐（2026-11-14）

1. **桌面歌词浮窗**：新播放器每次 `timeupdate/play/pause` 上报 `/api/music/now-playing`（600ms 节流，含当前行逐字 words 与下一行），
   底栏「词」按钮走 `pywebview.api.open_lyrics_overlay()` 打开 `static/lyrics_overlay.html`（浏览器环境退化为弹窗）。
2. **迷你模式**：底栏「▭」切换 `.app.compact`（隐藏左栏/Now/队列，只留列表 + 底栏），状态存 `localStorage.musicCompact`。
3. **网易云每日推荐**：登录后账号区出现「每日推荐」，读 `/api/netease/recommend` 渲染歌曲列表；未登录时提示需要登录。

验证：播放后 `GET /api/music/now-playing` 返回 title/playing/time 正确（桌面歌词浮窗因此可用）；迷你模式截图正常；
未登录时每日推荐显示「需要登录网易云后才能看每日推荐」；无 pageerror。

说明：迷你模式是页面内的紧凑布局，不是独立的置顶小窗（独立窗口需要桌面壳新开 webview 并做播放控制 IPC，尚未做）。


## 第 21 轮：添加到歌单 / QQ 音乐 / 独立迷你窗（2026-11-14）

### 1. 添加到歌单（右键）
列表行右键 → 菜单「添加到歌单…」→ 选歌单 → 切到目标歌单 `POST /api/music/playlist/add` → 切回原歌单。
实测：新建临时歌单 → 右键加到它 → 曲目数 1 → 删除临时歌单，流程通过。

### 2. QQ 音乐接入
- 新路由 `music/routers/qq.py`：`/api/qq/search`（复用 `MusicDownloader._search_qq`，把 "3:58" 转秒）、`/api/qq/url`、`/api/qq/stream`（后端带 Referer 代理 vkey 直链）。
- 播放器左栏新增「QQ」：搜索 → 列表 → 点播走 `/api/qq/stream?mid=`。
- 实测搜索「周杰伦」20 条（发如雪/晴天/兰亭序…）；但 vkey 直链对多数热门歌返回 **100 字节的占位 m4a**（QQ 的 VIP/试听限制），所以能搜、能解析，播放要看歌曲是否免费。

### 3. 独立置顶迷你窗
- 新增页面 `/music/mini`（`frontend/music_mini.html`，430×120 布局：封面/曲名/歌手/进度/上一首-播放-下一首），轮询 `/api/music/now-playing`。
- 控制走命令总线：`POST /api/music/command {action}` 递增 seq，主播放器每 800ms `GET /api/music/command` 执行（play/pause/toggle/next/prev/seek/volume）。
- `desktop.py` 新增 `open_mini_player(url)` / `close_mini_player()`：无边框 + 置顶 + 透明，430×120。
- 主播放器底栏新增「⧉」按钮：桌面壳里开真窗口，浏览器里退化为弹窗。
- 实测：主页面播放中推送 `next` 命令，播放索引 0 → 1，命令总线生效；迷你窗页面渲染正常。

附带：`/api/music/now-playing` 增加 `cover` 字段，迷你窗与桌面歌词都能显示封面。


## 第 22 轮：补回顶部标题栏 + 修 Now Playing 溢出（2026-11-14）

问题：/music 是整页导航，桌面壳自带的标题栏被替换掉；同时右侧「正在播放」面板在窗口较矮时会被内容撑出可视区，封面顶部被裁切、歌词区挤压。

修复：
1. 页面自带标题栏 `.appbar`（46px）：返回箭头、`♪ Furina 音乐`、当前来源提示；右侧最小化/最大化/关闭按钮，在桌面壳里调 `pywebview.api.minimize_window / maximize_window / close_window`，浏览器里自动隐藏窗口按钮。
2. 网格改成 `46px / 1fr / 96px` 三行；迷你模式隐藏 `.appbar`。
3. Now Playing 自适应：封面 `clamp(132px, 26vh, 224px)`、面板 padding 收紧到 16px、歌词内边距 18%，保证封面和歌词都落在卡片内。
4. 当前来源标题同步到顶栏右侧提示。

验证（1584×961）：`.appbar` 高 46；播放态下 `.now` 区域 top=58/bottom=853，封面 top=75 高 224，完全在卡片内；无 pageerror。


## 第 23 轮：离开页面不中断播放 + 迷你播放器增强（2026-11-14）

### 根因
`/music` 是整页导航，音频就在这个页面的 `<audio>` 里；一旦跳去工作台或 DSH 内核，页面被销毁 → 播放自然停止。

### 方案：音乐独立窗口
1. `desktop.py` 新增 `open_music_window(url)`：正常窗口 1320×840（最小 900×600），单例；再次点「音乐播放」只是 show+restore。
2. 启动器「音乐播放」磁贴改为调 `openMusicApp()`：桌面壳里开独立窗口（URL 带 `?window=1`），浏览器环境退化为整页跳转。
3. `/music?window=1` 下顶栏返回按钮改为「关闭音乐窗口」（`pywebview.api.close_window`）。
   主窗口此后可以自由跳到 DSH 内核等页面，音乐窗口继续播放。

### 迷你播放器增强
1. 命令总线改成**队列**：`POST /api/music/command` 递增 seq 入队，`GET /api/music/command?since=N` 返回未执行命令，主页面按顺序执行，连续点击不再丢命令。
2. now-playing 增加 `duration / mode / favorite / volume`，迷你窗据此显示「当前/总时长」、模式图标、收藏态、音量条。
3. 迷你窗新增：进度条点击跳转（seek）、音量条点击调音量、播放模式循环、收藏切换，封面加载失败回退 ♪。

验证：`GET now-playing` 含 duration/mode/volume；连续下发 seek(30)+mode+volume(0.25) 后，主页面实际变为 time=32.4、mode=loop、volume=0.25；迷你窗显示「0:05 / 4:19」与进度填充；无 pageerror。


## 第 24 轮：歌词状态修复 + 歌词搜索下载（2026-11-14）

### 歌词「卡住不动」的真因
本地很多 `.lrc` 是**纯文本、没有时间轴**（例如 Brave Shine 的歌词文件每行都没有 `[mm:ss]`），
解析后 `time` 全是 0 → `updateLyrics` 一直命中最后一行，看起来像"停在最后一句"，并且把这一行推给桌面歌词/迷你窗。

修复：
1. 加载后判断 `state.unsynced = 没有任何行 time > 0`；未同步歌词只做静态展示，不再高亮/滚动，也不再推送 `line`。
2. 歌词加载完成、切歌、拖进度后立即 `postNowPlaying()`（原来只靠 timeupdate 的 600ms 节流，切歌时浮窗会滞后）。
3. 验证：换成带时间轴的歌词后，49.5s→55.6s 四秒内歌词行正常切换，推送与页面同步；纯文本歌词不再抢高亮。

### 新增：歌词搜索 / 下载
Now Playing 面板右上角「歌词」按钮 → 弹窗：`POST /api/music/lyrics/search` 多源搜索（网易云/QQ 等），
每条结果标注「带时间轴 / 纯文本」并预览两行 → 点选后 `POST /api/music/lyrics/save` 写入 `data/Lyrics/<歌曲名>.lrc` 并立即重新加载。
实测：Brave Shine 从纯文本歌词切换为 51 行带时间轴歌词，保存即生效。

### 关于「播放器里的下载」和「音乐下载」
两者是**同一套后端**：都走 `GET /api/music/download/search` 和 WebSocket `/api/music/download/ws`，
下载落盘位置也一样（`data/Songs`）。区别只是 UI：旧的是工作台里的「音乐下载」挂件，新的是播放器左栏「下载」页。


## 第 25 轮：移除播放器内的下载器 + 修响应式布局（2026-11-14）

- 按需求删除新播放器里的「下载」：左栏按钮、`loadDownload / dlSource / dlSearch / dlStart / sendDl`、`.dl-*` 样式、`switchSource`/`doSearch` 的下载分支全部移除。
  工作台的「音乐下载」挂件保持不变（同一套 `/api/music/download/*` 后端）。
- 清理时发现两个自己造成的问题并修掉：
  1. 之前一次整块删除把「歌词搜索…歌单管理」整段复制了一遍（函数重复定义），已删掉重复块。
  2. `@media (max-width:1400px)` 里的 `grid-template-areas` 没有 `appbar`，窗口宽度 ≤1400 时标题栏会被自动排到右下角、整页布局塌掉——已补上 appbar 行，1080px 断点同样处理。

验证：1320×840 与 1584×961 两种尺寸下，标题栏都在顶部（top=0,h=46），左栏只剩 本地/酷狗/网易云/QQ/歌单/收藏，列表 181 首可播放，歌词弹窗正常，无 pageerror。


## 第 26 轮：改回内嵌式 + 修歌词滚动导致封面上移（2026-11-14）

### 封面上移被裁的真因
`#lyrics` 里用了 `scrollIntoView({block:'center'})`。`scrollIntoView` 会滚动**所有**可滚动祖先，
而「正在播放」面板是 `overflow:hidden`（可被程序滚动）——于是每次切歌词行，面板本身被向上滚，
封面跟着上移并被裁掉。

修复：改成只算歌词容器自己的 `scrollTop`（`target = line.offsetTop - box.clientHeight/2 + line.offsetHeight/2`），
并把 `.now.scrollTop` 归零做兜底。验证：带歌词播放时封面 top 稳定在 29（面板 top 12），`scrollTop` 始终为 0。

### 播放器改回内嵌
- `index.html` 新增 `#music-embed-view`，内部是 `iframe src="/music?embed=1"`；启动器「音乐播放」磁贴改为 `music-embed`（`big:true`，打开约 1204×736 的挂件）。
- `/music?embed=1` 时给 `body` 加 `embed` 类：隐藏页面自带标题栏，网格改为两行（内容 + 播放栏），三种断点都覆盖。
- 想要独立窗口/后台播放时，底栏「⧉」仍可弹出迷你窗（命令总线控制）。

验证：主窗口里点「音乐播放」→ 挂件内 iframe 加载，列表 181 首，无重复标题栏，无 pageerror。


## 第 27 轮：歌词改为时间轴优先（2026-11-14）

用户反馈「歌词完全对不上」。排查出两个叠加原因：
1. 默认走的是**逐字填充**（按当前行到下一行的区间均分进度）——遇到间奏长的行，字符几秒钟才点亮一格，看起来就是"完全跟不上"。
2. 自动滚动用 `on.offsetTop` 计算，而 `.ly` 的 offsetParent 不是 `#lyrics`，目标位置算错，高亮行常常不在视野里，主观上更像"对不上"。

修复：
1. 新增歌词模式，**默认 `timestamp`**：只按 LRC 时间戳高亮当前行（与旧播放器同一套逻辑），不做逐字填充。
2. `karaoke`（逐字）与 `static`（静态）保留，但默认不启用；未同步歌词自动走 `static`。
3. 滚动改为 rect 计算（`容器 scrollTop + 行相对容器位移 - 容器高度/2 + 行高/2`），高亮行稳定居中。
4. 歌词弹窗里加了「时间轴（默认）/ 逐字 / 静态」切换，选择存 `localStorage.lyricMode`。

验证：默认 `timestamp`，逐字高亮 span 数 0；播放 52.7s→59.4s 歌词行每 2 秒左右切换，且每行都居中（centered=True），滚动 215→300 正常推进；切到逐字模式后出现 13 个点亮字符。


## 第 28 轮：迷你播放器加「刷新」键（2026-11-14）

- 迷你窗控制条最左新增「⟲」刷新：清空本地缓存的 title/cover/mode/favorite，立刻向 `/api/music/now-playing` 重新拉一次（250ms 后再补一次），强制与主程序对齐；按钮会短暂高亮作为反馈。
- 顺手压缩了控制条尺寸（按钮 25px、音量条 52px、时间 `nowrap`），430×120 下「0:03 / 3:14 + 7 个控件」不再折行。

验证：主程序切歌后点刷新，迷你窗标题/时长立即更新为新歌曲（Seasons，0:02/3:14），单行显示。


## 第 29 轮：工具调用写入历史并回放（2026-11-14）

背景：工具调用其实一直存在会话文件里（assistant 消息的 `tool_calls` + `role=tool` 的结果消息），
但 `display_messages()` 只挑 user/assistant 的纯文本返回，所以刷新/切会话后工具卡片就没了。

改动：
1. 后端 `ChatManager.display_messages()`：先把 `role=tool` 的结果按 `tool_call_id` 建索引，再遍历消息；
   遇到带 `tool_calls` 的 assistant 就为每次调用输出一条
   `{role:"tool_call", call_id, name, args, output, time}`（参数 JSON 解析失败时降级成 `{"_raw": ...}`），
   位置保持在对应 assistant 文本之前，顺序与在线渲染一致。
2. 前端 `addMessage()` 增加 `role === 'tool_call'` 分支：用 `_createToolCard()` + `_updateToolCardResult()` 重建
   DSH 风格工具行（可展开看 IN/OUT），外层 `.message-tool-call` 控制左边距对齐气泡。

验证：打开旧会话（`s_20261113212335_cb9f56`）→ 接口返回 9 条 tool_call（open_url / write_file / edit_file …，带 args 与 output），
页面渲染出 9 张工具卡片，展开显示 `IN / OUT`，无 pageerror；会话条数由 27 条「原始消息」归并为 18 条「展示项」。


## 第 30 轮：BetterGI 更新流程实测（2026-11-14）

需求：BetterGI 已推送新版本，验证软件的「BetterGI 更新流程」能否自动完成更新。

### 实测结果：流程能跑起来，但更新没有完成
- 本机 `G:\BetterGI\BetterGI.exe` 版本 **0.66.0.0**（2026-10-01），BetterGI 弹窗提示可更新到 **0.67.x**。
- 第一轮（旧步骤）：启动 BetterGI ✅ → 等待界面 ✅ → 点「设置」✅（OCR 命中并点击）→ 滚动 ✅ → 点「检查更新」❌（超时）。
- 第二轮（改后步骤，8 步）：启动 ✅ → 等待主界面 ❌（更新弹窗盖住主界面，OCR 找不到 启动/设置 等文案）→ 流程提前中止。
- 全程 `BetterGI.exe` 版本未变，BetterGI 自身日志停在 16:19:30，没有任何更新动作记录。
- 追加验证：用 pyautogui 直接点击弹窗上的「立即更新」（两次不同坐标）也**完全没有反应** —— 弹窗不变、日志无新增。

### 结论与根因
不是 OCR 或步骤机的问题（第一轮它正确识别并点中了「设置」），而是**合成点击没有送达 BetterGI**：
BetterGI 以管理员身份运行（其 exe 路径从普通进程读取为空），本程序不是管理员，
Windows 的 UIPI（User Interface Privilege Isolation）会静默丢弃低完整性进程发往高完整性窗口的输入。

### 本轮顺带修掉的真问题
1. **窗口查找用错了对象**：`capture_window()` 只按原神窗口关键字找窗口，BGI 流程实际截的是「原神/前台窗口」。
   新增 `APP_WINDOW_KEYWORDS` / `find_app_window(app)` / `focus_app_window(app)`，BGI 流程现在锁定 BetterGI 窗口。
2. **步骤文案过期**：0.67.x 的更新页是「立即更新」按钮 + 三渠道，不再走 设置→检查更新。
   `BGI_FLOW` 重写为：启动 → 等主界面 → 点「立即更新」→ 等下载 → 确认/重启 → 等版本号变化（设置→检查更新保留为 optional 兜底）。
3. 点击前先 `focus_app_window` 把目标窗口调到前台。
4. 新增 `_is_admin()/_pid_is_elevated()` 与 `elevation_blocked` 提示：对方高权限时直接返回
   「BetterGI 正在以管理员身份运行……请以管理员身份启动本程序」，避免静默失败十几分钟（探测在部分环境不可靠，仅作兜底）。

### 交付状态
- 代码：流程与窗口定位已修好并编译通过；配置里的托管开关测试后已还原为 `false`；BGI webhook 已还原。
- 更新本身：**未完成**（0.66.0.0 仍在）。要让它跑通，需要以管理员身份启动本程序（或让更新流程由一个提权进程执行）。


## 第 31 轮：以管理员身份重启（BetterGI 更新可用性）（2026-11-14）

背景：BGI 更新流程的点击被 UIPI 拦截（BetterGI 管理员运行、本程序普通权限）。本轮把「提权重启」做成正式功能。

### 1. 后端
- 新增 `backend/zhiyan_web/system/elevation.py`：`is_admin()`、`build_restart_command()`、`restart_as_admin(dry_run, delay_exit)`；
  用 `ShellExecuteW(None, "runas", python, "desktop.py", cwd, 1)` 拉起重启，成功后 2 秒 `os._exit(0)` 退出旧进程。
- 新增接口：
  - `GET /api/system/elevation` → `{is_admin, restart:{python, script, params, cwd, command}}`
  - `POST /api/system/restart-as-admin` → **默认只预览**，必须 `{"confirm": true}` 才真的弹 UAC（防止误调用把程序弹掉）。
- `desktop.py` NativeBridge 增加 `restart_as_admin(dry_run)`，供桌面壳直接调用。

### 2. 前端
- BetterGI 页顶部新增提示条：非管理员时显示「当前不是管理员权限：BetterGI 若以管理员运行，更新流程的点击会被系统拦截」+「以管理员身份重启」按钮（二次确认后才调用 confirm 接口）。
- 顺手修了「最近错误: [object Object]」的渲染。

### 3. 附带
- 新增 `start_admin.bat`：双击即以管理员身份启动（`Start-Process -Verb RunAs`）。

验证：`GET /api/system/elevation` 返回 `is_admin:false` 与正确的重启命令；不传 confirm 时返回 `dry_run:true` 的预览且进程存活；BetterGI 页提示条正常显示、点按钮会先弹确认；无 pageerror。

> 事故与更正：验证过程中有一次调用漏了 confirm 保护，真的触发了提权重启（旧进程退出、弹出 UAC）。
> 已加上「默认预览、必须 confirm」的硬保护，避免再次发生。


## 第 32 轮：修 BGI 更新流程「安装包阶段卡住退出」（2026-11-14）

现象：BGI 更新走到「下载安装包 → 安装程序」时流程卡住并中止，推测是等待间隔太小、没捕捉到安装程序窗口。

### 实际原因（三个叠加）
1. **窗口找错了**：`find_app_window("bgi")` 依赖标题关键字 `bettergi`，但 BGI 窗口标题是「更好的原神」，
   而且窗口最小化后 `IsWindowVisible=false` 直接找不到 —— 流程等于在对着空气 OCR。
2. **安装程序是独立窗口**：BGI 下载完会拉起安装器（标题含 安装/Setup/Install），
   而原来所有观察都盯着 BGI 主窗口，安装阶段自然一直"未出现"。
3. **节奏太快**：下载/安装动辄几分钟，interval 2~3 秒 + 单步失败即 abort，很容易在中途放弃。

### 修复
1. `vision.py`
   - `APP_WINDOW_KEYWORDS` 增加 `installer`（安装/setup/install/更好的原神），`bgi` 增加「更好的原神」。
   - 标题找不到时**按进程名兜底**（`PROCESS_WINDOW_NAMES`：bettergi.exe / yuanshen.exe），枚举该进程顶层窗口（含最小化），
     `focus_app_window` 会 `SW_RESTORE` 还原。
   - 截图前先还原/激活目标窗口；非 game 目标找不到窗口时**不再退化成截前台窗口**（避免识别到别的程序）。
2. `update_flow.py`
   - 步骤支持 `app` 字段，每步可指定观察窗口（bgi / installer）。
   - BGI 流程重写为 11 步：启动 → 等主界面 → 点「立即更新」→ 等下载进度（BGI 窗口）→ 等安装程序出现（installer）
     → 点「安装/下一步」→ 等安装完成 → 点「完成/关闭」→ 等 BGI 重启 → 等版本号变化 → 已是最新则结束。
   - 间隔放宽到 4~5 秒，超时 180~900 秒，安装阶段全部 `on_fail: continue`（不再因为一步没识别到就整体中断）。

### 验证
- `find_app_window("bgi")` 现在能找回最小化的「更好的原神」窗口并还原；`capture_window(app="bgi")` 正常出图（900×600）。
- 用自定义短流程验证 `app` 覆盖生效：`app=installer` 步骤快速返回「未找到 installer 窗口」（不再误截前台），
  `app=bgi` 步骤 OCR 命中「启动」（ok=True）。
- 本机 BGI 已由用户手动更新到 **0.67.0.0**（exe 11-14 19:48），安装阶段的真实链路要等下一次版本更新才能复测。


## 第 33 轮：图像识别全内存化（不落盘）（2026-11-14）

原来每次观察都 `ImageGrab.grab().save(png)` 再让 OCR/模板去读文件，一次更新流程能写出几十个 PNG（`data/bettergi/vision` 已累积 171 个 / 17MB）。

改造：
1. `vision.capture_window(save_dir="", region, app, save=False)`：默认**不写盘**，直接返回 `image`（PIL.Image）+ `path=""`；
   需要文件的地方显式传 `save=True`。
2. `recognize()/recognize_scene()/match_template()/ai_decide()` 统一接受 `path / bytes / PIL.Image / numpy`：
   OCR 前转 numpy，模板匹配转灰度，AI 走 `_to_data_url()`（JPEG→base64，全在内存）。
3. `update_flow._observe()`：用 `shot["image"]` 识别，`finally` 里 `image.close()` 立即释放；返回的 shot 不再带图像对象。
4. `/api/bettergi/vision/test`：不再落盘，返回 `shot.data_url`（data:image/jpeg;base64）供前端预览。
5. `register_tools` 的视觉测试、`recover_once` 同样改内存。
6. 唯一保留落盘的是**QQ/官方机器人通知截图**（上传接口必须真实文件），显式 `save=True` 并保持原路径逻辑。

验证：
- `capture_window(app="bgi")` → `path=''`、`image` 有值；OCR 成功；`data/bettergi/vision` 新增文件 0。
- 跑一次更新流程（自定义短流程）：步骤命中成功，截图目录文件数 171 → 171（零新增）。
- `POST /api/bettergi/vision/test` 返回 `shot.data_url`（约 87KB base64）与 40 个识别项，无文件产生。

遗留：`data/bettergi/vision` 里历史截图 171 个 / 17MB 仍在，可清理。


## 第 34 轮：PyInstaller 多文件（onedir）打包（2026-11-14）

命令：`python -m PyInstaller build_exe.spec --noconfirm --clean`
产物：`dist/ZhiYanAgent/`（**onedir 多文件**，exe 同级平铺依赖，`contents_directory='.'`），约 **816MB**，`ZhiYanAgent.exe` 约 30MB。

### 打包期两次失败与修复
1. `Isolated subprocess crashed while importing package 'onnxruntime.quantization'`
   —— spec 里 `collect_submodules("onnxruntime")` 把 quantization/tools/transformers 一起收了，PyInstaller 分析其二进制依赖时子进程崩溃。
2. 排除 quantization 后又变成 `onnxruntime.capi` 崩溃（Python 3.14 + 该版本 DLL，在隔离分析子进程里必然失败）。

最终方案：**OCR 依赖不做模块分析，整目录当资源携带**。
- 删除 `collect_submodules(rapidocr/onnxruntime)`，改为 `datas.append((data/py_deps/ocr, "data/py_deps/ocr"))`；
- `Analysis.excludes` 里加 `onnxruntime*`、`rapidocr`、`rapidocr_onnxruntime`、`sympy`；
- 运行期 `vision._ensure_sys_path()` 本来就会把 `data/py_deps/ocr` 加进 `sys.path` 和 DLL 搜索目录，所以功能不受影响。

### 冒烟验证（实跑打包产物）
- 启动 `dist/ZhiYanAgent/ZhiYanAgent.exe` → 进程存活，监听 `127.0.0.1:49221`。
- `GET /` → 200；`GET /music` → 200；`GET /api/system/elevation` → 正常 JSON。
- 用完已结束进程，并按惯例把 `G:\BetterGI\User\config.json` 的 webhook 从测试端口还原为 `http://127.0.0.1:58370/api/bettergi/event`。

### 注意
- 打包出的 exe 与源码目录共用 `data/` 逻辑是「exe 同级 data」；`dist/ZhiYanAgent/data` 是本次冒烟跑出来的初始数据。
- `build_out.log / build_out2.log / build_out3.log / run_built_test.ps1` 是本次打包的日志与测试脚本，可删。


## 第 35 轮：用截图「训练」更新流程的按钮模板（2026-11-15）

用户提供了 6 张真实截图（BGI 更新弹窗、米哈游启动器的 更新游戏/恢复下载/开始游戏/下载中/校验并修复）。
做法不是训练模型，而是**把这些截图里的按钮裁成模板，走已有的模板匹配（多尺度 + 灰度）**：

### 1. 模板生成工具
新增 `tools/gen_bgi_templates.py`：按文件名关键字找截图 → 用颜色连通块自动定位按钮 → 裁切（含 4px 边距）→ 写入 `data/bettergi/templates/`。
- 黄色按钮（启动器）：`game_btn_update / game_btn_start / game_btn_resume`
- 绿色按钮（BGI 更新弹窗）：`bgi_btn_update`
- 状态条（校验并修复）：`game_status_verify`
- 「下载中」状态条裁出来的是小图标、误匹配到右上角（0.884），**已放弃该模板**，该状态改用 OCR 文本判断。

生成结果与自检得分（对原截图）：
| 模板 | 得分 | 命中位置 |
|---|---|---|
| game_btn_update | 0.997 | (1382,757) |
| game_btn_start | 0.997 | (1382,757) |
| game_btn_resume | 0.998 | (1063,632) |
| bgi_btn_update | 1.000 | (1103,683) |
| game_status_verify | 0.996 | (627,631) |

### 2. 接入流程
- 原神流程（6 步）：启动器就绪 → **模板/OCR 点「恢复下载」（可选）** → **模板/OCR 点「更新游戏」**（若是开始游戏则跳过）→ 等下载/校验文本 → **模板/OCR 等「开始游戏」**。
- 启动器按钮的匹配区域限制在 `[0.45,0.55,1,1]`（右下角），避免桌面黄色图标误命中。
- BGI 流程：「点击立即更新」步骤挂 `bgi_btn_update` 模板，OCR 文案作为兜底。

### 3. 打包
`build_exe.spec` 增加：`data/bettergi/templates` 随包携带（下次重新打包生效）。

验证：`recognize_scene(截图, [template rule])` 正确返回 `game_btn_update.png @0.997`；`/api/bettergi/update/flows` 里两条流程的步骤都带上了 template/region 字段。


## 第 36 轮：自动化打包脚本（2026-11-15）

用户以后自行编译，因此把打包封装成脚本：

### 入口
- 双击 `build.bat`，或命令行 `python tools/build.py`。
- 参数：
  - `--zip` 打包后压缩成 `dist/<名字>.zip`
  - `--no-clean` 增量打包（不清理 build/dist）
  - `--check` 只检查环境（Python/PyInstaller/spec/图标/模板/OCR/是否在运行）
  - `--name FurinaAgent` 覆盖产物名（会生成临时 spec 改名后打包，不动原 spec）
  - `--no-pause` 结束不等待按键（命令行/CI 用）

### 脚本做了什么
1. 环境自检：Python ≥3.10、PyInstaller、`build_exe.spec`、`zhiyan.ico`、`data/bettergi/templates` 模板数、`data/py_deps/ocr`。
2. 检测目标 exe 是否正在运行（会被占用导致失败），兼容 `ZhiYanAgent.exe / furina.exe / FurinaAgent.exe` 等历史命名。
3. 清理 `build/` 与 `dist/`（可用 `--no-clean` 跳过）。
4. 调 PyInstaller（`--noconfirm --clean`），日志写到 `build_YYYYMMDD_HHMMSS.log`，屏幕只打印关键行。
5. 校验产物：exe 存在且 >1MB，`frontend/ backend/ data/` 三个目录齐全，打印总大小。
6. 可选压缩 zip，最后打印产物目录。

### 与 spec 的配合
- 产物名默认取 `build_exe.spec` 里的 `name`（当前是 `ZhiYanAgent`）；`--name` 会基于 spec 生成 `build_exe.tmp.spec` 改名打包，结束后删除临时文件。
- 之前手工把产物改名成 `FurinaAgent/furina.exe` 的情况，脚本的「运行中检测」和「产物校验」都能兼容。

### 验证
- `python tools/build.py --check` 与 `--name FurinaAgent` 均正常输出环境检查结果。
- `build.bat --check --no-pause --name FurinaAgent` 通过（bat 重写为 CRLF + ASCII，避免 cmd 解析中文/换行出错）。


## 第 37 轮：产物改名 Furina Agent + 更换图标（2026-11-15）

- `build_exe.spec`：`name="ZhiYanAgent"` → **`name="Furina Agent"`**（EXE 与 COLLECT 两处），产物变为
  `dist/Furina Agent/Furina Agent.exe`。
- 图标：`icon=zhiyan.ico` → **`furina.ico`**。用 `头像.jpg`（1075×1076）居中裁方、缩放到 256²，
  导出多尺寸 ICO（256/128/64/48/32/16），生成 `furina.ico`（约 134KB，芙宁娜 Q 版）。
  > 备注：用户提到的 `图像.jpg` 在目录里不存在，实际使用了同目录的 `头像.jpg`。
- `tools/build.py` 的环境检查改为**从 spec 里解析图标路径与产物名**，改名/换图标后无需再改脚本。
- 旧产物 `dist/ZhiYanAgent` 仍保留，重新打包后可手动删除。

验证：`python tools/build.py --check` 输出「图标: furina.ico [OK]」「产物名: Furina Agent」。


## 第 38 轮：打包脚本修 Hidden import 噪声（2026-11-15）

用户反馈打包时出现：
```
ERROR: Hidden import 'uvicorn.protocols.websockets.ws_impl' not found
ERROR: Hidden import 'pydirectinput' not found
ERROR: Hidden import 'pywinauto' not found
```
这些是 PyInstaller 对「hiddenimports 里列了但环境没装」的模块打的 ERROR 日志，**不中断构建**，但看起来像失败。

### 修复
1. `build_exe.spec`：新增 `OPTIONAL_HIDDEN` 清单 + `_importable()` 过滤，
   打包时按 `importlib.util.find_spec` 判断，未安装的可选依赖直接从 hiddenimports 里剔除，
   并打印 `[spec] 跳过未安装的可选依赖: ...`。
   本次环境会过滤掉：`pydirectinput`、`pywinauto`、`uvicorn.protocols.websockets.ws_impl`。
2. `tools/build.py`：
   - 屏幕输出不再刷这些 ERROR 行；构建结束后汇总成「提示：N 个可选依赖未安装，已自动跳过（不影响打包）」。
   - 增加成功判定：PyInstaller 退出码为 0 **且** 日志里出现 `Build complete` 才算成功，避免误判。
3. 顺带确认：`build_exe.spec` 里 `name="Furina Agent"`、`icon=furina.ico`、模板与 OCR 依赖随包携带。

验证：`python tools/build.py --check` 通过；过滤逻辑对当前环境正好命中上述 3 个模块。


## 第 39 轮：打包脚本改为实时完整输出（2026-11-15）

问题：`执行: python -m PyInstaller ...` 之后长时间没有任何反馈，看起来像卡死。

原因：子进程 stdout 被管道缓冲，PyInstaller 的 INFO 行要攒满缓冲区才吐出来；
而分析依赖阶段本来就会静默几十秒到几分钟。

修改（`tools/build.py`）：
1. 子进程改为 `python -u -m PyInstaller ...`，关闭 Python 缓冲，日志逐行实时到达。
2. 用独立线程实时读取输出并**逐行打印**（默认 `verbose`）；`--quiet` 才回到「只显示关键行」。
3. 新增心跳：连续 15 秒没有新输出就打印
   `...仍在打包（已 Ns，累计 M 行输出；PyInstaller 分析依赖阶段可能较慢）`，避免误以为卡死。
4. 开跑前打印日志路径与预计耗时（首次 8~12 分钟），并声明「下面会实时输出每一步」。
5. 日志仍完整写入 `build_YYYYMMDD_HHMMSS.log`，结束后打印耗时与日志路径。

用法不变：`build.bat` / `python tools/build.py`；想要精简输出加 `--quiet`。


## 第 40 轮：借鉴 deepseek-ai/chat-demo 增强富内容渲染（2026-11-15）

调研：官方把网页端 1:1 复刻开源了（[deepseek-ai/chat-demo](https://github.com/deepseek-ai/chat-demo)，MIT，React 18 + Zustand + Rspack）。
其功能清单里最值得抄的是「公式 / 图表 / 引用角标 / 语音输入 / 分支切换」，本轮先做**公式 + 图表**。

### 1. 依赖本地化（离线可用）
- `frontend/static/vendor/katex/`：katex.min.js（275KB）、katex.min.css、auto-render.min.js、20 个 woff2 字体（约 296KB）。
- `frontend/static/vendor/mermaid/mermaid.min.js`（2.5MB，11.4.1）。
- **坑**：KaTeX 是 UMD 包，若页面已存在 AMD `define`（Monaco loader），它会注册成 AMD 模块而不挂 `window.katex`；
  解决：把 KaTeX 的 `<script>` 放到 monaco loader **之前**加载。

### 2. 渲染实现
- 新增 `enhanceRichContent(root)`：Mermaid 代码块 → `.mermaid-block` 容器并 `mermaid.run()`；正文公式交给 `renderMathInElement`（跳过 pre/code）。
- 调用点：流式 `done`、历史消息 `addMessage`、`finalizeAgentMessage`（流式过程中不渲染，避免反复重排）。
- 主题切换 `toggleDarkMode` → `_refreshMermaidBlocks()` 按明暗主题重绘图表。
- `renderMarkdown` 支持显示公式 `$$...$$` / `\[...\]`：抽出为占位符 → 末尾用 `katex.renderToString(displayMode)` 还原。

### 3. 顺手修掉的旧 bug
代码块原来在「整体 HTML 转义」之后才渲染 → 代码里的 `<`、`&`、`>` 会被二次转义（显示成 `&gt;`），
且后续 `\n → <br>` 会破坏代码块内容（Mermaid 图形因此报 Syntax error）。
改为**在渲染最开始就把 ``` 代码块抽成占位符**，最后用原始文本 `renderCodeBlock()` 还原。

验证：同一条消息里同时含行内公式、显示公式、python 代码块（含 `<`、`&`、`$$`）、mermaid 流程图 →
KaTeX 2 个节点 / display 1 个 / mermaid SVG 1 个，代码文本完整未变形，无 pageerror。

## 仍然留着的打磨项（不阻塞目标）

1. 计划条从 Composer 上方移进消息流（现在是顶部 dock，功能完整）。
2. 消息操作条补「编辑重发」（后端只有按 timestamp 删除接口，没有截断/改写接口）。
3. 工具行分类渲染：`renderToolResultEnhanced/renderReadCard/renderDiffCard` 仍是死代码，可接到对应工具名上。

## 第 3 轮待办

1. 计划条从 Composer 上方移进消息流（DSH 的计划块位置），完成后收成一行摘要。
2. 审批卡贴输入区上方（滚动也可见），保留消息流内的留痕。
3. 消息操作条补「编辑重发」，对标 DSH 的编辑上一轮。
4. 上下文用量环：`/api/chat/context` 的 `estimated_tokens/context_window` 画环 + 百分比。
5. 图片：Composer 缩略图与体积预检（现在只有附件列表，无图床预览）。

## 下一轮待办（按优先级）

1. `@` 文件引用弹层：输入 `@` 拉 `/api/editor/files` 或 `/api/computer/file/list`，插入 chip，发送时转成文件附件（后端解析成读取结果注入）。
2. 工具行分组：同一回合连续同类工具折叠成「读取 3 个文件」一行，展开看明细。
3. 消息操作条补「编辑重发」，对标 DSH 的编辑上一轮。
4. 审批卡贴输入区上方（滚动也可见），而不是只在消息流里。
5. 上下文环：`/api/chat/context` 已有 `estimated_tokens/context_window`，在 Composer 右侧画环 + 百分比。
6. 计划条从 Composer 上方移进消息流（DSH 的计划块位置），完成后收成一行摘要。
