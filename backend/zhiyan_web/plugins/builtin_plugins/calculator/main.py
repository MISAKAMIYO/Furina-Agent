"""
Calculator 插件 - 提供数学计算命令
"""

from __future__ import annotations

import math
import re

from zhiyan_web.plugins import PluginBase


class CalculatorPlugin(PluginBase):
    """Calculator 插件"""

    author = "ZhiYan"
    name = "calculator"

    async def initialize(self):
        """插件初始化"""
        print("CalculatorPlugin initialized")
        self.register_command("calc", desc="计算数学表达式")
        self.register_command("fib", desc="计算斐波那契数")
        self.register_command("prime", desc="判断质数")

    async def terminate(self):
        """插件卸载"""
        print("CalculatorPlugin terminated")

    async def on_message(self, message: dict) -> str | None:
        """处理消息"""
        content = message.get("content", "")

        if content.startswith("/calc"):
            expr = content.replace("/calc", "").strip()
            return self._calc(expr)
        elif content.startswith("/fib"):
            msg = content.replace("/fib", "").strip()
            return self._fib(msg)
        elif content.startswith("/prime"):
            msg = content.replace("/prime", "").strip()
            return self._prime(msg)
        return None

    def _calc(self, expr: str) -> str:
        """计算数学表达式"""
        if not expr:
            return "用法: /calc <表达式>\n例如: /calc 1+2*3\n支持: + - * / % ^ sqrt sin cos tan log"

        safe_expr = re.sub(r'[^0-9+\-*/%.^()a-zA-Z\s]', '', expr)

        allowed_funcs = {
            'sqrt': math.sqrt,
            'sin': math.sin,
            'cos': math.cos,
            'tan': math.tan,
            'log': math.log,
            'log10': math.log10,
            'abs': abs,
            'round': round,
            'pi': math.pi,
            'e': math.e,
            'pow': pow
        }

        try:
            result = eval(safe_expr, {"__builtins__": None}, allowed_funcs)
            return f"计算结果:\n{expr} = {result}"
        except ZeroDivisionError:
            return "错误: 除数不能为零"
        except SyntaxError:
            return f"错误: 表达式语法错误\n{expr}"
        except Exception as e:
            return f"计算错误: {str(e)}"

    def _fib(self, msg: str) -> str:
        """计算斐波那契数"""
        if not msg.isdigit():
            return "用法: /fib <数字>\n例如: /fib 10\n输出第10个斐波那契数"

        n = int(msg)
        if n <= 0:
            return "请输入正整数"

        a, b = 0, 1
        for _ in range(n - 1):
            a, b = b, a + b

        return f"第{n}个斐波那契数: {b}"

    def _prime(self, msg: str) -> str:
        """判断质数"""
        if not msg.isdigit():
            return "用法: /prime <数字>\n例如: /prime 17\n判断是否为质数"

        n = int(msg)
        if n <= 1:
            return f"{n} 不是质数"

        for i in range(2, int(math.sqrt(n)) + 1):
            if n % i == 0:
                return f"{n} 不是质数，能被 {i} 整除"

        return f"{n} 是质数"
