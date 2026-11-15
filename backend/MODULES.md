# ZhiYan Agent Web 模块化架构文档

## 1. 概述

本文档描述了 ZhiYan Agent Web 后端系统的模块化架构设计。系统采用分层架构，按照功能职责进行模块划分，实现低耦合高内聚的设计目标。

## 2. 目录结构

```
backend/
├── main.py                    # 启动入口
└── zhiyan_web/                # 主应用包
    ├── __init__.py            # 包初始化
    ├── app.py                 # FastAPI 应用实例
    ├── config/                # 配置模块
    │   ├── __init__.py
    │   └── settings.py        # 配置管理
    ├── core/                  # 核心模块
    │   ├── llm/               # LLM 模块
    │   │   ├── __init__.py
    │   │   ├── providers.py   # LLM 提供商
    │   │   └── chat_manager.py # 聊天管理器
    │   ├── tts/               # TTS 模块
    │   │   ├── __init__.py
    │   │   └── engine.py      # TTS 引擎
    │   └── websocket/         # WebSocket 模块
    │       ├── __init__.py
    │       └── connection.py  # 连接管理器
    ├── chat/                  # 聊天模块
    │   ├── __init__.py
    │   └── routers/           # 路由层
    │       ├── chat.py        # 聊天 API
    │       ├── websocket.py   # 聊天 WebSocket
    │       └── avatar.py      # 头像 API
    ├── music/                 # 音乐模块
    │   ├── __init__.py
    │   ├── services/          # 服务层
    │   │   ├── lyrics_parser.py    # 歌词解析
    │   │   ├── cover_finder.py     # 封面搜索
    │   │   └── playlist_manager.py # 播放列表管理
    │   └── routers/           # 路由层
    │       └── music.py       # 音乐播放 API
    ├── economy/               # 经济系统模块
    │   ├── __init__.py
    │   ├── services/          # 服务层
    │   │   └── economy_service.py # 经济系统服务
    │   └── routers/           # 路由层
    │       └── economy.py     # 经济系统 API
    ├── bottle/                # 漂流瓶模块
    │   ├── __init__.py
    │   ├── services/          # 服务层
    │   │   └── bottle_service.py  # 漂流瓶服务
    │   └── routers/           # 路由层
    │       └── bottle.py      # 漂流瓶 API
    ├── gomoku/                # 五子棋模块
    │   ├── __init__.py
    │   ├── services/          # 服务层
    │   │   └── gomoku_service.py  # 五子棋服务
    │   └── routers/           # 路由层
    │       └── gomoku.py      # 五子棋 API
    ├── blindbox/              # 盲盒系统模块
    │   ├── __init__.py
    │   ├── services/          # 服务层
    │   │   └── blindbox_service.py # 盲盒服务
    │   └── routers/           # 路由层
    │       └── blindbox.py    # 盲盒 API
    ├── wheel/                 # 决策轮盘模块
    │   ├── __init__.py
    │   ├── services/          # 服务层
    │   │   └── wheel_service.py   # 决策轮盘服务
    │   └── routers/           # 路由层
    │       └── wheel.py       # 决策轮盘 API
    ├── imagegen/              # 文本转图片模块
    │   ├── __init__.py
    │   ├── services/          # 服务层
    │   │   └── imagegen_service.py # 图片生成服务
    │   └── routers/           # 路由层
    │       └── imagegen.py    # 图片生成 API
    ├── codeexec/              # 代码执行器模块
    │   ├── __init__.py
    │   ├── services/          # 服务层
    │   │   └── codeexec_service.py # 代码执行服务
    │   └── routers/           # 路由层
    │       └── codeexec.py    # 代码执行 API
    ├── db/                    # 数据库模块
    │   ├── __init__.py
    │   └── database.py        # 数据库操作
    └── utils/                 # 工具模块
        ├── __init__.py
        └── helpers.py         # 辅助函数
```

## 3. 模块职责说明

### 3.1 配置模块 (config/)

**职责：**
- 系统配置的加载、保存和管理
- 定义数据目录路径
- 提供配置访问接口

**文件：**
- `settings.py`: 配置管理核心，包含配置加载、保存、获取等功能

**接口：**
```python
load_settings()        # 加载配置文件
save_settings()        # 保存配置文件
get_settings()         # 获取当前配置
update_settings(data)  # 更新配置
get_setting(key, default)  # 获取单个配置项
```

### 3.2 核心模块 (core/)

#### 3.2.1 LLM 模块 (core/llm/)

**职责：**
- LLM 提供商的抽象接口定义
- 多种 LLM 提供商实现（Mock、OpenAI、Deepseek、Qwen）
- 聊天会话管理

**文件：**
- `providers.py`: LLMProvider 抽象基类及具体实现
- `chat_manager.py`: 聊天管理器，管理对话会话和消息

**接口：**
```python
# providers.py
LLMProvider          # 抽象基类
MockProvider         # 模拟提供商
OpenAIProvider       # OpenAI 兼容提供商
create_provider()    # 创建提供商工厂函数

# chat_manager.py
ChatManager          # 聊天管理器
Conversation         # 对话会话数据类
LLMMessage           # 消息数据类
LLMResponse          # 响应数据类
```

#### 3.2.2 TTS 模块 (core/tts/)

**职责：**
- 语音合成引擎抽象和实现
- 支持 GPT-SoVITS 和 Edge TTS
- 情绪检测和合成

**文件：**
- `engine.py`: TTSEngine 实现

**接口：**
```python
TTSEngine                      # TTS 引擎类
synthesize(text, on_complete, on_error)    # 合成语音
synthesize_with_emotion(text, emotion, ...) # 带情绪合成
detect_emotion(text)           # 检测情绪
```

#### 3.2.3 WebSocket 模块 (core/websocket/)

**职责：**
- WebSocket 连接管理
- 消息广播功能

**文件：**
- `connection.py`: ConnectionManager 实现

**接口：**
```python
ConnectionManager      # 连接管理器
connect(websocket)     # 建立连接
disconnect(websocket)  # 断开连接
broadcast(message)     # 广播消息
send_to(websocket, message)  # 发送到指定连接
```

### 3.3 聊天模块 (chat/)

**职责：**
- 聊天相关 API 接口
- WebSocket 流式聊天
- 头像服务

**文件：**
- `routers/chat.py`: 聊天 HTTP API（历史、发送消息）
- `routers/websocket.py`: WebSocket 流式聊天
- `routers/avatar.py`: 头像 API

**接口：**
```
GET  /api/chat/history        # 获取聊天历史
DELETE /api/chat/history      # 清空聊天历史
POST /api/chat/send           # 发送消息
WS   /api/chat/ws             # 流式聊天

GET  /api/avatar/zhiyan       # 获取 AI 头像
GET  /api/avatar/user         # 获取用户头像
```

### 3.4 音乐模块 (music/)

#### 3.4.1 服务层 (music/services/)

**职责：**
- 歌词解析服务
- 封面搜索服务
- 播放列表管理服务

**文件：**
- `lyrics_parser.py`: 解析 LRC 歌词文件
- `cover_finder.py`: 根据歌曲标题查找封面
- `playlist_manager.py`: 管理播放列表和收藏

**接口：**
```python
# lyrics_parser.py
LyricsParser.parse_lrc_file(filepath)   # 解析 LRC 文件
LyricsParser.find_lyrics(song_title)    # 查找歌词

# cover_finder.py
CoverFinder.find_cover(song_title)      # 查找封面

# playlist_manager.py
PlaylistManager.get_songs()             # 获取所有歌曲
PlaylistManager.get_favorites()         # 获取收藏列表
PlaylistManager.toggle_favorite(filename) # 切换收藏状态
```

#### 3.4.2 路由层 (music/routers/)

**职责：**
- 音乐播放 API

**文件：**
- `music.py`: 音乐播放相关路由

**接口：**
```
GET  /api/music/playlist          # 获取播放列表
GET  /api/music/favorites         # 获取收藏列表
POST /api/music/favorite          # 切换收藏状态
GET  /api/music/stream/{filename} # 流式播放音乐
GET  /api/music/cover/{filename}  # 获取封面图片
GET  /api/music/lyrics/{title}    # 获取歌词
```

### 3.5 数据库模块 (db/)

**职责：**
- SQLite 数据库连接管理
- 数据库表初始化
- 基础 CRUD 操作

**文件：**
- `database.py`: 数据库操作实现

**接口：**
```python
get_db()                    # 获取数据库连接
init_db()                   # 初始化数据库表
execute_query(query, params, fetch_one)  # 执行查询
execute_insert(query, params)            # 执行插入
execute_update(query, params)            # 执行更新
execute_delete(query, params)            # 执行删除
```

### 3.6 经济系统模块 (economy/)

**职责：**
- 签到系统
- 打工系统
- 商店系统
- 背包系统
- 用户经济状态管理

**文件：**
- `services/economy_service.py`: 经济系统业务逻辑
- `routers/economy.py`: 经济系统 API 路由

**接口：**
```
GET  /api/economy/status        # 获取经济状态
POST /api/economy/checkin       # 每日签到
POST /api/economy/work          # 打工赚取金币
GET  /api/economy/shop/list     # 获取商店物品列表
POST /api/economy/shop/buy      # 购买物品
GET  /api/economy/inventory     # 获取背包物品
POST /api/economy/use_item      # 使用物品
```

### 3.7 漂流瓶模块 (bottle/)

**职责：**
- 扔瓶子功能
- 捡瓶子功能
- 瓶子回复功能
- 我的瓶子管理

**文件：**
- `services/bottle_service.py`: 漂流瓶业务逻辑
- `routers/bottle.py`: 漂流瓶 API 路由

**接口：**
```
POST /api/bottle/throw          # 扔瓶子
GET  /api/bottle/pick           # 捡瓶子
GET  /api/bottle/replies/{id}   # 获取瓶子回复
POST /api/bottle/reply          # 回复瓶子
GET  /api/bottle/my_bottles     # 获取我的瓶子
```

### 3.8 五子棋模块 (gomoku/)

**职责：**
- 创建新游戏
- 玩家落子
- AI 落子（简单策略）
- 胜负判定
- 游戏状态管理

**文件：**
- `services/gomoku_service.py`: 五子棋业务逻辑
- `routers/gomoku.py`: 五子棋 API 路由

**接口：**
```
POST /api/gomoku/new            # 创建新游戏
POST /api/gomoku/move           # 玩家落子
GET  /api/gomoku/status/{id}    # 获取游戏状态
POST /api/gomoku/cancel/{id}    # 取消游戏
```

### 3.9 盲盒系统模块 (blindbox/)

**职责：**
- 盲盒抽取（随机概率）
- 收藏管理
- 开启历史记录
- 稀有度系统

**文件：**
- `services/blindbox_service.py`: 盲盒业务逻辑
- `routers/blindbox.py`: 盲盒 API 路由

**接口：**
```
POST /api/blindbox/open         # 开启盲盒
GET  /api/blindbox/collection   # 获取收藏列表
GET  /api/blindbox/history      # 获取开启历史
```

### 3.10 决策轮盘模块 (wheel/)

**职责：**
- 随机决策（自定义选项）
- 预设选项管理
- 自定义轮盘创建和管理

**文件：**
- `services/wheel_service.py`: 决策轮盘业务逻辑
- `routers/wheel.py`: 决策轮盘 API 路由

**接口：**
```
POST /api/wheel/spin            # 转动轮盘（自定义选项）
GET  /api/wheel/preset          # 获取预设选项
POST /api/wheel/custom          # 创建自定义轮盘
GET  /api/wheel/custom/list     # 获取自定义轮盘列表
POST /api/wheel/custom/spin/{id} # 转动自定义轮盘
DELETE /api/wheel/custom/{id}   # 删除自定义轮盘
```

### 3.11 文本转图片模块 (imagegen/)

**职责：**
- 文本生成图片（模拟）
- 生成历史记录管理

**文件：**
- `services/imagegen_service.py`: 图片生成业务逻辑
- `routers/imagegen.py`: 图片生成 API 路由

**接口：**
```
POST /api/imagegen/generate     # 生成图片
GET  /api/imagegen/history      # 获取生成历史
DELETE /api/imagegen/history/{id} # 删除生成历史
```

### 3.12 代码执行器模块 (codeexec/)

**职责：**
- 沙箱 Python 代码执行
- 安全限制（禁止危险模块）
- 执行历史记录管理

**文件：**
- `services/codeexec_service.py`: 代码执行业务逻辑
- `routers/codeexec.py`: 代码执行 API 路由

**接口：**
```
POST /api/code/run              # 执行代码
GET  /api/code/history          # 获取执行历史
DELETE /api/code/history/{id}   # 删除执行历史
```

### 3.13 工具模块 (utils/)

**职责：**
- 通用辅助函数
- 数据格式化工具

**文件：**
- `helpers.py`: 辅助函数实现

**接口：**
```python
format_size(bytes)    # 格式化文件大小
format_time(seconds)  # 格式化时间
```

## 4. 模块依赖关系

### 4.1 依赖关系图

```
app.py (主入口)
    │
    ├── config/settings.py
    ├── core/llm/chat_manager.py
    │       └── core/llm/providers.py
    ├── core/tts/engine.py
    ├── core/websocket/connection.py
    ├── chat/routers/chat.py
    │       ├── core/llm/chat_manager.py
    │       └── config/settings.py
    ├── chat/routers/websocket.py
    │       ├── core/llm/chat_manager.py
    │       ├── core/websocket/connection.py
    │       └── config/settings.py
    ├── chat/routers/avatar.py
    │       └── config/settings.py
    ├── music/routers/music.py
    │       └── music/services/
    │               ├── playlist_manager.py
    │               ├── lyrics_parser.py
    │               └── cover_finder.py
    └── db/database.py
            └── config/settings.py
```

### 4.2 依赖说明

| 模块 | 依赖模块 | 说明 |
|------|----------|------|
| app.py | 所有模块 | 主入口，注册所有路由 |
| chat/routers | core/llm, core/websocket, config | 聊天功能依赖 LLM 和配置 |
| music/routers | music/services | 路由层依赖服务层 |
| music/services | config | 服务层依赖配置（路径等） |
| db/database.py | config | 数据库依赖配置（路径） |
| core/tts/engine.py | config | TTS 依赖配置（输出目录） |

## 5. 调用流程

### 5.1 聊天流程

```
客户端 → POST /api/chat/send
            ↓
        chat/routers/chat.py
            ↓
        core/llm/chat_manager.py
            ↓
        core/llm/providers.py (LLMProvider)
            ↓
        返回响应给客户端
```

### 5.2 流式聊天流程

```
客户端 → WebSocket /api/chat/ws
            ↓
        chat/routers/websocket.py
            ↓
        core/websocket/connection.py
            ↓
        core/llm/chat_manager.py
            ↓
        core/llm/providers.py (stream)
            ↓
        通过 WebSocket 推送 chunks
```

### 5.3 音乐播放流程

```
客户端 → GET /api/music/playlist
            ↓
        music/routers/music.py
            ↓
        music/services/playlist_manager.py
            ↓
        返回播放列表

客户端 → GET /api/music/stream/{filename}
            ↓
        music/routers/music.py
            ↓
        返回音乐文件流
```

### 5.4 本地音乐导入流程

```
客户端 → POST /api/music/upload（上传音频文件）
            ↓
        music/routers/music.py
            ↓
        music/services/metadata_extractor.py（提取标题/歌手/封面）
            ↓
        保存文件到本地 Songs 目录
            ↓
        添加到播放列表
```

## 6. 错误处理机制

### 6.1 统一错误处理

系统采用 FastAPI 的 HTTPException 进行统一错误处理：

```python
from fastapi import HTTPException

# 参数错误
raise HTTPException(status_code=400, detail="参数错误")

# 资源不存在
raise HTTPException(status_code=404, detail="资源不存在")

# 服务器错误
raise HTTPException(status_code=500, detail="服务器内部错误")
```

### 6.2 日志记录规范

所有模块使用统一的日志记录器：

```python
import logging

logger = logging.getLogger("ZhiYanWeb")

# 信息级别
logger.info("操作成功")

# 警告级别
logger.warning("潜在问题")

# 错误级别
logger.error("错误信息")
```

## 7. 扩展指南

### 7.1 添加新的 LLM 提供商

1. 在 `core/llm/providers.py` 中实现 `LLMProvider` 接口
2. 在 `create_provider()` 函数中注册新提供商
3. 更新配置文件支持新提供商类型

### 7.2 添加新的 TTS 引擎

1. 在 `core/tts/engine.py` 的 `TTSEngine` 类中添加新引擎支持
2. 实现对应的合成方法
3. 更新 `__init__` 中的引擎选择逻辑

### 7.3 添加新的音乐服务

1. 在 `music/services/` 下新增服务文件并在 `services/__init__.py` 导出
2. 在 `music/routers/` 下新增路由文件，并在 `routers/__init__.py` 与 `app.py` 注册

### 7.4 添加新的 API 路由

1. 在对应模块的 `routers/` 目录下创建新路由文件
2. 在 `app.py` 中注册新路由

## 8. 运行方式

```bash
# 开发模式
cd "zhiyan agent web"
python backend/main.py

# 使用 uvicorn
uvicorn zhiyan_web.app:app --host 0.0.0.0 --port 8000 --reload

# 生产模式
uvicorn zhiyan_web.app:app --host 0.0.0.0 --port 8000
```

## 9. 测试验证

重构后的系统应通过以下测试验证：

- `GET /api/status` - 系统状态正常
- `GET /api/settings` - 配置获取正常
- `POST /api/chat/send` - 聊天功能正常
- `GET /api/music/playlist` - 音乐播放列表正常
- `GET /api/music/download/search` - 音乐搜索正常
- WebSocket 连接正常
