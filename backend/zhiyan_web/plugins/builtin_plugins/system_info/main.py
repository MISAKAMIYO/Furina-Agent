"""
System Info 插件 - 提供系统信息查询命令
"""

from __future__ import annotations

import os
import platform
import sys

from zhiyan_web.plugins import PluginBase


class SystemInfoPlugin(PluginBase):
    """System Info 插件"""

    author = "ZhiYan"
    name = "system_info"

    async def initialize(self):
        """插件初始化"""
        print("SystemInfoPlugin initialized")
        self.register_command("sysinfo", desc="显示系统信息")
        self.register_command("cpu", desc="显示 CPU 信息")
        self.register_command("memory", desc="显示内存信息")
        self.register_command("disk", desc="显示磁盘信息")

    async def terminate(self):
        """插件卸载"""
        print("SystemInfoPlugin terminated")

    async def on_message(self, message: dict) -> str | None:
        """处理消息"""
        content = message.get("content", "")

        if content.startswith("/sysinfo"):
            return self._sysinfo()
        elif content.startswith("/cpu"):
            return self._cpu()
        elif content.startswith("/memory"):
            return self._memory()
        elif content.startswith("/disk"):
            return self._disk()
        return None

    def _sysinfo(self) -> str:
        """显示系统信息"""
        info = [
            "=== 系统信息 ===",
            f"操作系统: {platform.system()} {platform.release()}",
            f"版本: {platform.version()}",
            f"架构: {platform.machine()}",
            f"Python: {sys.version.split()[0]}",
            f"CPU: {platform.processor()}",
        ]

        # CPU 核心数
        try:
            cpu_count = os.cpu_count()
            info.append(f"CPU 核心数: {cpu_count}")
        except Exception:
            pass

        # 内存信息
        try:
            import psutil
            memory = psutil.virtual_memory()
            info.append(f"内存: {memory.used / 1024 / 1024 / 1024:.2f} GB / {memory.total / 1024 / 1024 / 1024:.2f} GB")
            info.append(f"内存使用率: {memory.percent}%")
        except ImportError:
            info.append("内存信息: 需要安装 psutil 模块")
        except Exception:
            pass

        # 磁盘信息
        try:
            import psutil
            disk = psutil.disk_usage('/')
            info.append(f"磁盘: {disk.used / 1024 / 1024 / 1024:.2f} GB / {disk.total / 1024 / 1024 / 1024:.2f} GB")
            info.append(f"磁盘使用率: {disk.percent}%")
        except ImportError:
            info.append("磁盘信息: 需要安装 psutil 模块")
        except Exception:
            pass

        # CPU 使用率
        try:
            import psutil
            cpu_percent = psutil.cpu_percent()
            info.append(f"CPU 使用率: {cpu_percent}%")
        except ImportError:
            pass
        except Exception:
            pass

        return "\n".join(info)

    def _cpu(self) -> str:
        """显示 CPU 信息"""
        try:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=1)
            cores = psutil.cpu_count(logical=True)
            per_core = psutil.cpu_percent(interval=1, percpu=True)
            per_core_str = ", ".join(f"核{i+1}:{p}%" for i, p in enumerate(per_core))
            return f"CPU 使用率: {cpu_percent}%\n核心数: {cores}\n各核心: {per_core_str}"
        except ImportError:
            return "错误: 需要安装 psutil 模块"
        except Exception as e:
            return f"获取 CPU 信息失败: {str(e)}"

    def _memory(self) -> str:
        """显示内存信息"""
        try:
            import psutil
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            return (
                f"物理内存: {mem.used / 1024 / 1024 / 1024:.2f} GB / {mem.total / 1024 / 1024 / 1024:.2f} GB ({mem.percent}%)\n"
                f"可用内存: {mem.available / 1024 / 1024 / 1024:.2f} GB\n"
                f"交换空间: {swap.used / 1024 / 1024 / 1024:.2f} GB / {swap.total / 1024 / 1024 / 1024:.2f} GB ({swap.percent}%)"
            )
        except ImportError:
            return "错误: 需要安装 psutil 模块"
        except Exception as e:
            return f"获取内存信息失败: {str(e)}"

    def _disk(self) -> str:
        """显示磁盘信息"""
        try:
            import psutil
            partitions = psutil.disk_partitions()
            result = []
            for p in partitions:
                try:
                    usage = psutil.disk_usage(p.mountpoint)
                    result.append(
                        f"{p.device} ({p.mountpoint}):\n"
                        f"  已用: {usage.used / 1024 / 1024 / 1024:.2f} GB\n"
                        f"  总计: {usage.total / 1024 / 1024 / 1024:.2f} GB\n"
                        f"  使用率: {usage.percent}%"
                    )
                except Exception:
                    pass
            return "\n".join(result) if result else "无法获取磁盘信息"
        except ImportError:
            return "错误: 需要安装 psutil 模块"
        except Exception as e:
            return f"获取磁盘信息失败: {str(e)}"
