//! ZhiYan Agent 桌面外壳 —— 阶段 1：命令行入口 / 端口登记 / 拉起后端 / 探活
//!
//! 职责边界（见 docs/ARCHITECTURE.md 阶段 0）：
//! Rust 只做"进程 / 窗口 / 系统集成"，业务全部留在 Python 后端。
//!
//! 阶段 1 目标：
//! 1. 命令行参数 ——debug / --port / --help（对齐 desktop.py 行为）
//! 2. 后端端口分配：优先端口段 8001-8020 内取空闲，段满回退系统随机
//! 3. 端口登记：写 <项目根>/logs/runtime.json（schema_version=1）
//! 4. 以子进程拉起 python backend/main.py，通过环境变量 ZHIYAN_PORT 下传端口
//! 5. TCP 探活轮询等待后端就绪（0.3s→2s 退避，60s 超时，对齐 desktop.py）
//! 6. 就绪后打印访问地址并保持存活；退出时同步 runtime.json 并回收子进程
//!
//! 阶段 1 仅 CLI 形态：Python 子进程直接继承控制台输出，Ctrl+C 可传递给后端
//! （CREATE_NO_WINDOW 与日志重定向留到阶段 3 窗口形态再做）。

use std::env;
use std::fs;
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::Serialize;

// ============ 常量 ============

/// 后端端口段（架构文档约定：Python 8001-8020，Node 2536-2540）
const BACKEND_PORT_START: u16 = 8001;
const BACKEND_PORT_END: u16 = 8020;
/// 探活总超时（秒）
const READY_TIMEOUT_SECS: u64 = 60;
/// 初始轮询间隔 / 最大轮询间隔（秒），对齐 desktop.py._wait_for_server
const POLL_INIT_SECS: f64 = 0.3;
const POLL_MAX_SECS: f64 = 2.0;

// ============ runtime.json 结构（schema_version=1） ============

#[derive(Serialize, Clone)]
struct RuntimeInfo {
    schema_version: u32,
    updated_at: String,
    processes: RuntimeProcesses,
}

#[derive(Serialize, Clone)]
struct RuntimeProcesses {
    backend: BackendProcess,
}

#[derive(Serialize, Clone)]
struct BackendProcess {
    pid: u32,
    port: u16,
    status: String, // "starting" | "running" | "failed"
    started_at: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

// ============ 时间工具（ISO8601 UTC，不引入 chrono） ============

/// 当前时间格式化为 ISO8601 UTC，如 2026-09-13T08:00:00Z
fn iso8601_now() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    let days = secs.div_euclid(86_400);
    let secs_of_day = secs.rem_euclid(86_400);
    let h = secs_of_day / 3_600;
    let m = (secs_of_day % 3_600) / 60;
    let s = secs_of_day % 60;
    let (y, mo, d) = civil_from_days(days);
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z", y, mo, d, h, m, s)
}

/// days（自 1970-01-01）→ (年, 月, 日)，算法参考 Howard Hinnant 的 civil_from_days
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m as u32, d as u32)
}

// ============ 端口分配 ============

/// 按端口段约定分配：优先 8001-8020 中第一个空闲端口；段满回退系统随机端口
fn find_free_port() -> u16 {
    for port in BACKEND_PORT_START..=BACKEND_PORT_END {
        if port_is_free(port) {
            return port;
        }
    }
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .unwrap_or(BACKEND_PORT_START)
}

fn port_is_free(port: u16) -> bool {
    match TcpListener::bind(("127.0.0.1", port)) {
        Ok(listener) => {
            drop(listener);
            true
        }
        Err(_) => false,
    }
}

// ============ runtime.json 登记 ============

/// runtime.json 路径：<项目根>/logs/runtime.json
fn runtime_path(project_root: &Path) -> PathBuf {
    project_root.join("logs").join("runtime.json")
}

/// 写入运行时信息（先写临时文件再改名，避免读取方看到半截 JSON）
fn write_runtime(project_root: &Path, backend: &BackendProcess) -> Result<(), String> {
    let info = RuntimeInfo {
        schema_version: 1,
        updated_at: iso8601_now(),
        processes: RuntimeProcesses {
            backend: backend.clone(),
        },
    };
    let json = serde_json::to_string_pretty(&info).map_err(|e| e.to_string())?;
    let path = runtime_path(project_root);
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| format!("创建目录失败: {e}"))?;
    }
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, json).map_err(|e| format!("写入临时文件失败: {e}"))?;
    fs::rename(&tmp, &path).map_err(|e| format!("落盘失败: {e}"))?;
    Ok(())
}

// ============ 后端定位与拉起 ============

/// 定位 Python 后端入口 backend/main.py，及其所属项目根。
/// 从可执行文件所在目录向上最多追溯 3 级，覆盖开发（target/debug）与产物两种布局。
/// 返回 (main.py 绝对路径, 项目根)
fn locate_backend() -> Option<(PathBuf, PathBuf)> {
    let exe_dir = env::current_exe().ok()?.parent()?.to_path_buf();
    let mut base = Some(exe_dir.as_path());
    for _ in 0..4 {
        let dir = base?;
        let main_py = dir.join("backend").join("main.py");
        if main_py.exists() {
            return Some((main_py, dir.to_path_buf()));
        }
        base = dir.parent();
    }
    None
}

/// 以子进程拉起 Python 后端；工作目录设为项目根，端口经 ZHIYAN_PORT 下传。
/// 解释器优先使用 `python`，启动失败时回退到 Windows Python Launcher `py`
/// （某些环境 python 未进 PATH 但 py 可用）。
/// 阶段 1 CLI 形态：继承标准输出/错误，便于查看后端日志且 Ctrl+C 可传递。
fn spawn_backend(main_py: &Path, project_root: &Path, port: u16) -> std::io::Result<Child> {
    let mut last_err: Option<std::io::Error> = None;
    for interpreter in &["python", "py"] {
        let result = Command::new(interpreter)
            .arg(main_py)
            .env("ZHIYAN_PORT", port.to_string())
            .current_dir(project_root)
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn();
        match result {
            Ok(child) => return Ok(child),
            Err(e) => {
                eprintln!("[ZhiYan Shell] 解释器 {interpreter} 启动失败，尝试下一个: {e}");
                last_err = Some(e);
            }
        }
    }
    Err(last_err.unwrap_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "未找到可用的 Python 解释器")
    }))
}

// ============ 探活 ============

/// TCP 连接 127.0.0.1:port，能连上即认为就绪
fn probe_port(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&addr, Duration::from_secs(1)).is_ok()
}

/// 轮询等待后端就绪（对齐 desktop.py：0.3s→2s 退避，60s 超时）
/// 子进程提前退出视为失败
fn wait_ready(port: u16, child: &mut Child) -> bool {
    let start = Instant::now();
    let mut interval = POLL_INIT_SECS;
    while start.elapsed().as_secs() < READY_TIMEOUT_SECS {
        if let Ok(Some(status)) = child.try_wait() {
            eprintln!("[ZhiYan Shell] 后端进程提前退出，退出码: {status}");
            return false;
        }
        if probe_port(port) {
            eprintln!("[ZhiYan Shell] 后端就绪 (耗时 {:.1}s)", start.elapsed().as_secs_f64());
            return true;
        }
        std::thread::sleep(Duration::from_millis((interval * 1000.0) as u64));
        interval = (interval * 1.2).min(POLL_MAX_SECS);
    }
    false
}

// ============ 命令行入口 ============

fn print_help() {
    println!(
        "ZhiYan Agent Rust 外壳 (zhiyan-agent v0.1.0)\n\
         \n\
         用法: zhiyan-agent [选项]\n\
         \n\
         选项:\n\
         \x20 --debug            开启调试模式（更详细的日志输出）\n\
         \x20 --port <端口>      指定后端端口（默认在 8001-8020 端口段内自动选择）\n\
         \x20 --help             显示本帮助\n\
         \n\
         说明: 启动后自动拉起 python backend/main.py，端口登记到 logs/runtime.json，\n\
         TCP 探活就绪后打印后端地址。按 Ctrl+C 退出（后端进程一并结束）。"
    );
}

/// 解析命令行参数：返回 (调试模式, 指定端口)
fn parse_args() -> (bool, Option<u16>) {
    let args: Vec<String> = env::args().skip(1).collect();
    let mut debug = false;
    let mut port = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--help" | "-h" => {
                print_help();
                std::process::exit(0);
            }
            "--debug" | "-d" => debug = true,
            "--port" | "-p" => {
                i += 1;
                match args.get(i).and_then(|v| v.trim().parse::<u16>().ok()) {
                    Some(p) => port = Some(p),
                    None => {
                        eprintln!("错误: --port 需要一个 1-65535 的端口号");
                        std::process::exit(2);
                    }
                }
            }
            other => {
                eprintln!("未知参数: {other}");
                print_help();
                std::process::exit(2);
            }
        }
        i += 1;
    }
    (debug, port)
}

fn main() {
    let (debug, port_opt) = parse_args();

    // 1. 定位后端入口与项目根
    let Some((main_py, project_root)) = locate_backend() else {
        eprintln!("错误: 未找到 backend/main.py（请确认可执行文件与 backend 目录的相对位置）");
        std::process::exit(1);
    };
    eprintln!("[ZhiYan Shell] 项目根: {}", project_root.display());
    eprintln!("[ZhiYan Shell] 后端入口: {}", main_py.display());

    // 2. 分配端口（优先端口段约定）
    let port = match port_opt {
        Some(p) => p,
        None => find_free_port(),
    };
    let started_at = iso8601_now();
    let runtime_path = runtime_path(&project_root);
    if debug {
        eprintln!("[ZhiYan Shell] 调试模式已开启，端口: {port}");
    } else {
        eprintln!("[ZhiYan Shell] 后端端口: {port}");
    }

    // 3. 拉起 Python 后端
    let mut child = match spawn_backend(&main_py, &project_root, port) {
        Ok(c) => c,
        Err(e) => {
            let failed = BackendProcess {
                pid: 0,
                port,
                status: "failed".to_string(),
                started_at,
                error: Some(format!("启动 Python 后端失败: {e}")),
            };
            let _ = write_runtime(&project_root, &failed);
            eprintln!("错误: 启动 Python 后端失败: {e}");
            std::process::exit(1);
        }
    };

    // 4. 登记 runtime.json（starting）
    let base = BackendProcess {
        pid: child.id(),
        port,
        status: "starting".to_string(),
        started_at: started_at.clone(),
        error: None,
    };
    if let Err(e) = write_runtime(&project_root, &base) {
        eprintln!("警告: 写入运行时信息失败: {e}");
    }

    // 5. TCP 探活等待后端就绪
    let running = if wait_ready(port, &mut child) {
        let info = BackendProcess {
            status: "running".to_string(),
            error: None,
            ..base
        };
        if let Err(e) = write_runtime(&project_root, &info) {
            eprintln!("警告: 写入运行时信息失败: {e}");
        }
        info
    } else {
        // 探活失败：清理子进程并登记失败状态
        let _ = child.kill();
        let _ = child.wait();
        let failed = BackendProcess {
            status: "failed".to_string(),
            error: Some("后端启动超时或进程提前退出".to_string()),
            ..base
        };
        let _ = write_runtime(&project_root, &failed);
        eprintln!(
            "错误: 后端在 {} 秒内未就绪，已清理子进程",
            READY_TIMEOUT_SECS
        );
        std::process::exit(1);
    };

    // 6. 就绪：打印访问信息，外壳保持存活直到后端退出
    println!("==============================================");
    println!("ZhiYan Agent Rust 外壳已就绪");
    println!("后端服务地址: http://127.0.0.1:{}", running.port);
    println!("运行时信息: {}", runtime_path.display());
    println!("按 Ctrl+C 退出（后端进程将一并结束）");
    println!("==============================================");

    let _ = child.wait();
    // 后端已退出：同步 runtime.json 状态
    let exited = BackendProcess {
        status: "failed".to_string(),
        error: Some("后端进程已退出".to_string()),
        ..running
    };
    let _ = write_runtime(&project_root, &exited);
    eprintln!("[ZhiYan Shell] 后端已退出，外壳结束");
}