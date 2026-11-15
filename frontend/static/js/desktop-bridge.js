/**
 * Furina Agent Desktop Bridge
 * 
 * 提供 Web 前端与 Python 桌面端之间的双向通信桥接。
 * 在桌面模式下运行时自动加载，浏览器模式下安全降级。
 */

(function() {
    'use strict';

    const DESKTOP_BRIDGE = {
        // 是否运行在桌面模式
        isDesktop: false,
        // 桥接是否就绪
        ready: false,
        // 待执行的回调队列（桥接就绪前缓存）
        _pendingCallbacks: [],
        // 原生 API 缓存
        _nativeApi: null,
    };

    // ============= 检测桌面环境 =============

    function detectDesktop() {
        // pywebview 注入的标记
        if (window.pywebview && window.pywebview.api) {
            DESKTOP_BRIDGE.isDesktop = true;
            DESKTOP_BRIDGE._nativeApi = window.pywebview.api;
            DESKTOP_BRIDGE.ready = true;
            return true;
        }
        
        // 通过 URL 参数检测（调试用）
        const params = new URLSearchParams(window.location.search);
        if (params.get('desktop') === 'true') {
            DESKTOP_BRIDGE.isDesktop = true;
            return true;
        }
        
        return false;
    }

    // ============= 桥接 API =============

    /**
     * 调用原生 Python 方法
     * @param {string} method - 方法名
     * @param {...any} args - 参数
     * @returns {Promise<any>}
     */
    async function callNative(method, ...args) {
        if (!DESKTOP_BRIDGE.isDesktop || !window.pywebview) {
            console.warn(`[DesktopBridge] 非桌面模式，无法调用: ${method}`);
            return null;
        }
        
        try {
            const api = window.pywebview.api;
            if (api && typeof api[method] === 'function') {
                return await api[method](...args);
            }
            console.warn(`[DesktopBridge] 原生方法不存在: ${method}`);
            return null;
        } catch (err) {
            console.error(`[DesktopBridge] 调用失败: ${method}`, err);
            return null;
        }
    }

    // ============= 系统通知 =============

    function showNotification(title, message) {
        if (DESKTOP_BRIDGE.isDesktop) {
            callNative('show_notification', title, message);
        } else if ('Notification' in window && Notification.permission === 'granted') {
            new Notification(title, { body: message });
        }
    }

    // ============= 文件操作 =============

    async function openFileDialog(fileTypes) {
        return await callNative('open_file_dialog', fileTypes);
    }

    async function saveFileDialog(content, filename, fileTypes) {
        return await callNative('save_file_dialog', content, filename, fileTypes);
    }

    // ============= 剪贴板 =============

    async function copyToClipboard(text) {
        if (DESKTOP_BRIDGE.isDesktop) {
            return await callNative('copy_to_clipboard', text);
        }
        // 浏览器回退
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch {
            return false;
        }
    }

    async function pasteFromClipboard() {
        if (DESKTOP_BRIDGE.isDesktop) {
            return await callNative('paste_from_clipboard');
        }
        try {
            return await navigator.clipboard.readText();
        } catch {
            return '';
        }
    }

    // ============= 窗口控制 =============

    function minimizeWindow() {
        callNative('minimize_window');
    }

    function maximizeWindow() {
        callNative('maximize_window');
    }

    function closeWindow() {
        callNative('close_window');
    }

    function toggleAlwaysOnTop() {
        callNative('toggle_always_on_top');
    }

    // ============= AI 授课：悬浮球小窗 =============

    function openFloatBall(url, width, height) {
        return callNative('open_float_ball', url || '', width || 0, height || 0);
    }

    function closeFloatBall() {
        return callNative('close_float_ball');
    }

    function hideFloatBall() {
        return callNative('hide_float_ball');
    }

    function ballMove(x, y) {
        return callNative('ball_move', x, y);
    }

    function ballGetPos() {
        return callNative('ball_get_pos');
    }

    function ballSetSize(width, height) {
        return callNative('ball_set_size', width, height);
    }

    function ballScreenSize() {
        return callNative('ball_screen_size');
    }

    function openExternal(url) {
        return callNative('open_external', url);
    }

    // ============= 系统信息 =============

    async function getSystemInfo() {
        return await callNative('get_system_info');
    }

    // ============= 桌面模式 UI 增强 =============

    function applyDesktopUI() {
        if (!DESKTOP_BRIDGE.isDesktop) return;
        
        // 添加桌面模式标记
        document.body.classList.add('desktop-mode');
        
        // 在标题栏添加桌面控制按钮
        const header = document.querySelector('.header');
        if (header) {
            const desktopControls = document.createElement('div');
            desktopControls.className = 'desktop-controls';
            desktopControls.innerHTML = `
                <button class="desktop-btn desktop-btn-min" onclick="DESKTOP_BRIDGE.minimizeWindow()" title="最小化">
                    <i class="fas fa-window-minimize"></i>
                </button>
                <button class="desktop-btn desktop-btn-max" onclick="DESKTOP_BRIDGE.maximizeWindow()" title="最大化">
                    <i class="fas fa-window-maximize"></i>
                </button>
                <button class="desktop-btn desktop-btn-close" onclick="DESKTOP_BRIDGE.closeWindow()" title="关闭">
                    <i class="fas fa-times"></i>
                </button>
            `;
            header.prepend(desktopControls);
        }
        
        // 添加桌面模式指示器
        const statusBar = document.querySelector('.header-actions');
        if (statusBar) {
            const badge = document.createElement('span');
            badge.className = 'desktop-badge';
            badge.innerHTML = '<i class="fas fa-desktop"></i> 桌面版';
            statusBar.prepend(badge);
        }
    }

    // ============= 初始化 =============

    function init() {
        // pywebview 的 window.pywebview 通常在文档创建时注入，但为稳妥起见，
        // 监听 pywebviewready 事件重新检测一次（悬浮球等独立小窗同样受益）。
        if (!detectDesktop()) {
            window.addEventListener('pywebviewready', function () {
                if (detectDesktop()) {
                    console.log('[DesktopBridge] pywebviewready 后检测到桌面模式');
                    applyDesktopUI();
                }
            });
        }

        if (DESKTOP_BRIDGE.isDesktop) {
            console.log('[DesktopBridge] 桌面模式已激活');
            document.addEventListener('DOMContentLoaded', applyDesktopUI);
        } else {
            console.log('[DesktopBridge] 浏览器模式（桌面桥接未激活）');
        }
    }

    // ============= 导出 =============

    window.DESKTOP_BRIDGE = DESKTOP_BRIDGE;
    window.DesktopBridge = {
        callNative,
        showNotification,
        openFileDialog,
        saveFileDialog,
        copyToClipboard,
        pasteFromClipboard,
        minimizeWindow,
        maximizeWindow,
        closeWindow,
        toggleAlwaysOnTop,
        openFloatBall,
        closeFloatBall,
        hideFloatBall,
        ballMove,
        ballGetPos,
        ballSetSize,
        ballScreenSize,
        openExternal,
        getSystemInfo,
        isDesktop: () => DESKTOP_BRIDGE.isDesktop,
        isReady: () => DESKTOP_BRIDGE.ready,
    };

    // 自动初始化
    init();

})();