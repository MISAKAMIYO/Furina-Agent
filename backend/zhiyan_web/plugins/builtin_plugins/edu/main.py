"""
Edu 插件 - AI 教辅（题库 / 查题 / 解题 / 课件导入 / 组卷 / 错题本 / 推题）

命令:
- /题库                     查看题库统计
- /查题 <关键词>            在题库中检索题目
- /解题 <题目内容>          让 AI 给出解题步骤与讲解
- /导入课件 <本地文件路径>   解析课件/作业并入库
- /组卷 [题量]              随机组卷（默认 10 题）
- /错题本 [学科]            查看错题本统计与待复习错题
- /推题 [题量]              按薄弱知识点推题
- /复习 <错题ID> 对|错       错了复习打卡

说明: 题库与教辅逻辑复用 zhiyan_web.edu（bank / solver），与网页端同一份数据。
"""
from __future__ import annotations

import logging

from zhiyan_web.plugins import PluginBase

logger = logging.getLogger("ZhiYanWeb.EduPlugin")

_QTYPE_LABEL = {"choice": "选择题", "fill": "填空题", "judge": "判断题", "answer": "解答题"}

# QQ 单条消息容易过长，讲解输出做长度保护
_MAX_REPLY_CHARS = 900


class EduPlugin(PluginBase):
    """AI 教辅插件"""

    author = "ZhiYan"
    name = "edu"

    async def initialize(self):
        self.register_command("题库", desc="查看题库统计")
        self.register_command("查题", desc="在题库中检索题目")
        self.register_command("解题", desc="AI 解题并给出讲解")
        self.register_command("导入课件", desc="解析课件/作业并入库")
        self.register_command("组卷", desc="按题量随机组卷")
        self.register_command("错题本", desc="查看错题本统计与待复习错题")
        self.register_command("推题", desc="按薄弱知识点推题")
        self.register_command("复习", desc="错题复习打卡")
        logger.info("EduPlugin initialized")

    async def terminate(self):
        logger.info("EduPlugin terminated")

    async def on_message(self, message: dict):
        content = (message.get("content") or "").strip()
        if not content.startswith("/"):
            return None

        body = content[1:].strip()
        if body in ("题库", "题库统计"):
            return self._stats()
        if body.startswith("查题"):
            return self._search(body[2:].strip())
        if body.startswith("解题"):
            return await self._solve(body[2:].strip())
        if body.startswith("导入课件"):
            return await self._import(body[4:].strip())
        if body.startswith("组卷"):
            return self._exam(body[2:].strip())
        if body.startswith("错题本"):
            return self._mistakes(body[3:].strip())
        if body.startswith("推题"):
            return await self._recommend(body[2:].strip())
        if body.startswith("复习"):
            return self._review(body[2:].strip())
        return None

    # ---- 各命令实现 ----

    def _stats(self) -> str:
        from zhiyan_web.edu import bank

        data = bank.stats()
        lines = [
            "教辅题库总览",
            f"题目 {data['question_total']} 道（含答案 {data['with_answer']} 道）",
            f"课件 {data['course_total']} 份，知识点 {data['knowledge_points']} 个",
        ]
        subjects = data.get("by_subject") or []
        if subjects:
            lines.append("学科分布：" + "、".join(f"{s['name']} {s['count']}" for s in subjects[:8]))
        types = data.get("by_type") or []
        if types:
            lines.append("题型分布：" + "、".join(
                f"{_QTYPE_LABEL.get(t['name'], t['name'])} {t['count']}" for t in types
            ))
        lines.append("用法：/查题 关键词、/解题 题目内容、/导入课件 本地文件路径")
        return "\n".join(lines)

    def _search(self, keyword: str) -> str:
        from zhiyan_web.edu import bank

        if not keyword:
            return "用法：/查题 关键词（例如 /查题 一元二次方程）"
        result = bank.list_questions(keyword=keyword, limit=5)
        items = result.get("items") or []
        if not items:
            return f"题库中没有匹配「{keyword}」的题目。可先 /导入课件 导入资料。"
        lines = [f"匹配「{keyword}」共 {result['total']} 道，显示前 {len(items)} 道："]
        for idx, q in enumerate(items, 1):
            stem = str(q.get("stem") or "").replace("\n", " ")
            if len(stem) > 60:
                stem = stem[:60] + "..."
            lines.append(
                f"{idx}. [{_QTYPE_LABEL.get(q.get('qtype'), '题目')}|难度{q.get('difficulty')}] {stem}"
                + (f"（答案：{q['answer']}）" if q.get("answer") else "")
            )
        lines.append("想看讲解：/解题 " + str(items[0].get("stem", ""))[:40])
        return "\n".join(lines)

    async def _solve(self, text: str) -> str:
        from zhiyan_web.edu.solver import solve_question

        if not text:
            return "用法：/解题 题目内容（可直接把题目粘在后面）"
        result = await solve_question(text=text)
        if not result.get("success"):
            return f"解题失败：{result.get('message') or '未知错误'}"

        lines = []
        if result.get("answer"):
            lines.append(f"答案：{result['answer']}")
        steps = result.get("steps") or []
        if steps:
            lines.append("步骤：")
            lines.extend(f"{i}. {s}" for i, s in enumerate(steps, 1))
        if result.get("analysis"):
            lines.append(f"讲解：{result['analysis']}")
        if result.get("mistakes"):
            lines.append(f"易错点：{result['mistakes']}")
        reply = "\n".join(lines) or "模型没有返回可用内容"
        return self._clip(reply)

    async def _import(self, path: str) -> str:
        from zhiyan_web.edu import bank

        if not path:
            return "用法：/导入课件 <本地文件路径>（支持 pdf/pptx/docx/xlsx/txt/md）"
        path = path.strip().strip('"')
        result = await bank.import_document(path, max_questions=30, source="qq")
        if not result.get("success"):
            return f"导入失败：{result.get('message') or '未知错误'}"
        return (
            f"{result.get('message')}\n"
            f"课件 ID {result.get('course_id')}，可到网页端「AI 教辅」查看与编辑。"
        )

    # ---- 第二期：组卷 / 错题本 / 推题 / 复习 ----

    def _exam(self, arg: str) -> str:
        from zhiyan_web.edu import exam

        count = 10
        digits = "".join(ch for ch in arg if ch.isdigit())
        if digits:
            count = max(1, min(int(digits), 50))
        result = exam.generate_exam({"question_count": count, "save": True})
        if not result.get("success"):
            return f"组卷失败：{result.get('message') or '未知错误'}"
        paper = result.get("paper") or {}
        lines = [str(result.get("message") or "组卷完成")]
        if paper.get("notice"):
            lines.append(str(paper["notice"]))
        lines.append("题目预览：")
        for q in (paper.get("questions") or [])[:5]:
            stem = str(q.get("stem") or "").replace("\n", " ")
            if len(stem) > 50:
                stem = stem[:50] + "..."
            lines.append(f"{q.get('order')}. [{_QTYPE_LABEL.get(q.get('qtype'), '题目')}] {stem}")
        if paper.get("id"):
            lines.append(f"试卷已存入网页端「AI 教辅 → 组卷」（#{paper['id']}），可导出打印。")
        return self._clip("\n".join(lines))

    def _mistakes(self, arg: str) -> str:
        from zhiyan_web.edu import mistake

        subject = arg.strip()
        data = mistake.stats()
        lines = [
            "错题本总览" + (f"（{subject}）" if subject else ""),
            f"共 {data.get('mistake_total', 0)} 道：待复习 {data.get('open_total', 0)}、"
            f"复习中 {data.get('reviewing_total', 0)}、已掌握 {data.get('mastered_total', 0)}"
            f"（掌握率 {data.get('mastery_rate', 0)}%）",
        ]
        weak = data.get("weak_points") or []
        if weak:
            lines.append("薄弱知识点：" + "、".join(f"{w['name']}({w['count']})" for w in weak[:6]))
        pending = mistake.list_mistakes(subject=subject, status="open", limit=5).get("items") or []
        if pending:
            lines.append("待复习错题：")
            for m in pending:
                stem = str(m.get("stem") or "").replace("\n", " ")
                if len(stem) > 40:
                    stem = stem[:40] + "..."
                lines.append(f"#{m.get('id')} {stem}")
            lines.append(f"复习打卡：/复习 <错题ID> 对 或 /复习 <错题ID> 错")
        else:
            lines.append("暂时没有待复习的错题。可在网页端把题目「记错题」。")
        return self._clip("\n".join(lines))

    async def _recommend(self, arg: str) -> str:
        from zhiyan_web.edu import recommend

        count = 5
        digits = "".join(ch for ch in arg if ch.isdigit())
        if digits:
            count = max(1, min(int(digits), 10))
        result = await recommend.build_practice(limit=count)
        items = result.get("items") or []
        if not items:
            return "题库中暂时没有可推的新题，先 /导入课件 导入资料，或把错题记进错题本。"

        lines = []
        if result.get("summary"):
            lines.append(f"学情：{result['summary']}")
        for a in (result.get("advice") or [])[:3]:
            lines.append(f"- {a}")
        lines.append(f"针对性练习（{len(items)} 题）：")
        for idx, q in enumerate(items, 1):
            stem = str(q.get("stem") or "").replace("\n", " ")
            if len(stem) > 50:
                stem = stem[:50] + "..."
            lines.append(f"{idx}. [{_QTYPE_LABEL.get(q.get('qtype'), '题目')}|难度{q.get('difficulty')}] {stem}")
        lines.append("看讲解：/解题 <题目内容>（在网页端「AI 教辅」可直接点讲解）")
        return self._clip("\n".join(lines))

    def _review(self, arg: str) -> str:
        from zhiyan_web.edu import mistake

        parts = arg.replace("，", " ").replace(",", " ").split()
        if not parts:
            return "用法：/复习 <错题ID> 对|错（例如 /复习 3 对）"
        digits = "".join(ch for ch in parts[0] if ch.isdigit())
        if not digits:
            return "用法：/复习 <错题ID> 对|错（例如 /复习 3 对）"
        mistake_id = int(digits)
        flag = parts[1] if len(parts) > 1 else "对"
        result = "wrong" if flag.strip().lower() in ("错", "wrong", "不对", "错题") else "correct"
        res = mistake.review_mistake(mistake_id, result=result, note="QQ 打卡")
        if not res.get("success"):
            return f"复习打卡失败：{res.get('message') or '未知错误'}"
        data = res.get("data") or {}
        return (
            f"错题 #{mistake_id} 已打卡（{'做对' if result == 'correct' else '做错'}）\n"
            f"{res.get('message')}\n"
            f"当前状态：{data.get('status_label') or ''}，复习 {data.get('review_count', 0)} 次，"
            f"连对 {data.get('correct_streak', 0)} 次。"
        )

    @staticmethod
    def _clip(text: str) -> str:
        if len(text) <= _MAX_REPLY_CHARS:
            return text
        return text[:_MAX_REPLY_CHARS] + "\n...(内容较长，完整讲解请到网页端「AI 教辅」查看)"