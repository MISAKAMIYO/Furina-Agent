"""预设（模拟）模式下的 Agent 演示任务。

用途：在不调用任何 LLM、不消耗 token、不触碰真实文件的前提下，把一条完整的
Agent 任务按真实 SSE 事件序列「演」一遍，方便检查对话过程区的样式与交互：

    思考 → 读取 → 搜索 → 编辑 → 运行命令（失败）→ 重试成功 → 越权写入（触发审批）→ 总结

事件类型与真实 Agent 完全一致（reasoning / tool_call / tool_result /
approval_request / approval_result / interim / chunk / done），审批走的是真的
approval_broker，所以「允许一次 / 拒绝」是真的会影响这段演示的走向。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncIterator, Dict

logger = logging.getLogger("ZhiYanWeb.DemoAgent")

# 节奏：像真实模型一样一小段一小段往外吐
REASON_STEP = 0.035     # 每块思考文字之间的间隔
TOOL_WAIT = 0.55        # 工具执行耗时
CHUNK_STEP = 0.012


def _demo_paths() -> Dict[str, str]:
    """演示用的假项目路径（跟随当前工作区，越界路径固定在工作区外）。"""
    try:
        from zhiyan_web.computer.services.workspace_manager import get_workspace_dir
        workspace = get_workspace_dir() or os.getcwd()
    except Exception:
        workspace = os.getcwd()
    project = os.path.join(workspace, "demo-shop")
    return {
        "workspace": workspace,
        "project": project,
        "package": os.path.join(project, "package.json"),
        "build": os.path.join(project, "scripts", "build.mjs"),
        # 故意放到工作区外，用来触发「等待审批」
        "outside": r"C:\Users\Public\Documents\furina-demo\release-notes.md",
    }


async def _reasoning(text: str) -> AsyncIterator[Dict]:
    """按小块推思考内容，模拟真实流式 reasoning。"""
    for i in range(0, len(text), 8):
        yield {"type": "reasoning", "content": text[i:i + 8]}
        await asyncio.sleep(REASON_STEP)


async def _tool(name: str, args: Dict, result: str, success: bool = True,
                wait: float = TOOL_WAIT) -> AsyncIterator[Dict]:
    """推一次工具调用 + 结果。"""
    yield {"type": "tool_call", "name": name, "args": args}
    await asyncio.sleep(wait)
    yield {
        "type": "tool_result",
        "name": name,
        "success": success,
        "preview": result,
        "result": result,
    }


async def run_demo_trace(conv_id: str = "") -> AsyncIterator[Dict]:
    """播放一整条演示任务，最后 yield done（content 为总结正文）。"""
    from . import approval_broker

    p = _demo_paths()
    logger.info(f"播放预设模式演示任务: {p['project']}")

    # 1. 先想一下要干什么
    async for ev in _reasoning(
        "工作区里有个 demo-shop 项目，先看它的结构和依赖，确认改动点。"
        "\n重点确认 package.json 里的版本号，以及构建脚本的入口。"
    ):
        yield ev

    # 2. 读取 package.json
    async for ev in _tool(
        "read_file",
        {"path": p["package"]},
        json.dumps({
            "path": p["package"],
            "size": 412,
            "lines": 19,
            "content": "{\n  \"name\": \"demo-shop\",\n  \"version\": \"1.4.0\",\n  \"scripts\": {\"build\": \"node scripts/build.mjs\"}\n}",
        }, ensure_ascii=False),
    ):
        yield ev

    # 3. 搜索版本引用
    async for ev in _tool(
        "search_files",
        {"path": p["project"], "query": "1.4.0"},
        "找到 3 处引用：package.json:3、scripts/build.mjs:12、README.md:8",
    ):
        yield ev

    # 4. 再想一步
    async for ev in _reasoning(
        "版本号只写在 package.json 里，构建脚本引用的是变量。"
        "\n把版本号升到 1.5.0，然后跑一次构建确认没有回归。"
    ):
        yield ev

    # 5. 编辑 package.json
    async for ev in _tool(
        "edit_file",
        {
            "path": p["package"],
            "old_text": '\"version\": \"1.4.0\"',
            "new_text": '\"version\": \"1.5.0\"',
        },
        json.dumps({"success": True, "path": p["package"], "replaced": 1}, ensure_ascii=False),
    ):
        yield ev

    # 6. 第一次构建失败（用于检查失败行的红色与退出码徽标）
    async for ev in _tool(
        "command_prompt_execute",
        {"command": "node scripts/build.mjs", "description": "构建并检查产物", "timeout": 120},
        "Error: Cannot find module 'esbuild'\n退出码 1",
        success=False,
        wait=0.9,
    ):
        yield ev

    # 7. 分析失败原因
    async for ev in _reasoning(
        "构建脚本依赖 esbuild，但 devDependencies 里没有。先装依赖再重跑，不要改构建脚本本身。"
    ):
        yield ev

    # 8. 装依赖
    async for ev in _tool(
        "command_prompt_execute",
        {"command": "npm i -D esbuild", "description": "安装缺失的构建依赖", "timeout": 180},
        json.dumps({"status": "completed", "command": "npm i -D esbuild",
                    "output": "added 2 packages in 3.1s"}, ensure_ascii=False),
        wait=0.9,
    ):
        yield ev

    # 9. 重新构建成功
    async for ev in _tool(
        "command_prompt_execute",
        {"command": "node scripts/build.mjs", "description": "重新构建并检查产物", "timeout": 120},
        json.dumps({"status": "completed", "command": "node scripts/build.mjs",
                    "output": "build ok · dist/index.js 12.4 KB"}, ensure_ascii=False),
        wait=0.8,
    ):
        yield ev

    # 10. 越权写入 → 真审批（走 approval_broker，允许/拒绝真的会影响后续）
    write_args = {
        "path": p["outside"],
        "content": "# demo-shop 1.5.0\n\n- 升级版本号\n- 补齐构建依赖\n",
    }
    yield {"type": "tool_call", "name": "write_file", "args": write_args}
    await asyncio.sleep(0.3)

    need = approval_broker.evaluate("write_file", write_args)
    permission = (need or {}).get("permission", "danger-full-access")
    detail = json.dumps(write_args, ensure_ascii=False)
    detail += "\n本次放行只作用于这一次操作，不会改变全局安全模式。"
    req = approval_broker.open_request(
        conv_id, "write_file", write_args,
        permission=permission,
        target=write_args["path"],
        detail=detail,
        reason=(need or {}).get("reason", "演示任务：目标路径在工作区之外"),
    )
    yield {"type": "approval_request", **req}

    decision = await approval_broker.wait(req["id"])
    yield {"type": "approval_result", "id": req["id"], "decision": decision}
    if decision == "allow":
        yield {
            "type": "tool_result",
            "name": "write_file",
            "success": True,
            "preview": f"已写入 {write_args['path']}（演示：未真实写盘）",
            "result": json.dumps({"success": True, "path": write_args["path"]}, ensure_ascii=False),
        }
    else:
        yield {
            "type": "tool_result",
            "name": "write_file",
            "success": False,
            "preview": "用户拒绝了本次操作，发布说明未写入" if decision == "deny"
                       else "等待审批超时，发布说明未写入",
            "result": "用户未放行，操作已取消",
        }

    # 11. 中途小结（检查 interim 行）
    yield {"type": "interim", "text": "构建已通过，产物 dist/index.js 已更新；发布说明待写入。"}
    await asyncio.sleep(0.35)

    # 12. 最终回复
    lines = [
        "演示任务完成（预设模式，未消耗 token，也未真实执行命令 / 写盘）。\n\n",
        "**本次做了这些事：**\n\n",
        f"1. 读取 `{p['package']}`，确认版本号 `1.4.0`\n",
        f"2. 搜索后发现 3 处引用，只需改 `package.json`\n",
        "3. 版本号升级到 `1.5.0`\n",
        "4. 首次构建失败：缺少 `esbuild`（退出码 1）\n",
        "5. 安装依赖后重新构建成功，产物 12.4 KB\n",
        f"6. 发布说明写入 `{p['outside']}`，" +
        ("已获放行\n" if decision == "allow" else "未获放行\n"),
        "\n> 这条轨迹是用来检查工具行、思考行与审批卡样式的，",
        "切换回真实模型后即可正常执行。",
    ]
    for line in lines:
        for i in range(0, len(line), 6):
            yield {"type": "chunk", "text": line[i:i + 6]}
            await asyncio.sleep(CHUNK_STEP)

    yield {"type": "done", "content": "".join(lines)}
