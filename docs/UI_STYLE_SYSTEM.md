# UI 样式系统（LOCKED）

自 2026-11-13 起，前端统一使用聊天页确立的视觉语言：**芙宁娜主题配色 + DeepSeek Harness 式结构与控件**。
本文件是唯一视觉规范；新增页面或改版时必须复用这里的类与令牌，禁止再为单个页面另写一套按钮/输入框/卡片样式。

## 1. 令牌

全局令牌定义在 `static/css/style.css` 末尾「全站统一设计系统（LOCKED）」段：

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--ui-radius-xs` | 8px | 小徽标、小按钮 |
| `--ui-radius-sm` | 10px | 按钮、输入框 |
| `--ui-radius-md` | 12px | 紧凑卡片、复选框卡 |
| `--ui-radius-lg` | 16px | 卡片、面板、菜单 |
| `--ui-control-h` | 34px | 标准控件高度 |
| `--ui-control-h-sm` | 30px | 小按钮/标签高度 |
| `--ui-page-header-h` | 52px | 页面头部最小高度 |
| `--ui-card-bg` | `--glass-bg-light` | 卡片底色 |
| `--ui-card-border` | `--glass-border-light` | 卡片描边 |
| `--ui-card-shadow` | `--shadow-1` | 卡片阴影 |
| `--ui-control-bg` | `--surface-2` | 输入框底色 |

颜色一律使用语义变量：`--ink / --ink-2 / --ink-3`、`--surface / --surface-2`、`--accent / --accent-hover / --accent-press / --accent-tint / --accent-ring`、`--hairline`、`--danger / --success / --warning`。浅色为水蓝，`html.dark` 下自动切换为深蓝 + 金，不要写死颜色。

聊天页专属令牌位于 `#chat-view`（`--chat-*`），只服务聊天页，不要在新页面里使用。

## 2. 页面骨架

```html
<div id="xxx-view" class="view">
  <div class="view-header">
    <h2><i class="fas fa-xxx"></i> 页面标题</h2>
    <div class="view-actions">
      <button class="btn btn-sm">操作</button>
      <button class="btn btn-sm" onclick="switchView('chat')">返回</button>
    </div>
  </div>

  <div class="settings-section">
    <div class="settings-section-title"><i class="fas fa-xxx"></i> 区块标题</div>
    ...内容...
  </div>
</div>
```

- `.view-header`：最小高 52px、左右 24px、底部分隔线，标题 15px/600，图标用主色。
- 页面内容统一包一层带内边距的容器（24px）或直接使用 `.settings-section`；无内层容器的页面已由 `#bettergi-view` 等表单统一处理。
- 长页面必须可滚动（`.view` 加 `overflow-y:auto` 或内层容器滚动），头部可吸顶。

## 3. 组件

**卡片/面板**
`.settings-section`、`.edu-panel`、`.edu-card`、`.ext-cat-panel`、`.ext-market-card`、`.plugins-container`、`.skills-container`、`.converter-panel`、`.agent-section`、`.filter-bar`、`.parameter-card`、`.tts-param-group`、`.proactive-card`、`.skill-stat-card`、`.collapse-panel`
→ 玻璃底 + 1px 描边 + 16px 圆角 + `--shadow-1`。

**区块标题**
`.settings-section-title`、`.settings-section h3`、`.edu-panel-title`
→ 14px/600、主色图标、底部 hairline、下边距 12px。

**按钮**
- 标准：`btn`（34px 高、10px 圆角、surface 底、hairline 描边）
- 主操作：`btn btn-primary`（主色底 + `--fill-ink` 文字）
- 次操作：`btn btn-secondary`
- 小号：`btn btn-sm`（30px 高、9px 圆角）
- 图标：`btn-icon`（30px 方形）

**表单**
`.form-group` + `input / select / textarea`，或直接 `.input-lg / .select-lg / .textarea-lg`
→ 34px 高、10px 圆角、`--surface-2` 底、hairline 描边；聚焦主色描边 + 2px `--accent-ring` 光环。
提示文字用 `.form-tips`、`.checkbox-desc`、`.edu-hint`（12px `--ink-3`）。

**标签/分类**
`.edu-tab`、`.plugins-filter-btn`、`.ext-cat-btn`、`.tts-emotion-quick-btn`
→ 30px 高胶囊；激活态为主色淡底 + 主色描边 + 主色文字，不使用整块实色。

**其它**
- 表格：`table.tbl`（th 11.5px `--ink-3`，td hairline 分隔，行 hover `--surface-2`）
- 空状态：`.empty-state`
- 弹窗：`.modal-content / .modal-header / .modal-body / .modal-footer`
- 滚动条：全局统一 8px，浅色蓝、深色金，禁止单页另写。

## 4. 禁止事项

- 不要写死 `#fff` / `white` / `#000` 作为背景或文字色（缩略图、二维码白底等必要场景除外）。
- 不要新增第二套按钮/输入框/卡片类；优先复用现有类。
- 不要为单个页面改全局令牌；需要新语义时先加令牌再复用。
- 不要覆盖聊天页 `#chat-view` 的 `--chat-*` 令牌作用域。
- 改动后必须同时检查浅色与深色两套主题。

## 5. 相关位置

- 统一层：`frontend/static/css/style.css` 末尾「全站统一设计系统（LOCKED）」。
- 聊天页 DSH 结构：同文件「DSH 主页式聊天 UI」段与 `frontend/index.html` 聊天视图。
- 主题切换：`html.dark` + `localStorage.darkMode`（`toggleDarkMode()`）。
