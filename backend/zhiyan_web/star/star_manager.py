"""
PluginManager - 插件生命周期管理器
完全兼容 AstrBot 插件格式，支持直接加载 AstrBot 插件

插件格式要求:
- 插件目录必须包含 main.py 或 <目录名>.py 作为入口
- 元数据文件: metadata.yaml / metadata.yml / metadata.json / plugin.json
- 插件类必须继承 Star 基类（自动通过 __init_subclass__ 注册）
"""
from __future__ import annotations

import asyncio
import functools
import importlib.util
import inspect
import json
import logging
import os
import shutil
import subprocess
import sys
import traceback
import zipfile
from types import ModuleType

from .star import StarMetadata, star_map, star_registry, _normalize_platform, resolve_platforms
from .star_handler import EventType, StarHandlerMetadata, star_handlers_registry
from .context import Context
from zhiyan_web.config import PROJECT_ROOT, DATA_DIR, BUILTIN_PLUGINS_DIR

logger = logging.getLogger("ZhiYanWeb.star")

PLUGIN_METADATA_FILENAMES = ["metadata.yaml", "metadata.yml", "metadata.json", "plugin.json"]

# 平台标签（供前端展示）：标准平台 → 显示名
_PLATFORM_LABELS = {
    "qq": "QQ",
    "webchat": "网页",
}


def _platform_tags(platforms: list[str]) -> list[str]:
    """将（已解析的）平台列表转为展示标签；全平台返回 ['通用']"""
    if not platforms:
        return ["通用"]
    if "qq" in platforms and "webchat" in platforms:
        return ["通用"]
    tags = []
    for p in platforms:
        label = _PLATFORM_LABELS.get(p, p)
        if label not in tags:
            tags.append(label)
    return tags or ["通用"]


def _get_handler_params(handler_fn):
    """获取 handler 函数的参数名列表"""
    try:
        sig = inspect.signature(handler_fn)
        return list(sig.parameters.keys())
    except (ValueError, TypeError):
        return []


class PluginManager:
    """插件管理器，负责插件的发现、加载、卸载、重载和启停"""

    def __init__(self, context: Context, config: dict) -> None:
        self.context = context
        self.context._star_manager = self
        self.config = config

        # 插件存储路径
        self.plugin_store_path = os.path.join(DATA_DIR, "plugins")
        """用户安装的插件路径: data/plugins/"""
        self.plugin_config_path = os.path.join(DATA_DIR, "plugin_configs")
        """插件配置存储路径: data/plugin_configs/"""
        self.reserved_plugin_path = BUILTIN_PLUGINS_DIR
        """内置插件路径"""
        self.conf_schema_fname = "_conf_schema.json"

        os.makedirs(self.plugin_store_path, exist_ok=True)
        os.makedirs(self.plugin_config_path, exist_ok=True)
        os.makedirs(self.reserved_plugin_path, exist_ok=True)

        self._pm_lock = asyncio.Lock()
        self.failed_plugin_dict: dict[str, dict] = {}

    # ────────── 插件发现 ──────────

    @staticmethod
    def _get_classes(module: ModuleType) -> list[str]:
        classes = []
        clsmembers = inspect.getmembers(module, inspect.isclass)
        for name, cls_obj in clsmembers:
            if name.lower().endswith("plugin") or name.lower() == "main":
                from .base import Star as ZhiYanStar
                # 检查是否继承自任一 Star 基类（zhiyan_web 或 astrbot）
                try:
                    from astrbot.core.star.base import Star as AstrbotStar
                    if (issubclass(cls_obj, AstrbotStar) and cls_obj is not AstrbotStar):
                        classes.append(name)
                        break
                except ImportError:
                    pass
                if issubclass(cls_obj, ZhiYanStar) and cls_obj is not ZhiYanStar:
                    classes.append(name)
                    break
        return classes

    @staticmethod
    def _is_plugin_base_subclass(cls_obj: type) -> bool:
        """检查是否为 PluginBase 子类（旧版兼容）"""
        try:
            from zhiyan_web.plugins.base import PluginBase
            return issubclass(cls_obj, PluginBase) and cls_obj is not PluginBase
        except Exception:
            return False

    @staticmethod
    def _get_plugin_base_classes(module: ModuleType) -> list[str]:
        """获取模块中的 PluginBase 子类（旧版兼容）"""
        classes = []
        clsmembers = inspect.getmembers(module, inspect.isclass)
        for name, cls_obj in clsmembers:
            if name.lower().endswith("plugin") or name.lower() == "main":
                if PluginManager._is_plugin_base_subclass(cls_obj):
                    classes.append(name)
                    break
        return classes

    def _register_plugin_base_handlers(
        self, plugin_instance, module: ModuleType, module_path: str, root_dir_name: str
    ):
        """注册 PluginBase 插件的 on_message 为 StarHandlerMetadata

        将旧版 PluginBase 插件的 on_message 方法包装为 AstrBot 兼容的 Handler，
        使其能够被 pipeline 的 StarRequestSubStage 处理。
        """
        import inspect as _inspect

        # 检查是否有 on_message 方法
        on_message = getattr(plugin_instance, "on_message", None)
        if not on_message or not _inspect.iscoroutinefunction(on_message):
            logger.debug("插件 %s 没有 on_message 方法，跳过 Handler 注册", root_dir_name)
            return

        # 检查插件是否注册了命令（通过 PluginContext.register_commands 注册的已经被处理）
        # 如果已经注册了 handler，则不需要额外处理
        existing = star_handlers_registry.get_handlers_by_module_name(module_path)
        if existing:
            logger.debug("插件 %s 已有已注册的 Handler，跳过自动注册", root_dir_name)
            return

        # 创建包装 handler
        async def _wrapped_handler(event):
            """将 AstrMessageEvent 转换为 dict 并调用 PluginBase.on_message"""
            try:
                message_str = event.message_str if hasattr(event, 'message_str') else str(event)
                message_dict = {"content": message_str, "type": "message"}
                result = await on_message(message_dict)
                if result is not None:
                    return {"response": str(result)}
            except Exception as e:
                logger.error("PluginBase on_message 执行异常 %s: %s", root_dir_name, e)
            return None

        # 创建 Handler 元数据
        handler_name = f"{root_dir_name}_on_message"
        handler_full_name = f"{module_path}.{handler_name}"
        handler_md = StarHandlerMetadata(
            event_type=EventType.AdapterMessageEvent,
            handler_full_name=handler_full_name,
            handler_name=handler_name,
            handler_module_path=module_path,
            handler=_wrapped_handler,
            event_filters=[],
            desc=f"PluginBase 插件 {root_dir_name} 的消息处理器",
        )

        # 注册到全局 registry
        star_handlers_registry.append(handler_md)
        logger.info("已注册 PluginBase 插件 Handler: %s (%s)", root_dir_name, handler_full_name)

    @staticmethod
    def _get_modules(path: str) -> list[dict]:
        modules = []
        if not os.path.exists(path):
            return modules
        for d in os.listdir(path):
            dir_path = os.path.join(path, d)
            if not os.path.isdir(dir_path):
                continue
            if os.path.exists(os.path.join(dir_path, "main.py")):
                module_str = "main"
            elif os.path.exists(os.path.join(dir_path, f"{d}.py")):
                module_str = d
            else:
                continue
            modules.append({
                "pname": d,
                "module": module_str,
                "module_path": os.path.join(dir_path, module_str),
            })
        return modules

    def _get_plugin_modules(self) -> list[dict]:
        plugins = []
        # 先收集内置（reserved）插件名，供去重使用
        reserved_names = set()
        if os.path.exists(self.reserved_plugin_path):
            for p in self._get_modules(self.reserved_plugin_path):
                reserved_names.add(p["pname"])
        # 用户安装插件（内置已提供同名核心插件时跳过，避免重复加载）
        if os.path.exists(self.plugin_store_path):
            for p in self._get_modules(self.plugin_store_path):
                if p["pname"] in reserved_names:
                    continue
                p["reserved"] = False
                plugins.append(p)
        # 内置核心插件（reserved）
        if os.path.exists(self.reserved_plugin_path):
            for p in self._get_modules(self.reserved_plugin_path):
                p["reserved"] = True
                plugins.append(p)
        return plugins

    # ────────── 元数据加载 ──────────

    def _load_plugin_metadata(self, plugin_path: str) -> StarMetadata | None:
        """从插件目录加载元数据，支持 YAML 和 JSON 格式"""
        plugin_root = os.path.abspath(plugin_path)
        if not os.path.exists(plugin_root):
            return None

        for fname in PLUGIN_METADATA_FILENAMES:
            fpath = os.path.join(plugin_root, fname)
            if not os.path.exists(fpath):
                continue
            try:
                if fname.endswith((".yaml", ".yml")):
                    try:
                        import yaml
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = yaml.safe_load(f)
                    except ImportError:
                        logger.warning("yaml 未安装，跳过 YAML 元数据: %s", fpath)
                        continue
                else:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)

                if not isinstance(data, dict):
                    continue

                if "desc" not in data and "description" in data:
                    data["desc"] = data["description"]

                return StarMetadata(
                    name=data.get("name", os.path.basename(plugin_path)),
                    author=data.get("author", "unknown"),
                    desc=data.get("desc", ""),
                    short_desc=data.get("short_desc", ""),
                    version=data.get("version", "1.0.0"),
                    repo=data.get("repo", ""),
                    display_name=data.get("display_name", ""),
                    support_platforms=data.get("support_platforms", []),
                    astrbot_version=data.get("astrbot_version", None),
                    pages=data.get("pages", []),
                )
            except Exception as e:
                logger.warning("加载插件元数据失败 %s: %s", fpath, e)
        return None

    def _load_plugin_config(self, root_dir_name: str, plugin_dir_path: str | None = None) -> dict | None:
        """加载插件配置"""
        config_path = os.path.join(self.plugin_config_path, f"{root_dir_name}_config.json")
        config_data = None
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except Exception:
                pass

        if config_data is None:
            config_data = {}

        # 应用 _conf_schema.json 中的默认值
        if plugin_dir_path:
            schema_path = os.path.join(plugin_dir_path, self.conf_schema_fname)
            if os.path.exists(schema_path):
                try:
                    with open(schema_path, "r", encoding="utf-8") as f:
                        schema = json.load(f)
                    self._apply_schema_defaults(config_data, schema)
                except Exception:
                    pass

        return config_data

    @staticmethod
    def _apply_schema_defaults(config: dict, schema: dict) -> None:
        """递归应用 _conf_schema.json 中的默认值到配置中"""
        for key, value in schema.items():
            if key in ("ui:widget", "hint", "obvious_hint", "description", "options", "enum"):
                continue
            if key not in config:
                if "default" in value:
                    config[key] = value["default"]
                elif isinstance(value, dict) and "items" in value:
                    # 嵌套对象，创建空字典并递归应用默认值
                    nested = {}
                    PluginManager._apply_schema_defaults(nested, value["items"])
                    if nested:
                        config[key] = nested
            elif isinstance(value, dict) and "items" in value:
                if isinstance(config.get(key), dict):
                    PluginManager._apply_schema_defaults(config[key], value["items"])
                elif isinstance(config.get(key), str) and config[key] == "":
                    # 空字符串，也尝试创建嵌套默认值
                    nested = {}
                    PluginManager._apply_schema_defaults(nested, value["items"])
                    if nested:
                        config[key] = nested

    # ────────── 模块导入 ──────────

    async def _import_plugin_module(
        self, path: str, module_str: str, root_dir_name: str, plugin_dir_path: str
    ) -> ModuleType:
        module_file_path = os.path.join(plugin_dir_path, f"{module_str}.py")
        if not os.path.exists(module_file_path):
            raise ModuleNotFoundError(f"模块文件不存在: {module_file_path}")

        # 添加路径到 sys.path
        backend_dir = os.path.join(PROJECT_ROOT, "backend")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        if plugin_dir_path not in sys.path:
            sys.path.insert(0, plugin_dir_path)

        # 确保 data.plugins 作为包命名空间存在（支持 from data.plugins.xxx import ...）
        project_root = PROJECT_ROOT
        data_plugins_path = os.path.join(DATA_DIR, "plugins")
        if "data" not in sys.modules:
            import types
            data_pkg = types.ModuleType("data")
            data_pkg.__path__ = [os.path.join(project_root, "data")]
            sys.modules["data"] = data_pkg
        if "data.plugins" not in sys.modules:
            import types
            dp_pkg = types.ModuleType("data.plugins")
            dp_pkg.__path__ = [data_plugins_path]
            sys.modules["data.plugins"] = dp_pkg

        # 检查是否包含相对导入（如 from .xxx import ...）
        has_relative_import = False
        try:
            with open(module_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            # 检查相对导入模式
            has_relative_import = "from ." in content
        except Exception:
            pass

        if has_relative_import:
            # 确保 __init__.py 存在，使插件目录成为包
            init_path = os.path.join(plugin_dir_path, "__init__.py")
            if not os.path.exists(init_path):
                try:
                    with open(init_path, "w", encoding="utf-8") as f:
                        f.write(f'# Auto-generated package init for {root_dir_name}\n')
                except Exception:
                    pass

            # 以包方式加载插件
            # path 格式: data.plugins.{root_dir_name}.{module_str}
            # 先加载包，再导入子模块
            package_name = f"data.plugins.{root_dir_name}"
            if package_name not in sys.modules:
                package_spec = importlib.util.spec_from_file_location(
                    package_name,
                    os.path.join(plugin_dir_path, "__init__.py"),
                    submodule_search_locations=[plugin_dir_path],
                )
                if package_spec:
                    package_module = importlib.util.module_from_spec(package_spec)
                    sys.modules[package_name] = package_module
                    if package_spec.loader:
                        package_spec.loader.exec_module(package_module)

            # 加载 main 子模块
            import importlib as _importlib
            try:
                module = _importlib.import_module(f"{package_name}.{module_str}")
                sys.modules[path] = module
                return module
            except Exception:
                # 回退到标准加载方式
                pass

        # 标准加载方式（不包含相对导入）
        spec = importlib.util.spec_from_file_location(path, module_file_path)
        if spec is None:
            raise ModuleNotFoundError(f"无法创建模块规范: {path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[path] = module

        spec.loader.exec_module(module)
        return module

    # ────────── 插件加载 ──────────

    async def load(self, specified_dir_name: str = None):
        """加载所有插件

        Args:
            specified_dir_name: 指定加载的插件目录名（可选）
        """
        plugin_modules = self._get_plugin_modules()
        if not plugin_modules:
            logger.info("未找到任何插件模块")
            return True, None

        has_load_error = False
        loaded_count = 0

        for plugin_module in plugin_modules:
            try:
                module_str = plugin_module["module"]
                root_dir_name = plugin_module["pname"]
                reserved = plugin_module.get("reserved", False)

                plugin_dir_path = (
                    os.path.join(self.plugin_store_path, root_dir_name)
                    if not reserved
                    else os.path.join(self.reserved_plugin_path, root_dir_name)
                )

                path = "data.plugins." if not reserved else "zhiyan_web.plugins.builtin_plugins."
                path += root_dir_name + "." + module_str

                if specified_dir_name and root_dir_name != specified_dir_name:
                    continue

                # 检查是否被禁用
                try:
                    from zhiyan_web.config.settings import get_settings
                    _settings = get_settings()
                    _disabled = _settings.get("disabled_plugins", [])
                    if root_dir_name in _disabled:
                        logger.info("插件 %s 已被禁用，跳过加载", root_dir_name)
                        continue
                except Exception:
                    pass

                logger.info("加载插件: %s ...", root_dir_name)

                # 自动安装第三方依赖（仅用户安装插件；内置核心插件不触发 pip 安装）
                if not reserved:
                    try:
                        from .dependency_installer import get_dependency_installer
                        _dep_result = await get_dependency_installer().ensure_dependencies(
                            root_dir_name, plugin_dir_path
                        )
                        if _dep_result.get("installed_count"):
                            logger.info(
                                "插件 %s 已自动安装 %d 个依赖",
                                root_dir_name,
                                _dep_result["installed_count"],
                            )
                        elif _dep_result.get("status") == "failed":
                            logger.warning(
                                "插件 %s 依赖安装不完整: %s",
                                root_dir_name,
                                _dep_result.get("message", ""),
                            )
                    except Exception as e:
                        logger.warning("插件 %s 自动安装依赖异常: %s", root_dir_name, e)

                # 导入模块
                try:
                    module = await self._import_plugin_module(
                        path=path,
                        module_str=module_str,
                        root_dir_name=root_dir_name,
                        plugin_dir_path=plugin_dir_path,
                    )
                except Exception as e:
                    logger.error("插件 %s 导入失败: %s", root_dir_name, e)
                    has_load_error = True
                    self.failed_plugin_dict[root_dir_name] = {
                        "name": root_dir_name,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "reserved": reserved,
                    }
                    continue

                # 加载元数据
                metadata = self._load_plugin_metadata(plugin_dir_path)
                if not metadata:
                    metadata = StarMetadata(
                        name=root_dir_name,
                        author="unknown",
                        desc="",
                        version="1.0.0",
                    )

                # 加载插件配置（包含 _conf_schema.json 默认值）
                from astrbot.core.config.astrbot_config import AstrBotConfig
                plugin_config_dict = self._load_plugin_config(root_dir_name, plugin_dir_path)
                plugin_config = AstrBotConfig(plugin_config_dict)
                plugin_config._config_file = os.path.join(self.plugin_config_path, f"{root_dir_name}_config.json")

                # 检查是否通过 __init_subclass__ 注册
                if path in star_map:
                    meta = star_map[path]

                    # 检查是否为 PluginBase 子类（需要特殊处理）
                    is_plugin_base = self._is_plugin_base_subclass(meta.star_cls_type) if meta.star_cls_type else False

                    # YAML/JSON 元数据优先
                    metadata_yaml = self._load_plugin_metadata(plugin_dir_path)
                    if metadata_yaml:
                        meta.name = metadata_yaml.name or meta.name
                        meta.author = metadata_yaml.author or meta.author
                        meta.desc = metadata_yaml.desc or meta.desc
                        meta.short_desc = metadata_yaml.short_desc or meta.short_desc
                        meta.version = metadata_yaml.version or meta.version
                        meta.repo = metadata_yaml.repo or meta.repo
                        meta.display_name = metadata_yaml.display_name or meta.display_name
                        meta.support_platforms = metadata_yaml.support_platforms or meta.support_platforms
                        meta.astrbot_version = metadata_yaml.astrbot_version or meta.astrbot_version
                        meta.pages = metadata_yaml.pages or meta.pages

                    logger.info(meta)
                    meta.config = plugin_config
                    meta.module = module
                    meta.root_dir_name = root_dir_name
                    meta.reserved = reserved
                    meta.module_path = path

                    # 注入插件 ID 到类属性
                    if meta.star_cls_type:
                        p_author = (meta.author or "unknown").lower().replace("/", "_")
                        p_name = (meta.name or "unknown").lower().replace("/", "_")
                        setattr(meta.star_cls_type, "name", p_name)
                        setattr(meta.star_cls_type, "author", p_author)
                        setattr(meta.star_cls_type, "plugin_id", f"{p_author}/{p_name}")

                    # 实例化插件类
                    if meta.star_cls_type:
                        try:
                            # PluginBase 子类使用 PluginContext，Star 子类使用 Star Context
                            if is_plugin_base:
                                from zhiyan_web.plugins.context import PluginContext as LegacyPluginContext
                                from asyncio import Queue
                                legacy_ctx = LegacyPluginContext(
                                    event_queue=Queue(),
                                    config=self.config,
                                )
                                try:
                                    from zhiyan_web.core import star_context as global_star_context
                                    if global_star_context:
                                        legacy_ctx._star_context = global_star_context
                                except Exception:
                                    pass
                                plugin_context = legacy_ctx
                            else:
                                plugin_context = self.context

                            # 尝试多种实例化方式，兼容需要/不需要 config 的插件
                            instantiate_kwargs = {"context": plugin_context}
                            if plugin_config:
                                instantiate_kwargs["config"] = plugin_config
                            try:
                                meta.star_cls = meta.star_cls_type(**instantiate_kwargs)
                            except (TypeError, AttributeError):
                                # 尝试不带 config
                                try:
                                    meta.star_cls = meta.star_cls_type(context=plugin_context)
                                except (TypeError, AttributeError):
                                    # 尝试带空 AstrBotConfig
                                    from astrbot.core.config.astrbot_config import AstrBotConfig as _AstrBotConfig
                                    meta.star_cls = meta.star_cls_type(
                                        context=plugin_context, config=_AstrBotConfig({})
                                    )
                        except (TypeError, AttributeError):
                            # 回退：PluginBase 用 PluginContext，Star 用 Star Context
                            if is_plugin_base:
                                from zhiyan_web.plugins.context import PluginContext as LegacyPluginContext
                                from asyncio import Queue
                                legacy_ctx = LegacyPluginContext(
                                    event_queue=Queue(),
                                    config=self.config,
                                )
                                try:
                                    from zhiyan_web.core import star_context as global_star_context
                                    if global_star_context:
                                        legacy_ctx._star_context = global_star_context
                                except Exception:
                                    pass
                                try:
                                    meta.star_cls = meta.star_cls_type(context=legacy_ctx)
                                except (TypeError, AttributeError):
                                    from astrbot.core.config.astrbot_config import AstrBotConfig as _AstrBotConfig
                                    meta.star_cls = meta.star_cls_type(context=legacy_ctx, config=_AstrBotConfig({}))
                            else:
                                try:
                                    meta.star_cls = meta.star_cls_type(context=self.context)
                                except (TypeError, AttributeError):
                                    from astrbot.core.config.astrbot_config import AstrBotConfig as _AstrBotConfig
                                    meta.star_cls = meta.star_cls_type(context=self.context, config=_AstrBotConfig({}))

                        if meta.star_cls:
                            p_author = (meta.author or "unknown").lower().replace("/", "_")
                            p_name = (meta.name or "unknown").lower().replace("/", "_")
                            setattr(meta.star_cls, "name", p_name)
                            setattr(meta.star_cls, "author", p_author)
                            setattr(meta.star_cls, "plugin_id", f"{p_author}/{p_name}")

                    # 绑定 handler（将插件的 handler 方法绑定到实例，幂等处理）
                    related_handlers = star_handlers_registry.get_handlers_by_module_name(path)
                    for handler in related_handlers:
                        _inst = meta.star_cls
                        # 解开已存在的一层或多层 functools.partial，取最底层原始函数
                        _raw = handler.handler
                        while isinstance(_raw, functools.partial):
                            _raw = _raw.func
                        # 若当前 handler 已绑定到本实例，则跳过，避免重复预置实例导致多传参数
                        _cur = handler.handler
                        if (
                            isinstance(_cur, functools.partial)
                            and _cur.args
                            and _cur.args[0] is _inst
                            and _cur.func is _raw
                        ):
                            meta.star_handler_full_names.append(handler.handler_full_name)
                            continue
                        # 检查 handler 是否期望 context 参数（兼容 AstrBot 插件）
                        _has_context = 'context' in _get_handler_params(_raw)
                        if _has_context:
                            _ctx = getattr(_inst, 'context', None)
                            handler.handler = functools.partial(
                                _raw,
                                _inst,
                                context=_ctx,
                            )
                        else:
                            handler.handler = functools.partial(
                                _raw,
                                _inst,
                            )
                        meta.star_handler_full_names.append(handler.handler_full_name)

                else:
                    # 旧版方式：通过类名查找（Star 子类）
                    classes = self._get_classes(module)
                    plugin_base_classes = self._get_plugin_base_classes(module)

                    if not classes and not plugin_base_classes:
                        logger.warning(
                            "插件 %s 未通过 Star 注册，也没有找到旧版插件类。"
                            "请确认插件主类继承 Star，或类名以 Plugin 结尾 / 命名为 Main。",
                            root_dir_name,
                        )
                        continue

                    if classes:
                        plugin_cls = getattr(module, classes[0])
                        # 使用 Star 的 Context
                        context = self.context
                    else:
                        plugin_cls = getattr(module, plugin_base_classes[0])
                        # 使用旧版 PluginContext（兼容旧版插件）
                        from zhiyan_web.plugins.context import PluginContext as LegacyPluginContext
                        from asyncio import Queue
                        legacy_ctx = LegacyPluginContext(
                            event_queue=Queue(),
                            config=self.config,
                        )
                        # 为旧版插件创建专用上下文，注入 StarContext 的能力
                        try:
                            from zhiyan_web.core import star_context as global_star_context
                            if global_star_context:
                                legacy_ctx._star_context = global_star_context
                        except Exception:
                            pass
                        context = legacy_ctx

                    obj = None
                    try:
                        if plugin_config:
                            try:
                                obj = plugin_cls(context=context, config=plugin_config)
                            except (TypeError, AttributeError):
                                obj = plugin_cls(context=context)
                        else:
                            try:
                                obj = plugin_cls(context=context)
                            except (TypeError, AttributeError):
                                obj = plugin_cls(context=context, config={})
                    except Exception as e:
                        logger.warning("插件 %s 实例化失败: %s", root_dir_name, e)
                        continue

                    metadata.star_cls = obj
                    metadata.config = plugin_config
                    metadata.module = module
                    metadata.root_dir_name = root_dir_name
                    metadata.reserved = reserved
                    metadata.star_cls_type = plugin_cls
                    metadata.module_path = path
                    star_map[path] = metadata
                    star_registry.append(metadata)

                    # 绑定 handler 到实例（与 if 分支保持一致，避免 else 分支 handler 未绑定导致 Missing 'event'）
                    _related_handlers = star_handlers_registry.get_handlers_by_module_name(path)
                    for _handler in _related_handlers:
                        _raw = _handler.handler
                        while isinstance(_raw, functools.partial):
                            _raw = _raw.func
                        _cur = _handler.handler
                        if (
                            isinstance(_cur, functools.partial)
                            and _cur.args
                            and _cur.args[0] is obj
                            and _cur.func is _raw
                        ):
                            continue
                        _has_context = "context" in _get_handler_params(_raw)
                        if _has_context:
                            _ctx = getattr(obj, "context", None)
                            _handler.handler = functools.partial(_raw, obj, context=_ctx)
                        else:
                            _handler.handler = functools.partial(_raw, obj)

                    # 对 PluginBase 插件：更新元数据（从类属性获取）
                    if plugin_base_classes and not classes:
                        p_name = getattr(plugin_cls, "name", None) or getattr(obj, "name", None) or root_dir_name
                        p_author = getattr(plugin_cls, "author", None) or getattr(obj, "author", None) or "unknown"
                        p_desc = getattr(plugin_cls, "desc", None) or getattr(obj, "desc", None) or ""
                        p_version = getattr(plugin_cls, "version", None) or getattr(obj, "version", None) or "1.0.0"
                        metadata.name = p_name
                        metadata.author = p_author
                        metadata.desc = p_desc
                        metadata.version = p_version
                        # 设置插件 ID
                        p_author_id = p_author.lower().replace("/", "_")
                        p_name_id = p_name.lower().replace("/", "_")
                        setattr(obj, "name", p_name_id)
                        setattr(obj, "author", p_author_id)
                        setattr(obj, "plugin_id", f"{p_author_id}/{p_name_id}")
                        metadata.plugin_id = f"{p_author_id}/{p_name_id}"
                        # 注册 on_message 为 StarHandlerMetadata
                        try:
                            self._register_plugin_base_handlers(obj, module, path, root_dir_name)
                        except Exception as e:
                            logger.warning("注册 PluginBase Handler 失败 %s: %s", root_dir_name, e)

                # 调用 initialize()
                meta = star_map.get(path)
                if meta and meta.star_cls:
                    try:
                        await meta.star_cls.initialize()
                    except Exception as e:
                        logger.warning("插件 %s initialize 失败: %s", meta.name, e)

                meta.activated = True
                loaded_count += 1
                logger.info("插件加载成功: %s v%s", metadata.name, metadata.version)

            except Exception as e:
                logger.error("加载插件 %s 时出错: %s", plugin_module.get("pname", "unknown"), e)
                logger.error(traceback.format_exc())

        logger.info("插件加载完成: 已加载 %d 个, 失败 %d 个", loaded_count, len(self.failed_plugin_dict))
        if has_load_error:
            return False, self.failed_plugin_dict
        return True, None

    # ────────── 插件卸载 ──────────

    async def _terminate_plugin(self, metadata: StarMetadata):
        """终止插件"""
        if not metadata.activated:
            return
        if metadata.star_cls:
            try:
                await metadata.star_cls.terminate()
            except Exception as e:
                logger.warning("插件 %s terminate 失败: %s", metadata.name, e)

        # 触发插件卸载事件
        handlers = star_handlers_registry.get_handlers_by_event_type(EventType.OnPluginUnloadedEvent)
        for handler in handlers:
            try:
                await handler.handler(metadata)
            except Exception:
                logger.error(traceback.format_exc())

    def _cleanup_plugin_state(self, root_dir_name: str, reserved: bool = False):
        """清理插件状态"""
        prefix = "zhiyan_web.plugins.builtin_plugins." if reserved else "data.plugins."
        module_prefix = f"{prefix}{root_dir_name}"

        # 清理 sys.modules
        for key in list(sys.modules.keys()):
            if key == module_prefix or key.startswith(f"{module_prefix}."):
                del sys.modules[key]

        # 清理 star_map 和 star_registry
        for module_path, metadata in list(star_map.items()):
            if (metadata.root_dir_name == root_dir_name and metadata.reserved == reserved) or \
               module_path.startswith(module_prefix):
                star_map.pop(module_path, None)
                if metadata in star_registry:
                    star_registry.remove(metadata)
                break

        # 清理 handlers
        for handler in list(star_handlers_registry):
            if handler.handler_module_path.startswith(module_prefix):
                star_handlers_registry.remove(handler)

    async def unload(self, plugin_name: str):
        """卸载插件"""
        for metadata in list(star_registry):
            if metadata.name == plugin_name:
                await self._terminate_plugin(metadata)
                self._cleanup_plugin_state(
                    metadata.root_dir_name or plugin_name,
                    metadata.reserved,
                )
                logger.info("插件已卸载: %s", plugin_name)
                return True, f"插件 {plugin_name} 已卸载"
        return False, f"插件 {plugin_name} 未找到"

    # ────────── 插件重载 ──────────

    async def reload(self, plugin_name: str = None):
        """重载插件

        - plugin_name 指定时：仅重载该插件（清理其模块缓存 + 注册表），
          不影响其他已加载插件，避免 handler/过滤器重复注册。
        - plugin_name 为 None 时：重载所有插件，先清理全部插件模块缓存
          与注册表，确保相对导入插件（模块被 sys.modules 缓存）也能被
          重新执行并重新注册 handler。
        """
        async with self._pm_lock:
            if plugin_name:
                # 重载指定插件
                for metadata in list(star_registry):
                    if metadata.name == plugin_name:
                        await self._terminate_plugin(metadata)
                        self._cleanup_plugin_state(
                            metadata.root_dir_name or plugin_name,
                            metadata.reserved,
                        )
                        break
                # 仅加载指定插件，其他插件保持不变
                result = await self.load(plugin_name)
                return result

            # 重载所有插件
            for metadata in list(star_registry):
                try:
                    await self._terminate_plugin(metadata)
                except Exception as e:
                    logger.warning("插件 %s 终止失败: %s", metadata.name, e)

            # 清理所有插件模块的 sys.modules 缓存，确保模块被重新执行、
            # __init_subclass__ 与 @filter.command 装饰器重新注册 handler。
            # 保留 data.plugins / zhiyan_web.plugins.builtin_plugins 命名空间本身。
            for key in list(sys.modules.keys()):
                if key.startswith("data.plugins."):
                    del sys.modules[key]
                elif key.startswith("zhiyan_web.plugins.builtin_plugins."):
                    del sys.modules[key]

            star_handlers_registry.clear()
            star_map.clear()
            star_registry.clear()

            # 清空命令冲突检测的全局注册表，避免重载后基于旧数据的误报
            try:
                from .register.star_handler import _registered_command_names
                _registered_command_names.clear()
            except Exception:
                pass

            result = await self.load()
            return result

    # ────────── 插件启停 ──────────

    async def enable(self, plugin_name: str) -> bool:
        """启用插件"""
        metadata = self.context.get_registered_star(plugin_name)
        if not metadata:
            return False
        if metadata.activated:
            return True
        # 注意：reload 内部已获取 _pm_lock，此处不重复获取
        success, _ = await self.reload(plugin_name)
        return success

    async def disable(self, plugin_name: str) -> bool:
        """禁用插件"""
        async with self._pm_lock:
            metadata = self.context.get_registered_star(plugin_name)
            if not metadata:
                return False
            await self._terminate_plugin(metadata)
            metadata.activated = False
            logger.info("插件已禁用: %s", plugin_name)
            return True

    # ────────── 插件配置 ──────────

    async def get_plugin_config(self, plugin_name: str) -> dict | None:
        """获取插件配置"""
        metadata = self.context.get_registered_star(plugin_name)
        if not metadata:
            return None
        return metadata.config

    async def set_plugin_config(self, plugin_name: str, config: dict) -> bool:
        """更新插件配置（含 schema 类型验证）"""
        metadata = self.context.get_registered_star(plugin_name)
        if not metadata:
            return False

        # 获取 schema 进行类型验证
        root_dir = metadata.root_dir_name or plugin_name
        plugin_dir = os.path.join(
            self.reserved_plugin_path if metadata.reserved else self.plugin_store_path,
            root_dir,
        )
        schema_path = os.path.join(plugin_dir, self.conf_schema_fname)
        if os.path.exists(schema_path):
            try:
                with open(schema_path, "r", encoding="utf-8") as f:
                    schema = json.load(f)
                config = self._validate_config_types(config, schema)
            except Exception as e:
                logger.warning("插件 %s 配置 schema 验证失败: %s", plugin_name, e)

        metadata.config = config
        config_path = os.path.join(self.plugin_config_path, f"{root_dir}_config.json")
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            logger.info("插件 %s 配置已保存", plugin_name)
            return True
        except Exception as e:
            logger.error("保存插件配置失败: %s", e)
            return False

    @staticmethod
    def _validate_config_types(config: dict, schema: dict) -> dict:
        """根据 schema 类型验证并转换配置值"""
        result = {}
        for key, value in config.items():
            if key not in schema:
                result[key] = value
                continue
            field_schema = schema[key]
            if not isinstance(field_schema, dict):
                result[key] = value
                continue
            field_type = field_schema.get("type", "string")

            if field_type == "object" and isinstance(value, dict):
                items_schema = field_schema.get("items", {})
                result[key] = PluginManager._validate_config_types(value, items_schema)
            elif field_type == "bool":
                result[key] = bool(value)
            elif field_type == "int":
                try:
                    result[key] = int(value) if value is not None else field_schema.get("default", 0)
                except (ValueError, TypeError):
                    result[key] = field_schema.get("default", 0)
            elif field_type == "float":
                try:
                    result[key] = float(value) if value is not None else field_schema.get("default", 0.0)
                except (ValueError, TypeError):
                    result[key] = field_schema.get("default", 0.0)
            elif field_type == "list":
                if not isinstance(value, list):
                    result[key] = field_schema.get("default", [])
                else:
                    result[key] = value
            else:
                result[key] = str(value) if value is not None else field_schema.get("default", "")
        return result

    # ────────── 插件状态 ──────────

    def get_plugin_status(self) -> list[dict]:
        """获取所有插件状态"""
        status_list = []
        for metadata in star_registry:
            platforms = resolve_platforms(metadata.support_platforms)
            status_list.append({
                "name": metadata.name,
                "author": metadata.author,
                "desc": metadata.desc,
                "version": metadata.version,
                "activated": metadata.activated,
                "reserved": metadata.reserved,
                "root_dir_name": metadata.root_dir_name,
                "display_name": metadata.display_name or metadata.name,
                "plugin_id": metadata.plugin_id,
                "support_platforms": metadata.support_platforms,
                "platforms": platforms,
                "platform_tags": _platform_tags(platforms),
            })
        # 添加失败插件
        for dir_name, info in self.failed_plugin_dict.items():
            platforms = resolve_platforms(info.get("support_platforms", []))
            status_list.append({
                "name": info.get("name", dir_name),
                "author": info.get("author", "unknown"),
                "desc": info.get("desc", ""),
                "version": info.get("version", "?"),
                "activated": False,
                "reserved": info.get("reserved", False),
                "root_dir_name": dir_name,
                "display_name": info.get("display_name", dir_name),
                "error": info.get("error", ""),
                "plugin_id": info.get("plugin_id", ""),
                "support_platforms": info.get("support_platforms", []),
                "platforms": platforms,
                "platform_tags": _platform_tags(platforms),
            })
        return status_list

    # ────────── 插件安装（AstrBot 兼容） ──────────

    async def install_plugin(self, source: str) -> dict:
        """安装插件

        支持从 GitHub 仓库 URL 安装：
        - https://github.com/author/repo
        - https://github.com/author/repo/tree/main/plugin_dir
        - author/repo（简写，自动补全 https://github.com/）

        Args:
            source: GitHub 仓库 URL 或简写

        Returns:
            dict: 安装结果
        """
        import re
        import subprocess
        import tempfile

        logger.info(f"安装插件: {source}")
        source = source.strip()

        # 解析 GitHub URL
        if not source.startswith("http"):
            # 简写: author/repo
            source = f"https://github.com/{source}"

        # 提取仓库信息
        github_match = re.match(
            r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/tree/([^/]+)(?:/(.*))?)?$",
            source,
        )
        if not github_match:
            return {"success": False, "message": "不支持的安装来源，仅支持 GitHub 仓库 URL"}

        author = github_match.group(1)
        repo = github_match.group(2).replace(".git", "")
        branch = github_match.group(3) or "main"
        subdir = github_match.group(4) or ""

        # 目标目录
        plugin_dir_name = repo if not subdir else subdir.rstrip("/").split("/")[-1]
        target_dir = os.path.join(self.plugin_store_path, plugin_dir_name)

        if os.path.exists(target_dir):
            return {"success": False, "message": f"插件目录已存在: {plugin_dir_name}"}

        try:
            # 优先使用 HTTP zip 下载（配置了加速地址则走代理，否则直连，绕过 git 直连限制）
            ok, msg = await self._install_plugin_via_zip(
                author, repo, branch, subdir, target_dir
            )
            if ok:
                success, failed = await self.load(plugin_dir_name)
                if success:
                    return {"success": True, "message": f"插件 {plugin_dir_name} 安装成功"}
                error_msg = ""
                if failed and plugin_dir_name in failed:
                    error_msg = failed[plugin_dir_name].get("error", "未知错误")
                return {
                    "success": True,
                    "message": f"插件安装完成，但加载失败: {error_msg}",
                    "load_error": error_msg,
                }
            logger.warning(f"通过 zip 安装失败，回退 git clone: {msg}")

            # 克隆仓库到临时目录
            clone_url = f"https://github.com/{author}/{repo}.git"
            with tempfile.TemporaryDirectory() as tmpdir:
                logger.info(f"克隆仓库: {clone_url} (branch: {branch})")
                import subprocess
                result = subprocess.run(
                    ["git", "clone", "--depth", "1", "-b", branch, clone_url, tmpdir],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode != 0:
                    return {"success": False, "message": f"克隆失败: {result.stderr[:200]}"}

                # 如果指定了子目录，从子目录复制
                source_dir = os.path.join(tmpdir, subdir) if subdir else tmpdir
                if not os.path.exists(source_dir):
                    return {"success": False, "message": f"子目录不存在: {subdir}"}

                # 复制插件目录
                shutil.copytree(source_dir, target_dir)
                logger.info(f"插件目录已创建: {target_dir}")

            # 加载新插件
            success, failed = await self.load(plugin_dir_name)
            if success:
                return {"success": True, "message": f"插件 {plugin_dir_name} 安装成功"}
            else:
                error_msg = ""
                if failed and plugin_dir_name in failed:
                    error_msg = failed[plugin_dir_name].get("error", "未知错误")
                return {
                    "success": True,
                    "message": f"插件安装完成，但加载失败: {error_msg}",
                    "load_error": error_msg,
                }

        except subprocess.TimeoutExpired:
            return {"success": False, "message": "克隆超时（60秒）"}
        except Exception as e:
            logger.error(f"安装插件失败: {e}", exc_info=True)
            # 清理残留目录
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
            return {"success": False, "message": f"安装失败: {str(e)}"}

    async def _install_plugin_via_zip(
        self, author: str, repo: str, branch: str, subdir: str, target_dir: str
    ) -> tuple:
        """通过 GitHub zip 包下载并解压安装插件（流式写入，避免大包占内存）

        下载地址按顺序尝试：
        1. 配置了 GitHub 加速地址时先走代理
        2. 直连 GitHub archive
        任一成功即解压复制到 target_dir。

        Returns:
            (True, msg) 成功 / (False, msg) 失败原因
        """
        import httpx
        import tempfile

        from zhiyan_web.github_proxy.routers import get_proxy_base

        proxy_base = get_proxy_base()
        zip_url = f"https://github.com/{author}/{repo}/archive/refs/heads/{branch}.zip"
        candidate_urls = [f"{proxy_base}/{zip_url}"] if proxy_base else []
        candidate_urls.append(zip_url)

        last_err = ""
        timeout = httpx.Timeout(180.0, connect=30.0)
        for i, fetch_url in enumerate(candidate_urls):
            label = "代理" if (i == 0 and proxy_base) else "直连"
            try:
                logger.info(f"通过{label}下载插件 zip: {fetch_url}")
                async with httpx.AsyncClient(timeout=timeout, verify=False, follow_redirects=True) as client:
                    async with client.stream("GET", fetch_url) as resp:
                        if resp.status_code != 200:
                            last_err = f"下载失败 HTTP {resp.status_code}"
                            continue
                        with tempfile.TemporaryDirectory() as tmpdir:
                            zip_path = os.path.join(tmpdir, "plugin.zip")
                            with open(zip_path, "wb") as f:
                                async for chunk in resp.aiter_bytes():
                                    f.write(chunk)
                            extract_dir = os.path.join(tmpdir, "extract")
                            os.makedirs(extract_dir, exist_ok=True)
                            with zipfile.ZipFile(zip_path, "r") as zf:
                                zf.extractall(extract_dir)
                            # zip 内通常有一层根目录 <repo>-<branch>
                            entries = os.listdir(extract_dir)
                            if not entries:
                                last_err = "zip 内容为空"
                                continue
                            root = os.path.join(extract_dir, entries[0])
                            source_dir = (
                                os.path.join(root, subdir)
                                if (os.path.isdir(root) and subdir)
                                else root
                            )
                            if not os.path.exists(source_dir):
                                last_err = f"子目录不存在: {subdir or '/'}"
                                continue
                            os.makedirs(target_dir, exist_ok=True)
                            for name in os.listdir(source_dir):
                                s = os.path.join(source_dir, name)
                                d = os.path.join(target_dir, name)
                                if os.path.isdir(s):
                                    shutil.copytree(s, d, dirs_exist_ok=True)
                                else:
                                    shutil.copy2(s, d)
                logger.info(f"插件目录已通过 zip 创建: {target_dir}")
                return True, "ok"
            except Exception as e:
                last_err = str(e)
                logger.warning(f"{label} zip 下载失败({fetch_url}): {e}")
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)
        return False, last_err or "下载失败"

    async def uninstall_plugin(self, plugin_name: str) -> dict:
        """卸载插件
        Args:
            plugin_name: 插件名称（root_dir_name）

        Returns:
            dict: 卸载结果
        """
        # 查找插件
        metadata = None
        for m in star_registry:
            if m.name == plugin_name or m.root_dir_name == plugin_name:
                metadata = m
                break

        if not metadata:
            return {"success": False, "message": f"插件 {plugin_name} 未找到"}

        if metadata.reserved:
            return {"success": False, "message": "不能卸载内置插件"}

        # 先卸载
        await self.unload(plugin_name)

        # 删除插件目录
        root_dir = metadata.root_dir_name or plugin_name
        plugin_dir = os.path.join(self.plugin_store_path, root_dir)
        if os.path.exists(plugin_dir):
            shutil.rmtree(plugin_dir, ignore_errors=True)
            logger.info(f"插件目录已删除: {plugin_dir}")

        # 删除插件配置
        config_path = os.path.join(self.plugin_config_path, f"{root_dir}_config.json")
        if os.path.exists(config_path):
            os.remove(config_path)

        # 从失败列表移除
        self.failed_plugin_dict.pop(root_dir, None)

        return {"success": True, "message": f"插件 {plugin_name} 已卸载"}

    def get_plugin_detail(self, plugin_name: str) -> dict | None:
        """获取插件详情（完整元数据 + 配置模式）

        Args:
            plugin_name: 插件名称

        Returns:
            dict: 插件详情，未找到返回 None
        """
        metadata = self.context.get_registered_star(plugin_name)
        if not metadata:
            return None

        # 加载配置模式（_conf_schema.json）
        conf_schema = None
        root_dir = metadata.root_dir_name or plugin_name
        schema_path = os.path.join(
            self.reserved_plugin_path if metadata.reserved else self.plugin_store_path,
            root_dir,
            self.conf_schema_fname,
        )
        if os.path.exists(schema_path):
            try:
                with open(schema_path, "r", encoding="utf-8") as f:
                    conf_schema = json.load(f)
            except Exception as e:
                logger.debug(f"加载配置模式失败: {e}")

        # 获取插件目录中的文件列表
        plugin_dir = os.path.join(
            self.reserved_plugin_path if metadata.reserved else self.plugin_store_path,
            root_dir,
        )
        files = []
        if os.path.exists(plugin_dir):
            for fname in os.listdir(plugin_dir):
                if fname.endswith(".py") or fname in ("metadata.yaml", "metadata.json", "plugin.json"):
                    fpath = os.path.join(plugin_dir, fname)
                    files.append({
                        "name": fname,
                        "size": os.path.getsize(fpath),
                    })

        # 获取 handler 信息
        handlers = []
        for handler in star_handlers_registry:
            if handler.handler_module_path == metadata.module_path:
                handler_info = {
                    "name": handler.handler_name,
                    "event_type": handler.event_type.name,
                    "desc": handler.desc,
                    "enabled": handler.enabled,
                }
                # 检查过滤器
                for f in handler.event_filters:
                    if hasattr(f, "command_name"):
                        handler_info["command"] = f.command_name
                        handler_info["aliases"] = list(f.alias) if f.alias else []
                    elif hasattr(f, "regex"):
                        handler_info["regex"] = f.regex.pattern if hasattr(f.regex, "pattern") else str(f.regex)
                handlers.append(handler_info)

        return {
            "name": metadata.name,
            "author": metadata.author,
            "desc": metadata.desc,
            "short_desc": metadata.short_desc,
            "version": metadata.version,
            "repo": metadata.repo,
            "display_name": metadata.display_name or metadata.name,
            "plugin_id": metadata.plugin_id,
            "activated": metadata.activated,
            "reserved": metadata.reserved,
            "root_dir_name": metadata.root_dir_name,
            "config": metadata.config,
            "conf_schema": conf_schema,
            "files": files,
            "handlers": handlers,
            "support_platforms": metadata.support_platforms,
            "platforms": resolve_platforms(metadata.support_platforms),
            "astrbot_version": metadata.astrbot_version,
            "pages": metadata.pages,
        }

    # ────────── 插件依赖管理（自动安装 requirements / _deps.json） ──────────

    def _get_plugin_dir(self, plugin_name: str):
        """根据插件名解析其磁盘目录，找不到返回 None"""
        metadata = self.context.get_registered_star(plugin_name)
        if not metadata:
            return None, None, None
        root_dir = metadata.root_dir_name or plugin_name
        plugin_dir = os.path.join(
            self.reserved_plugin_path if metadata.reserved else self.plugin_store_path,
            root_dir,
        )
        return plugin_dir, root_dir, metadata.reserved

    def get_plugin_dependencies(self, plugin_name: str) -> dict:
        """查询插件依赖状态（已安装/缺失/进行中）"""
        from .dependency_installer import get_dependency_installer
        plugin_dir, root_dir, _reserved = self._get_plugin_dir(plugin_name)
        if not plugin_dir or not os.path.isdir(plugin_dir):
            return {"error": f"插件 {plugin_name} 目录不存在"}
        return get_dependency_installer().get_dependency_status(plugin_dir)

    async def install_plugin_dependencies(self, plugin_name: str) -> dict:
        """手动安装插件缺失依赖（await 直至完成）"""
        from .dependency_installer import get_dependency_installer
        plugin_dir, root_dir, _reserved = self._get_plugin_dir(plugin_name)
        if not plugin_dir or not os.path.isdir(plugin_dir):
            return {"success": False, "message": f"插件 {plugin_name} 目录不存在"}
        result = await get_dependency_installer().install_missing(plugin_name, plugin_dir)
        return {
            "success": result["status"] != "failed",
            "message": result.get("message", ""),
            "status": result["status"],
            "installed_count": result.get("installed_count", 0),
            "detail": result.get("detail", []),
        }