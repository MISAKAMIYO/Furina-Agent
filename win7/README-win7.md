# Furina Agent 在 Windows 7 上运行（Python 3.8.10）

Windows 7 最高只支持到 **Python 3.8.10**。本目录是一层兼容层 + 精简后端，让 **Agent 功能**（设置、会话、流式对话、工具调用、记忆）在 Win7 上跑起来。

音乐播放器、BetterGI 托管、OCR、截屏录屏、DSH、插件市场这些依赖重或依赖 Win10+ API 的模块**不加载**，也不会出现在界面上能用的入口里。

---

## 一、装什么

1. **Python 3.8.10**（64 位）：<https://www.python.org/ftp/python/3.8.10/python-3.8.10-amd64.exe>
   安装时勾选 **Add Python to PATH**。
2. 在本目录（项目根）执行：

```bat
python -m venv .venv38
.venv38\Scripts\activate
python -m pip install -r win7\requirements-win7.txt
```

依赖都钉死在 3.8 / Win7 上验证过的版本；`cryptography` 必须是 3.4.x（41+ 的 Windows 构建依赖 Win10 API）。

## 二、启动

双击 **`start_win7.bat`**，或：

```bat
python win7\launch.py              :: 自动挑端口并打开浏览器
python win7\launch.py --port 8001  :: 固定端口
python win7\launch.py --no-browser :: 只起服务
```

启动后浏览器打开 `http://127.0.0.1:<端口>/`。第一次使用在「设置」里填 API Key（与主程序共用 `data/settings.json`，已加密的 Key 能直接解密）。

## 三、兼容层做了什么

`win7/compat38.py` 在**任何 `zhiyan_web` 模块之前**导入，只在 `sys.version_info < (3, 9)` 时生效：

| 内容 | 说明 |
|---|---|
| `asyncio.to_thread` | 3.9 才有，用 `run_in_executor` 等价实现 |
| `functools.cache` | 3.9 才有，用 `lru_cache(maxsize=None)` |
| `math.lcm` / `itertools.pairwise` / `itertools.batched` | 3.9/3.10 才有 |
| `datetime.UTC` | 3.11 才有 |
| `zoneinfo` / `tomllib` / `graphlib` | 映射到 `backports.zoneinfo` / `tomli` / `graphlib_backport`（没装就跳过） |

另外在源码里修了一处 3.8 的真实崩溃点：

- 13 个模块把 `Callable` / `AsyncGenerator` 等从 `collections.abc` 导入，并**在运行期**做 `Callable[..., Any]`（TypeVar 的 bound）。
  3.8 的 `collections.abc.Callable` 不可下标，会抛 `TypeError: 'ABCMeta' object is not subscriptable`。
  这些导入已改为 `typing` 版本（3.9+ 同样可用，行为一致）。

> 注意：**不能**在运行期改 `collections.abc.Callable`——`typing.Callable.__origin__` 就是它，改掉会让 typing 自己下标时报错。这是实测踩到的坑。

## 四、精简后端做了什么

`win7/agent_app.py` 只挂：

- `/api/settings`（读/写配置）
- `/api/chat/*`（会话、历史、`agent-send-stream` 流式、审批、检查点、fork）
- `/api/memory/*`、`/api/likability/*`
- 电脑操控工具注册（文件读写、命令、grep 等，纯 Python 部分）
- `/static/*`、`/`、`/api/health`

每个路由用 `try/except` 单独挂载：某个模块在 Win7 上 import 失败，只跳过它，不影响整体启动。

## 五、已知限制

1. **没有桌面壳**：Win7 上没有可用的现代 WebView2/pywebview 组合，直接开浏览器；窗口行为和主程序不同（没有置顶/无边框）。
2. **音乐、BetterGI、OCR、截屏、DSH、插件市场**不在 Win7 版里；界面上点这些入口会 404。
3. **TTS** 依赖 `edge-tts`，需要 Python 3.9+，Win7 版不启用。
4. `data/` 目录与主程序共用，建议先备份再在 Win7 上跑，避免两个版本同时写同一份会话。
5. 工具里涉及 `pywin32` 的部分（窗口句柄、剪贴板增强）需要额外装 `pywin32`，不装则相关工具禁用。

## 六、自检

```bat
python win7\selftest.py
```

会依次检查：Python 版本 → 兼容补丁 → 精简后端导入 → 路由数量 → 用 mock provider 跑一次流式对话。
