# AI 教辅子系统（edu）框架路线

> 来源：手写需求笔记（AI 教辅课程资料生成 + AI Agent 需求列表）
> 定位：在现有 Furina 架构（FastAPI 后端 + 静态前端 + star 插件系统 + QQ/OneBot 网关 + LLM Agent 工具循环 + TTS）上新增 `backend/zhiyan_web/edu/` 教辅子系统。

## 一、需求清单

1. 资料处理：QQ 群发来的 PPT/TXT/PDF 课件、作业 → AI 读取题目 → 完成解题过程 → 生成详细讲解与答案解析
2. 建立题库/资料库（循环素材），支撑后续组卷
3. 随机组卷：按题量、时间限制从题库抽题生成模拟卷
4. 问题推荐：按难度、个人掌握情况、学科/知识模块推题
5. 网络异常兜底：断网时本地 OCR 识别题目
6. 实时语音问答：语音提问 → ASR → LLM → TTS 回答
7. 自动批改：客观题自动判分，主观题识别作答并给批改建议
8. 错题本：错题自动整理、按学科/知识点分类
9. 错题标记联动 Agent：标记错题后自动整理同类题、针对性练习
10. 对话历史管理、系统设置管理
11. 技术栈：Python、Rust、C#、HTML/Node.js/JS/CSS；Agent、语音播报、代码编辑器已有，缺整个教辅系统与上下文管理

## 二、总体架构

```
接入层    网页前端 / QQ 群(OneBot) / 语音入口
应用层    edu 路由组 + star 插件命令（/组卷 /批改 /错题本 …）
能力层    文档解析 → 题库引擎 → 出题/组卷/推荐 → 批改 → 语音问答
数据层    data/edu/：question_bank.db、courses/、exams/、mistakes/、对话历史
```

## 三、模块划分（backend/zhiyan_web/edu/）

| 模块 | 职责 | 关键依赖 |
|---|---|---|
| `parsers/` | 课件解析：PDF 用 PyMuPDF、PPTX 用 python-pptx、TXT/图片走 OCR；统一抽取为「题目结构」（题干/选项/答案/解析/学科/知识点/难度） | PyMuPDF、python-pptx |
| `bank/` | 题库：SQLite 存储 + 标签体系（学科、知识点、难度）；入库时可选 LLM 自动打标签；向量索引复用现有 rag_index 做相似题检索 | SQLite、rag_index |
| `solver/` | 解题与讲解：复用 chat_manager 的 agent 工具循环，套「教辅讲解」系统提示词模板，输出解题步骤 + 答案 + 易错点 | chat_manager |
| `exam/` | 随机组卷：按学科/知识点/难度分布/题量/时长约束抽题，导出 PDF（reportlab）或 HTML 试卷 | reportlab |
| `recommend/` | 推题：依据错题本记录的正确率、知识点掌握度做规则 + LLM 混合推荐 | mistake_book |
| `ocr/` | 本地离线兜底：RapidOCR/PaddleOCR 离线模型，断网时识别题目图片 | RapidOCR |
| `grading/` | 自动批改：客观题文本比对；主观题拍照/文本 → OCR → LLM 评分给建议；结果回写错题本 | ocr、solver |
| `voice_qa/` | 语音问答链路：ASR（本地 FunASR/whisper）→ 现有 LLM → 现有 TTS 引擎，前端加录音按钮 | ASR、tts |
| `mistake_book/` | 错题本：错题按学科/知识点归档，一键"同类题练习"（调 recommend + exam） | bank、recommend |
| `memory/` | 对话历史与学生画像：复用现有 memory 模块与 chat_history，增加掌握度、错题统计维度 | memory |

## 四、关键集成点

1. QQ 链路：在 star 插件系统注册 `/课件`、`/组卷`、`/批改`、`/错题本` 等命令；文件消息（群文件/图片）走 parsers 入口
2. 网页端：新增「教辅」视图卡片（课件库/题库/组卷/错题本/语音问答），沿用现有 UI 风格
3. 离线策略：OCR、ASR 用本地模型；LLM 不可用时题目解析降级为"仅入库不讲解"
4. 配置：`settings.json` 增加 `edu` 节（题库路径、组卷默认参数、OCR/ASR 开关）

## 五、分期路线

1. **第一期**：解析入库 + 解题讲解 + 题库（核心价值链路）
2. **第二期**：组卷 + 错题本 + 推题
3. **第三期**：自动批改 + 语音问答 + 离线 OCR 兜底

## 六、第一期实现情况（已完成）

### 后端（backend/zhiyan_web/edu/）

| 文件 | 内容 |
|---|---|
| `models.py` | 表结构 `edu_courses` / `edu_questions`、题型与难度归一化、请求模型 |
| `bank.py` | 题库服务：课件与题目增删改查、学科/题型/难度统计、知识点标签、导入编排 |
| `solver.py` | 解题讲解：LLM 一次性调用，输出答案/步骤/讲解/知识点/易错点 |
| `parsers/document.py` | 文本抽取：pdf(PyMuPDF→pypdf)、pptx(docx 走 zip+XML)、xlsx(openpyxl)、txt/md/csv 多编码 |
| `parsers/extractor.py` | 题目结构化抽取（LLM），失败回退「按题号切分」启发式 |
| `routers/edu.py` | 14 个 `/api/edu/*` 端点（统计/课件/题目/解析预览/解题） |

数据落库：`data/zhiyan.db`（edu_courses、edu_questions）；上传文件落盘：`data/edu/files/`。

### 前端

`frontend/index.html` 新增 `#edu-view` 视图与应用面板入口「AI 教辅」；`app.js` 新增 23 个 edu 函数（统计/标签/筛选/分页/导入/加题/编辑/讲解/删除）；`style.css` 新增 `.edu-*` 样式段。

### QQ / 插件

`plugins/builtin_plugins/edu/`（main.py + metadata.yaml）：`/题库`、`/查题 <关键词>`、`/解题 <题目内容>`、`/导入课件 <本地路径>`。

### 配置

`settings.json` → `edu`：`enabled`、`default_subject`、`default_grade`、`max_questions_per_import`、`extract_chunk_chars`、`auto_save_analysis`、`ocr_enabled`。

### 已验证

导入（路径/上传）→ 入库 → 检索 → 讲解全链路通过；网页端页面渲染与交互经浏览器验收通过；QQ 侧四条命令经管道实测返回正常。

### 已知限制（后续期次处理）

1. 题目抽取与讲解依赖已配置的 LLM：当前 `settings.json` 的 `provider` 为 `mock`，此时抽取会回退启发式（无答案/解析）、讲解返回模拟文案；配置真实模型后即为完整效果。
2. 图片题目需本地 OCR（第三期），当前返回明确提示。
3. 组卷、错题本、推题（第二期）与自动批改、语音问答（第三期）尚未开始。

## 七、AI 授课子系统（teaching，已实现）

在教辅子系统之上新增「AI 授课」模块，统一挂载在 `backend/zhiyan_web/edu/`，路由前缀 `/api/edu/teach`（与教辅共用 `/api/edu`，前端统一用 `eduApi` 访问）。

### 后端模块

| 文件 | 职责 |
|---|---|
| `llm_utils.py` | 一次性 LLM 调用、JSON 稳健解析、多模态（图片）调用 |
| `slides/` | 模块1 教学幻灯片：`generator`(AI 大纲) + `pptx_writer`(python-pptx) + `html_render`(无依赖全屏 HTML) + `seewo`(希沃私有格式框架) + `service`(编排入库) |
| `flow.py` | 模块2 授课流程：读取课件 → AI 总结流程 → 无答案试题由 AI 补答案 → JSON+Markdown 落盘 |
| `rollcall.py` | 模块3 随机点名：名单/成员权重、时间戳伪随机(sha256 派生种子，可复现)、按批次去重窗口、权重轮盘无放回 |
| `catalog.py` | 模块4 快捷目录：逐页解析 → AI 生成纲要与每页元素 → OCR 文本匹配页码 → 发送翻页键跳转 |
| `ocr.py` | 模块4 双通道 OCR：本地 RapidOCR + 多模态大模型兜底；含截屏 OCR、图片题目结构化 |
| `web_questions.py` | 模块5 网络试题：网络题库 API / AI 自主生成 / AI 联网检索后生成（DuckDuckGo 免 Key 检索） |
| `asr.py` | 模块6 ASR 三引擎：本地 faster-whisper / 云端 DashScope(qwen-audio-asr) / 前端 Web Speech |
| `voice.py` | 模块6 实时问答：ASR → 连续记忆上下文 → LLM 作答 → TTS 播报 |
| `memory_bridge.py` | 模块7 连续记忆：复用 `conversation_store`(会话) + `vector_memory`(长期记忆) |
| `routers/teaching.py` | 53 个 `/api/edu/teach/*` 端点（幻灯片/流程/点名/目录/OCR/语音/记忆/对话/网络试题/状态/配置） |

新增数据表：`edu_slides` / `edu_flows` / `edu_catalogs` / `edu_rollcall_groups` / `edu_rollcall_members` / `edu_rollcall_logs` / `edu_web_questions`；产物目录 `data/edu/{slides,flows,catalogs,voice}`。

### 前端

`frontend/index.html` 新增 `#teach-view`（8 个标签：幻灯片创作/授课流程/随机点名/快捷目录/网络试题/实时问答/连续记忆/对话）+ 左侧导航与功能菜单入口；`app.js` 新增约 60 个 `teach*` 函数（含录音、抖动点名动画、豆包式对话）；`style.css` 新增 `.teach-*` 样式段。

### 悬浮球（模块4 桌面集成）

`frontend/float_ball.html`：独立置顶透明小窗页面，支持拖动、展开目录、截屏识别当前页、发送翻页键跳转。
`desktop.py`：`NativeBridge` 新增 `open_float_ball` / `close_float_ball` / `hide_float_ball` / `ball_move` / `ball_get_pos` / `ball_set_size` / `open_external`；启动时预创建一个隐藏的置顶小窗（`/float_ball`），按需显示，覆盖在 WPS / 希沃白板之上；`desktop-bridge.js` 已暴露对应 JS 方法。

### 配置

`settings.json → edu.teaching`：`slides` / `flow` / `rollcall` / `catalog` / `ocr` / `vision` / `voice` / `network` 八组子配置。

### 依赖（均为可选，缺失自动降级）

`python-pptx`（缺失则只出 HTML 幻灯片）、`rapidocr-onnxruntime`（缺失则 OCR 走多模态）、`faster-whisper`（缺失则 ASR 走云端/浏览器）。

### 已验证

后端 483 路由导入通过；实测通过：点名（权重+去重+时间戳种子）、授课流程（3 题抽取 + AI 补答案 + 落盘）、快捷目录（3 页构建 + 匹配度 0.9 命中第 2 页）、幻灯片生成（HTML 导出成功、PPTX 缺依赖时优雅跳过）、语音/对话问答（mock 模型 + 连续记忆落库）、文件下载与 `/float_ball` 页面；前端 8 个标签经浏览器实测渲染正常、无控制台报错。

### 已知限制

1. 幻灯片内容质量、目录元素、AI 试题均依赖真实 LLM；`provider=mock` 时幻灯片与目录回退骨架/启发式，网络试题返回空。
2. PPTX 生成需安装 `python-pptx`；本地 OCR/ASR 需安装对应依赖（本机 Python 3.14 暂无 wheel，已自动降级到多模态/云端）。
3. 希沃原生 `.seewo` 为私有格式无法生成，仅提供结构对齐的中间描述文件（框架）。
4. 悬浮球覆盖与翻页键需在桌面版（exe / `desktop.py`）运行；浏览器模式为预览。
5. 云端 ASR 走 DashScope OpenAI 兼容的 `input_audio`，需 `edu.teaching.vision.api_key` 或顶层 `api_key`。
