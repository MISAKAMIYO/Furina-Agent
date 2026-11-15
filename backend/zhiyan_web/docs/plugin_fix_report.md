# 插件系统修复报告

## 问题概述

用户报告 web 应用中的大量插件命令出现未注册或冲突的问题。经过深入分析，发现根本原因是插件系统存在**双重注册表**和**类型不共享**的问题。

## 根因分析

### 1. 双重 star_registry 和 star_map 实例
- `astrbot.core.star.star` 有自己的 `star_registry` 和 `star_map`
- `zhiyan_web.star.star` 有自己的 `star_registry` 和 `star_map`
- 使用 `from astrbot.api.star import Star` 的插件注册到 `astrbot.core.star.star` 的注册表
- `PluginManager` 使用 `zhiyan_web.star.star` 的注册表
- **结果**: 插件注册到错误的注册表，PluginManager 无法找到它们

### 2. EventType 枚举不共享
- `astrbot.core.star.star_handler.EventType` 和 `zhiyan_web.star.star_handler.EventType` 是**不同的枚举类**
- Python 枚举通过身份（identity）比较，不同类的相同值不相等
- **结果**: pipeline 的 `get_handlers_by_event_type()` 永远找不到插件注册的 handler

### 3. StarHandlerMetadata 和 StarHandlerRegistry 不共享
- 同理，这两个类在 astrbot 和 zhiyan_web 模块中是不同的类
- 即使 `star_handlers_registry` 实例通过 try/except 共享，其类型仍然不同

### 4. 缺少命令冲突检测
- `_check_command_conflict` 只在 `zhiyan_web.star.register.star_handler` 中
- 使用 `@filter.command` 的插件走 `astrbot.core.star.register.star_handler.register_command` 路径
- **结果**: astrbot 路径的注册没有冲突检测

### 5. PluginManager._get_classes 不兼容
- `_get_classes` 检查 `issubclass(cls_obj, zhiyan_web.star.base.Star)`
- 插件继承自 `astrbot.core.star.base.Star`
- **结果**: PluginManager 找不到插件类

### 6. CommandFilter 缺少功能
- `astrbot.core.star.filter.command.CommandFilter` 支持 `GreedyStr`、`Optional[T]`、自定义过滤器等
- `zhiyan_web.star.filter.command.CommandFilter` 是简化版本，缺少这些功能

### 7. CommandGroupFilter 缺少唤醒前缀剥离
- `astrbot.core.star.filter.command_group.CommandGroupFilter.filter()` 会剥离唤醒前缀
- `zhiyan_web.star.filter.command_group.CommandGroupFilter` 不会

## 修复方案

### 修复 1: 共享 star_registry 和 star_map
- **文件**: `data/astrbot/core/star/star.py`
- **修改**: 添加 try/except 导入，从 `zhiyan_web.star.star` 获取 `star_registry` 和 `star_map`
- **效果**: 两个模块的插件注册到同一个注册表

### 修复 2: 共享 EventType、StarHandlerMetadata、StarHandlerRegistry
- **文件**: `data/astrbot/core/star/star_handler.py`
- **修改**: 用 try/except 包裹整个文件，优先从 `zhiyan_web.star.star_handler` 导入所有核心类型
- **效果**: EventType 枚举比较正确，Handler 类型一致

### 修复 3: 添加缺失的 EventType 枚举值
- **文件**: `data/astrbot/core/star/star_handler.py`（fallback 分支）
- **添加**: `OnAstrBotStartingEvent`、`OnAstrBotStopEvent`、`OnAstrBotReadyEvent`、`OnCommandErrorEvent`、`MessageEvent` 别名

### 修复 4: 添加命令冲突检测
- **文件**: `data/astrbot/core/star/register/star_handler.py`
- **修改**: 添加 `_check_command_conflict` 函数，在 `register_command` 中调用
- **效果**: 所有插件命令注册路径都有冲突检测

### 修复 5: 增强 CommandFilter
- **文件**: `backend/zhiyan_web/star/filter/command.py`
- **修改**: 
  - 添加 `GreedyStr` 类
  - 添加 `custom_filter_ok` 自定义过滤器检查
  - 添加 `add_custom_filter` 方法
  - 添加 `equals` 方法
  - 添加 `validate_and_convert_params` 方法（支持 Optional[T]、Union 类型、布尔值转换等）
  - 添加命令名缓存 `_cmpl_cmd_names`
  - 添加唤醒前缀剥离

### 修复 6: 增强 CommandGroupFilter
- **文件**: `backend/zhiyan_web/star/filter/command_group.py`
- **修改**: 在 `filter()` 方法中添加唤醒前缀剥离

### 修复 7: PluginManager 兼容性
- **文件**: `backend/zhiyan_web/star/star_manager.py`
- **修改**: `_get_classes` 方法同时检查 `zhiyan_web.star.base.Star` 和 `astrbot.core.star.base.Star`
- **效果**: 继承自任一 Star 基类的插件都能被正确识别

## 测试结果

### 核心测试
1. ✅ `star_registry` 和 `star_map` 共享 - 相同对象 id
2. ✅ `EventType` 共享 - 相同对象 id，`AdapterMessageEvent` 比较为 True
3. ✅ `star_handlers_registry` 共享 - 相同对象 id
4. ✅ `StarHandlerMetadata` 共享 - 相同对象 id
5. ✅ `PluginManager._get_classes` 兼容 - 正确识别 astrbot Star 子类

### 功能测试
6. ✅ 通过 astrbot 路径注册的命令被 zhiyan_web 正确找到
7. ✅ CommandFilter 正确匹配带前缀和不带前缀的命令
8. ✅ 命令冲突检测正确触发（同名命令警告）
9. ✅ 三个命令（查番、下一页、上一页）各自独立匹配

## 测试计划

### 单元测试
1. `test_shared_registries`: 验证所有注册表/类型共享
2. `test_command_filter_matching`: 验证 CommandFilter 匹配逻辑
3. `test_command_group_filter_matching`: 验证 CommandGroupFilter 匹配逻辑
4. `test_command_conflict_detection`: 验证冲突检测
5. `test_greedy_str_param`: 验证 GreedyStr 参数处理
6. `test_validate_params`: 验证参数类型转换

### 集成测试
1. `test_plugin_loading`: 加载实际插件，验证注册
2. `test_plugin_command_execution`: 发送命令，验证响应
3. `test_plugin_manager_get_classes`: 验证 PluginManager 能识别所有插件

### 执行方式
```bash
cd d:\Python\NEW_start\FURINA AGENT WEB\backend
python -m pytest tests/ -v
```

## 受影响的文件

| 文件 | 修改类型 |
|------|----------|
| `data/astrbot/core/star/star.py` | 修改 - 共享注册表 |
| `data/astrbot/core/star/star_handler.py` | 重写 - 共享类型 + 添加事件 |
| `data/astrbot/core/star/register/star_handler.py` | 修改 - 添加冲突检测 |
| `backend/zhiyan_web/star/filter/command.py` | 重写 - 增强功能 |
| `backend/zhiyan_web/star/filter/command_group.py` | 修改 - 添加前缀剥离 |
| `backend/zhiyan_web/star/star_manager.py` | 修改 - 双向兼容 |