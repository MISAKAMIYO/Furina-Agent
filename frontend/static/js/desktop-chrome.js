/**
 * Furina Agent 桌面窗体
 *
 * 无系统边框窗口的顶栏与窗口控制：
 * - 仅顶栏可拖动窗口（挂件、画布等页面元素拖动不会带走窗口）
 * - Windows 走原生 WM_NCLBUTTONDOWN 拖动，其它平台回退到增量移动
 * - 桌面模式下压平网页版"窗口套窗口"的外框，隐藏页面内旧标题栏
 *
 * 触发方式：pywebview 环境自动挂载；浏览器带 ?desktop=true 可预览。
 */

(function () {
    'use strict';

    var MOUNTED = false;

    function getApi() {
        return (window.pywebview && window.pywebview.api) || null;
    }

    function isDesktopEnv() {
        if (window.pywebview && window.pywebview.api) return true;
        return /[?&]desktop=true/.test(window.location.search);
    }

    var STYLE = [
        ':root { --dtb-h: 46px; }',
        'body.desktop-mode { padding-top: var(--dtb-h) !important; box-sizing: border-box; }',
        'body.desktop-mode .desk { padding: 0 !important; background: var(--canvas); }',
        'body.desktop-mode .win { width: 100%; height: 100%; max-width: none; max-height: none; border-radius: 0; box-shadow: none; }',
        'body.desktop-mode .win > .titlebar { display: none !important; }',
        'body.desktop-mode .widget, body.desktop-mode .widget-layer, body.desktop-mode .widget-dock, body.desktop-mode .widget-bar { -webkit-app-region: no-drag; }',
        '',
        '#custom-titlebar {',
        '  position: fixed; top: 0; left: 0; right: 0; height: var(--dtb-h);',
        '  display: flex; align-items: center; gap: 10px; padding: 0 8px 0 14px;',
        '  box-sizing: border-box; z-index: 99999; user-select: none;',
        "  font-family: 'Segoe UI', 'Microsoft YaHei', -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif;",
        '  background: linear-gradient(180deg, rgba(255,255,255,0.9) 0%, rgba(246,249,253,0.82) 100%);',
        '  backdrop-filter: saturate(180%) blur(18px);',
        '  -webkit-backdrop-filter: saturate(180%) blur(18px);',
        '  border-bottom: 1px solid rgba(24,40,72,0.07);',
        '}',
        'html.dark #custom-titlebar { background: linear-gradient(180deg, rgba(22,27,42,0.94) 0%, rgba(16,20,32,0.9) 100%); border-bottom-color: rgba(255,255,255,0.07); }',
        '.dtb-brand { display: flex; align-items: center; gap: 9px; flex: none; }',
        '.dtb-mark { width: 24px; height: 24px; border-radius: 8px; display: inline-flex; align-items: center; justify-content: center; color: #fff; background: linear-gradient(135deg, #2F7FD6 0%, #7FB2F0 100%); box-shadow: 0 3px 10px rgba(47,127,214,0.32); }',
        '.dtb-mark svg { width: 15px; height: 15px; }',
        '.dtb-name { font-size: 13px; font-weight: 600; letter-spacing: 0.2px; color: var(--ink, #1D2637); }',
        '.dtb-tag { font-size: 10.5px; line-height: 1; padding: 3px 7px; border-radius: 999px; color: var(--ink-3, #7A879C); background: rgba(47,127,214,0.08); border: 1px solid rgba(47,127,214,0.14); }',
        'html.dark .dtb-tag { color: #C3D2EA; background: rgba(127,178,240,0.12); border-color: rgba(127,178,240,0.24); }',
        '.dtb-drag { flex: 1; align-self: stretch; cursor: default; }',
        '.dtb-right { display: flex; align-items: center; gap: 3px; flex: none; }',
        '.dtb-status { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 10px; border-radius: 999px; font-size: 11px; color: #16a34a; background: rgba(34,197,94,0.08); border: 1px solid rgba(34,197,94,0.18); }',
        'html.dark .dtb-status { color: #6EE7A8; }',
        '.dtb-dot { width: 6px; height: 6px; border-radius: 50%; background: #22c55e; animation: dtbPulse 2.4s ease-in-out infinite; }',
        '@keyframes dtbPulse { 0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.45); } 70% { box-shadow: 0 0 0 5px rgba(34,197,94,0); } 100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); } }',
        '.dtb-sep { width: 1px; height: 18px; margin: 0 5px; background: linear-gradient(180deg, transparent, rgba(24,40,72,0.16), transparent); }',
        'html.dark .dtb-sep { background: linear-gradient(180deg, transparent, rgba(255,255,255,0.16), transparent); }',
        '.dtb-btn { width: 34px; height: 30px; display: inline-flex; align-items: center; justify-content: center; padding: 0; border: none; border-radius: 8px; background: transparent; color: var(--ink-3, #7A879C); cursor: pointer; transition: background 0.15s ease, color 0.15s ease, transform 0.08s ease; }',
        '.dtb-btn:hover { background: rgba(24,40,72,0.07); color: var(--ink, #1D2637); }',
        'html.dark .dtb-btn:hover { background: rgba(255,255,255,0.09); color: #E8EEF9; }',
        '.dtb-btn:active { transform: scale(0.93); }',
        '.dtb-btn svg { width: 14px; height: 14px; stroke: currentColor; stroke-width: 1.5; fill: none; stroke-linecap: round; stroke-linejoin: round; }',
        '.dtb-btn.close:hover { background: #E5484D; color: #fff; }',
        '.custom-context-menu { position: fixed; display: none; padding: 4px; min-width: 130px; z-index: 100000; font-size: 13px; color: #1D2637; background: rgba(255,255,255,0.96); border: 1px solid rgba(15,23,42,0.08); border-radius: 10px; box-shadow: 0 10px 34px rgba(15,23,42,0.16); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); }',
        '.custom-context-menu.show { display: block; }',
        'html.dark .custom-context-menu { background: rgba(26,32,48,0.96); border-color: rgba(255,255,255,0.1); color: #E8EEF9; }',
        '.custom-context-menu-item { display: flex; align-items: center; gap: 10px; padding: 7px 12px; border-radius: 7px; cursor: pointer; }',
        '.custom-context-menu-item:hover { background: rgba(47,127,214,0.1); color: #2F7FD6; }',
        '.custom-context-menu-divider { height: 1px; margin: 4px 8px; background: rgba(15,23,42,0.08); }',
        '@media (max-width: 780px) { .dtb-tag { display: none; } .dtb-status span:last-child { display: none; } .dtb-status { padding: 0 7px; } }'
    ].join('\n');

    var MARKUP = [
        '<div class="dtb-brand pywebview-drag-region">',
        '  <span class="dtb-mark">',
        '    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 13.5c2.1-3.2 4.2-3.2 6.3 0s4.2 3.2 6.3 0 4.2-3.2 6.4 0"/><path d="M2.5 17.5c2.1-3.2 4.2-3.2 6.3 0s4.2 3.2 6.3 0 4.2-3.2 6.4 0" opacity=".55"/></svg>',
        '  </span>',
        '  <span class="dtb-name">Furina Agent</span>',
        '  <span class="dtb-tag">桌面版</span>',
        '</div>',
        '<div class="dtb-drag pywebview-drag-region" id="dtb-drag"></div>',
        '<div class="dtb-right">',
        '  <span class="dtb-status"><span class="dtb-dot"></span><span>运行中</span></span>',
        '  <span class="dtb-sep"></span>',
        '  <button type="button" class="dtb-btn" data-act="min" title="最小化" aria-label="最小化"><svg viewBox="0 0 16 16"><path d="M3.5 8h9"/></svg></button>',
        '  <button type="button" class="dtb-btn" data-act="max" title="最大化" aria-label="最大化">',
        '    <svg class="i-max" viewBox="0 0 16 16"><rect x="3.4" y="3.4" width="9.2" height="9.2" rx="1.6"/></svg>',
        '    <svg class="i-restore" viewBox="0 0 16 16" style="display:none"><rect x="3" y="5.2" width="7.8" height="7.8" rx="1.4"/><path d="M5.6 5.2V3.8A1.1 1.1 0 0 1 6.7 2.7h6.4a1.1 1.1 0 0 1 1.1 1.1v6.4a1.1 1.1 0 0 1-1.1 1.1h-1.3"/></svg>',
        '  </button>',
        '  <button type="button" class="dtb-btn close" data-act="close" title="关闭" aria-label="关闭"><svg viewBox="0 0 16 16"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></svg></button>',
        '</div>'
    ].join('');

    function bindContextMenu() {
        var menu = null;
        function hide() {
            if (menu) menu.classList.remove('show');
        }
        function copy(text) {
            var api = getApi();
            if (api && api.copy_to_clipboard) {
                api.copy_to_clipboard(text);
                return;
            }
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.style.cssText = 'position:fixed;opacity:0;';
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand('copy'); } catch (e) { /* 忽略 */ }
            document.body.removeChild(ta);
        }
        document.addEventListener('contextmenu', function (e) {
            var sel = window.getSelection();
            var text = sel ? sel.toString() : '';
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') { hide(); return; }
            if (!text.trim()) { hide(); return; }
            e.preventDefault();
            if (!menu) {
                menu = document.createElement('div');
                menu.className = 'custom-context-menu';
                menu.innerHTML = '<div class="custom-context-menu-item" data-act="copy">复制</div>' +
                    '<div class="custom-context-menu-divider"></div>' +
                    '<div class="custom-context-menu-item" data-act="selectAll">全选</div>';
                document.body.appendChild(menu);
                menu.addEventListener('click', function (ev) {
                    var item = ev.target.closest('.custom-context-menu-item');
                    if (!item) return;
                    if (item.dataset.act === 'copy') copy(String(window.getSelection()));
                    else document.execCommand('selectAll');
                    hide();
                });
            }
            var mw = menu.offsetWidth || 120;
            var mh = menu.offsetHeight || 72;
            menu.style.left = Math.min(e.clientX, window.innerWidth - mw - 8) + 'px';
            menu.style.top = Math.min(e.clientY, window.innerHeight - mh - 8) + 'px';
            menu.classList.add('show');
        });
        document.addEventListener('click', function (e) {
            if (menu && !menu.contains(e.target)) hide();
        });
        document.addEventListener('scroll', hide, true);
    }

    function mount() {
        if (MOUNTED || document.getElementById('custom-titlebar')) return;
        MOUNTED = true;

        document.body.classList.add('desktop-mode');

        var styleEl = document.createElement('style');
        styleEl.id = 'desktop-chrome-style';
        styleEl.textContent = STYLE;
        document.head.appendChild(styleEl);

        var maximized = false;

        var titlebar = document.createElement('div');
        titlebar.id = 'custom-titlebar';
        titlebar.innerHTML = MARKUP;
        document.body.insertBefore(titlebar, document.body.firstChild);

        function setMaxState(v) {
            maximized = !!v;
            var btn = titlebar.querySelector('[data-act="max"]');
            if (!btn) return;
            var maxIcon = btn.querySelector('.i-max');
            var restoreIcon = btn.querySelector('.i-restore');
            if (maxIcon) maxIcon.style.display = maximized ? 'none' : '';
            if (restoreIcon) restoreIcon.style.display = maximized ? '' : 'none';
            btn.title = maximized ? '还原' : '最大化';
            btn.setAttribute('aria-label', btn.title);
        }

        window.toggleMaximize = function () {
            var api = getApi();
            if (!api) return;
            var next = !maximized;
            setMaxState(next);
            try {
                if (next) {
                    if (api.maximize_window) api.maximize_window();
                } else if (api.restore_window) {
                    api.restore_window();
                } else if (api.maximize_window) {
                    api.maximize_window();
                }
            } catch (e) {
                setMaxState(!next);
            }
        };

        titlebar.addEventListener('click', function (e) {
            var btn = e.target.closest('.dtb-btn');
            if (!btn) return;
            var api = getApi();
            if (!api) return;
            var act = btn.dataset.act;
            if (act === 'min') { if (api.minimize_window) api.minimize_window(); }
            else if (act === 'max') { window.toggleMaximize(); }
            else if (act === 'close') { if (api.close_window) api.close_window(); }
        });

        // 窗口拖动交给 pywebview 的拖拽区域机制：只有带 pywebview-drag-region
        // 类名的标题栏区域能带动窗口，页面内挂件等元素的拖动不受影响。
        titlebar.addEventListener('dblclick', function (e) {
            if (e.target.closest('.dtb-right')) return;
            window.toggleMaximize();
        });

        bindContextMenu();
        console.log('[DesktopChrome] 桌面窗体已挂载');
    }

    window.mountDesktopChrome = mount;

    if (isDesktopEnv()) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', mount);
        } else {
            mount();
        }
    } else {
        window.addEventListener('pywebviewready', function () { mount(); });
    }
})();
