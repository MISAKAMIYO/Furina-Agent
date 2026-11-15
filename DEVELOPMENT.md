# Furina Agent Web 开发文档

## 项目概述

Furina Agent Web 是基于 FastAPI 开发的 Web 版 AI 助手，旨在将原生 Furina Agent 的核心功能迁移到浏览器端，提供更便捷的访问体验。

## 当前项目状态

### 已完成功能

| 功能模块 | 状态 | 说明 |
|:---|:---:|:---|
| 聊天系统 | ✅ | 流式聊天（WebSocket）、历史记录 |
| 音乐播放器 | ✅ | 播放、进度、播放列表 |
| TTS 引擎 | ✅ | GPT-SoVITS / Edge TTS / 情绪检测 |
| LLM 设置 | ✅ | 提供商、API Key、模型配置 |
| 记忆引擎 | ✅ | 四层记忆系统（Identity/Active/Surfaced/Archive） |
| 知识库 | ✅ | 增删改查、标签管理、内置知识 |
| 翻译系统 | ✅ | Google/Bing/有道/DeepL 多引擎 |
| 图片获取 | ✅ | Lolicon/Pexels/萌图 API |
| 好感度系统 | ✅ | 等级、互动、衰减、每日限制 |
| 承诺系统 | ✅ | 创建、完成、删除 |
| 搜索管理 | ✅ | 动漫搜索（trace.moe）、百科查询、视频搜索 |
| 会话管理 | ✅ | 多会话创建、切换、删除、导出 |
| 前端页面扩展 | ✅ | 记忆/知识库/翻译/图片/好感度/承诺视图 |

### 待开发功能

| 功能模块 | 状态 | 优先级 | 说明 |
|:---|:---:|:---:|:---|
| 经济系统 | ❌ | 🟢 低 | 签到、打工、商店、背包 |
| 漂流瓶 | ❌ | 🟢 低 | 扔瓶子、捡瓶子、回复 |
| 五子棋 | ❌ | 🟢 低 | 人机对战 |
| 盲盒系统 | ❌ | 🟢 低 | 抽取、收藏、历史记录 |
| 决策轮盘 | ❌ | 🟢 低 | 随机决策、预设选项 |
| 文本转图片 | ❌ | 🟢 低 | Liblib API 生成 |
| 代码执行器 | ❌ | 🟢 低 | 沙箱 Python 执行 |
| 表情包管理 | ❌ | 🟢 低 | 表情包搜索、发送 |
| 词云生成 | ❌ | 🟢 低 | 分词、词频、图像生成 |
| 账户系统 | ❌ | 🟢 低 | 注册、登录、权限管理 |
| 内容审查 | ❌ | 🟢 低 | 敏感词过滤 |

## 当前项目结构

```
zhiyan agent web/
├── backend/
│   └── main.py                    # FastAPI 后端（约 2220 行）
├── frontend/
│   ├── index.html                 # 前端主页面（聊天/TTS/音乐视图）
│   ├── music.html                 # 音乐播放器页面
│   └── static/
│       ├── assets/
│       │   └── background.jpg     # 背景图片
│       ├── css/
│       │   ├── style.css          # 主样式文件（约 1146 行）
│       │   └── music-player.css   # 音乐播放器样式
│       └── js/
│           └── app.js             # 前端逻辑（约 540 行）
├── DEVELOPMENT.md                 # 开发文档
├── requirements.txt               # 依赖列表
└── settings.json                  # 配置文件（自动生成）
```

## 已实现 API 端点

### 核心功能
| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/status` | GET | 获取服务器状态 |
| `/api/settings` | GET/POST | 获取/更新设置 |
| `/api/chat/send` | POST | 发送消息 |
| `/api/chat/ws` | WS | WebSocket 流式聊天 |
| `/api/chat/history` | GET/DELETE | 获取/清空聊天历史 |

### TTS
| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/tts/synthesize` | POST | 合成语音 |
| `/api/tts/audio/{filename}` | GET | 获取音频文件 |
| `/api/tts/emotions` | GET | 获取情绪列表 |

### 音乐
| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/music/playlist` | GET | 获取播放列表 |
| `/api/music/stream/{filename}` | GET | 流式播放音乐 |
| `/api/music/cover/{filename}` | GET | 获取音乐封面 |

### 记忆引擎
| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/memory/list` | GET | 获取记忆列表 |
| `/api/memory/add` | POST | 添加记忆 |
| `/api/memory/search` | POST | 搜索记忆 |
| `/api/memory/update` | PUT | 更新记忆 |
| `/api/memory/delete/{id}` | DELETE | 删除记忆 |
| `/api/memory/layers` | GET | 获取记忆层级 |
| `/api/memory/types` | GET | 获取记忆类型 |

### 知识库
| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/knowledge/list` | GET | 获取知识列表 |
| `/api/knowledge/add` | POST | 添加知识 |
| `/api/knowledge/search` | POST | 搜索知识 |
| `/api/knowledge/update` | PUT | 更新知识 |
| `/api/knowledge/delete/{id}` | DELETE | 删除知识 |
| `/api/knowledge/tags` | GET | 获取标签列表 |

### 翻译系统
| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/translate` | POST | 翻译文本 |
| `/api/translate/engines` | GET | 获取引擎列表 |
| `/api/translate/languages` | GET | 获取语言列表 |

### 图片获取
| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/images/fetch` | GET | 获取随机图片 |
| `/api/images/search` | POST | 搜索图片 |
| `/api/images/history` | GET | 获取图片历史 |
| `/api/images/{filename}` | GET | 获取图片文件 |

### 好感度系统
| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/likability/status` | GET | 获取好感度状态 |
| `/api/likability/interact` | POST | 进行互动 |
| `/api/likability/history` | GET | 获取互动历史 |
| `/api/likability/levels` | GET | 获取等级配置 |
| `/api/likability/interactions` | GET | 获取互动类型 |

### 承诺系统
| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/promises/list` | GET | 获取承诺列表 |
| `/api/promises/add` | POST | 创建承诺 |
| `/api/promises/complete/{id}` | POST | 完成承诺 |
| `/api/promises/delete/{id}` | DELETE | 删除承诺 |

## 已实现功能设计

### 搜索管理 (SearchManager)

**功能描述**：动漫搜索（trace.moe）、百科查询、视频搜索。

**API 设计**：

| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/search/anime` | POST | 通过图片搜索动漫 |
| `/api/search/baike` | GET | 百科查询 |
| `/api/search/video` | GET | 视频搜索 |

### 会话管理 (SessionManager)

**功能描述**：多会话管理，支持创建、切换、删除、导出。

**API 设计**：

| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/sessions/list` | GET | 获取会话列表 |
| `/api/sessions/create` | POST | 创建新会话 |
| `/api/sessions/get/{session_id}` | GET | 获取会话详情 |
| `/api/sessions/update/{session_id}` | PUT | 更新会话信息 |
| `/api/sessions/delete/{session_id}` | DELETE | 删除会话 |
| `/api/sessions/export/{session_id}` | GET | 导出会话记录 |

### 前端页面扩展

已完成以下视图：

1. **记忆管理视图** - 记忆列表、搜索、添加、筛选（按层级/类型）
2. **知识库视图** - 知识列表、搜索、标签筛选、添加
3. **翻译视图** - 输入文本、选择语言/引擎、翻译结果
4. **好感度视图** - 当前状态、互动按钮、等级进度、互动历史
5. **承诺视图** - 承诺列表、创建、完成、删除

## 待开发功能设计

### 经济系统 (EconomySystem)

**功能描述**：签到、打工、商店、背包。

**API 设计**：

| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/economy/status` | GET | 获取经济状态（金币、等级） |
| `/api/economy/checkin` | POST | 每日签到 |
| `/api/economy/work` | POST | 打工赚取金币 |
| `/api/economy/shop/list` | GET | 获取商店物品列表 |
| `/api/economy/shop/buy` | POST | 购买物品 |
| `/api/economy/inventory` | GET | 获取背包物品 |
| `/api/economy/use_item` | POST | 使用物品 |

### 漂流瓶 (BottleSystem)

**功能描述**：扔瓶子、捡瓶子、回复。

**API 设计**：

| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/bottle/throw` | POST | 扔瓶子 |
| `/api/bottle/pick` | GET | 捡瓶子 |
| `/api/bottle/replies/{bottle_id}` | GET | 获取瓶子回复 |
| `/api/bottle/reply` | POST | 回复瓶子 |
| `/api/bottle/my_bottles` | GET | 获取我的瓶子 |

### 五子棋 (GomokuGame)

**功能描述**：人机对战五子棋。

**API 设计**：

| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/gomoku/new` | POST | 创建新游戏 |
| `/api/gomoku/move` | POST | 玩家落子 |
| `/api/gomoku/status/{game_id}` | GET | 获取游戏状态 |
| `/api/gomoku/cancel/{game_id}` | POST | 取消游戏 |

### 盲盒系统 (BlindBoxSystem)

**功能描述**：抽取、收藏、历史记录。

**API 设计**：

| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/blindbox/open` | POST | 开启盲盒 |
| `/api/blindbox/collection` | GET | 获取收藏列表 |
| `/api/blindbox/history` | GET | 获取开启历史 |

### 决策轮盘 (DecisionWheel)

**功能描述**：随机决策、预设选项。

**API 设计**：

| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/wheel/spin` | POST | 转动轮盘 |
| `/api/wheel/preset` | GET | 获取预设选项 |
| `/api/wheel/custom` | POST | 创建自定义轮盘 |

### 文本转图片 (ImageGenerator)

**功能描述**：使用 Liblib API 生成图片。

**API 设计**：

| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/imagegen/generate` | POST | 生成图片 |
| `/api/imagegen/history` | GET | 获取生成历史 |

### 代码执行器 (CodeExecutor)

**功能描述**：沙箱 Python 代码执行。

**API 设计**：

| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/code/run` | POST | 执行代码 |
| `/api/code/history` | GET | 获取执行历史 |

### 表情包管理 (EmoticonManager)

**功能描述**：表情包搜索、发送。

**API 设计**：

| 端点 | 方法 | 描述 |
|:---|:---|:---|
| `/api/emoticon/search` | GET | 搜索表情包 |
| `/api/emoticon/list` | GET | 获取表情包列表 |
| `/api/emoticon/upload` | POST | 上传表情包 |

## 数据库设计

### 已创建的表

```sql
-- 记忆表
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'fact',
    layer TEXT NOT NULL DEFAULT 'archive',
    salience REAL NOT NULL DEFAULT 0.5,
    importance REAL NOT NULL DEFAULT 0.5,
    keywords TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    accessed_at INTEGER NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0
);

-- 知识表
CREATE TABLE knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'user',
    priority INTEGER NOT NULL DEFAULT 50,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    view_count INTEGER NOT NULL DEFAULT 0
);

-- 好感度表
CREATE TABLE likability (
    user_id TEXT UNIQUE NOT NULL,
    exp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    daily_interactions TEXT NOT NULL DEFAULT '{}',
    last_update_date TEXT,
    created_at INTEGER NOT NULL
);

-- 互动记录表
CREATE TABLE interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,
    exp_gained INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);

-- 承诺表
CREATE TABLE promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    deadline INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    completed_at INTEGER
);

-- 图片历史表
CREATE TABLE image_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    local_path TEXT NOT NULL,
    source TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL
);

-- 会话表
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0
);
```

### 待创建的表（经济系统、漂流瓶等）

```sql
-- 经济系统表
CREATE TABLE economy (
    user_id TEXT UNIQUE NOT NULL,
    coins INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    exp INTEGER NOT NULL DEFAULT 0,
    last_checkin_date TEXT,
    last_work_date TEXT,
    work_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 商店物品表
CREATE TABLE shop_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price INTEGER NOT NULL DEFAULT 0,
    rarity TEXT NOT NULL DEFAULT 'common',
    effect TEXT NOT NULL DEFAULT '{}',
    stock INTEGER NOT NULL DEFAULT -1,
    created_at INTEGER NOT NULL
);

-- 背包物品表
CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    acquired_at INTEGER NOT NULL
);

-- 漂流瓶表
CREATE TABLE bottles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    is_anonymous INTEGER NOT NULL DEFAULT 1,
    picked_up INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

-- 漂流瓶回复表
CREATE TABLE bottle_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bottle_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    is_anonymous INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

-- 五子棋游戏表
CREATE TABLE gomoku_games (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    board TEXT NOT NULL DEFAULT '[]',
    current_player TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'playing',
    winner TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 盲盒收藏表
CREATE TABLE blindbox_collection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    rarity TEXT NOT NULL DEFAULT 'common',
    acquired_at INTEGER NOT NULL
);

-- 盲盒历史表
CREATE TABLE blindbox_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    rarity TEXT NOT NULL DEFAULT 'common',
    created_at INTEGER NOT NULL
);

-- 图片生成历史表
CREATE TABLE imagegen_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    image_url TEXT NOT NULL,
    local_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL
);

-- 代码执行历史表
CREATE TABLE code_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    code TEXT NOT NULL,
    output TEXT,
    error TEXT,
    created_at INTEGER NOT NULL
);
```

## 依赖清单

```txt
fastapi>=0.110.0
uvicorn>=0.28.0
pydantic>=2.0.0
requests>=2.31.0
aiofiles>=23.0.0
edge-tts>=6.0.0
```

## 开发规范

### 代码风格

- Python 3.8+，遵循 PEP 8 规范
- 使用 logging 模块记录日志，禁止 print()
- 使用类型提示（Type Hints）
- 数据库操作使用参数化查询防止 SQL 注入

### API 响应格式

统一响应格式：
```python
{
    "success": bool,
    "message": str,
    "data": Any
}
```

### 错误处理

- 捕获异常并记录日志
- 返回统一格式的错误响应
- HTTP 状态码：400 参数错误，401 未授权，404 资源不存在，500 服务器错误

## 部署说明

### 开发环境

```bash
cd "zhiyan agent web"
pip install -r requirements.txt
python backend/main.py
```

### 生产环境

```bash
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## 实施计划

### 第一阶段：补全后端 API（🔴 高优先级）

1. **搜索管理** - 动漫搜索、百科查询
2. **会话管理** - 多会话创建、切换、删除、导出

### 第二阶段：扩展前端页面（🟡 中优先级）

1. **记忆管理视图**
2. **知识库视图**
3. **翻译视图**
4. **好感度视图**
5. **承诺视图**

### 第三阶段：娱乐功能（🟢 低优先级）

1. **经济系统** - 签到、打工、商店、背包
2. **漂流瓶** - 扔瓶子、捡瓶子、回复
3. **五子棋** - 人机对战