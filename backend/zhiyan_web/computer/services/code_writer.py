"""
代码编写服务 - 提供 Agent 编写代码的高级能力

功能:
- 创建多文件项目
- 代码编辑与重构
- 代码审查
- 项目模板生成
"""
from __future__ import annotations

import os
import json
import re
import logging
import tempfile
from typing import Dict, List, Optional
from datetime import datetime

from zhiyan_web.config.settings import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.Computer.CodeWriter")

# 项目文件存储目录
PROJECTS_DIR = os.path.join(DATA_DIR, "agent_projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)

# 支持的项目模板
PROJECT_TEMPLATES = {
    "python-script": {
        "description": "Python 脚本项目",
        "files": {
            "main.py": "# {{project_name}}\n# {{description}}\n\ndef main():\n    pass\n\nif __name__ == \"__main__\":\n    main()\n",
            "requirements.txt": "# Dependencies\n",
            "README.md": "# {{project_name}}\n\n{{description}}\n",
        },
    },
    "python-web": {
        "description": "Python Web 应用（FastAPI）",
        "files": {
            "main.py": '"""{{project_name}} - FastAPI Web 应用"""\n\nfrom fastapi import FastAPI\n\napp = FastAPI(title="{{project_name}}", description="{{description}}")\n\n\n@app.get("/")\nasync def root():\n    return {"message": "Hello from {{project_name}}"}\n',
            "requirements.txt": "fastapi\nuvicorn\n",
            "README.md": "# {{project_name}}\n\n{{description}}\n",
        },
    },
    "python-bot": {
        "description": "Python 聊天机器人插件",
        "files": {
            "__init__.py": '"""{{project_name}} - 插件主入口"""\n\nimport logging\n\nlogger = logging.getLogger("{{project_name}}")\n\nasync def on_message(message, context):\n    """处理消息"""\n    return {"reply": f"收到消息: {message}"}\n',
            "metadata.json": '{\n    "name": "{{project_name}}",\n    "version": "1.0.0",\n    "description": "{{description}}",\n    "author": "Furina Agent"\n}\n',
            "README.md": "# {{project_name}}\n\n{{description}}\n",
        },
    },
    "html-page": {
        "description": "HTML 网页项目",
        "files": {
            "index.html": '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n    <meta charset="UTF-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n    <title>{{project_name}}</title>\n    <link rel="stylesheet" href="style.css">\n</head>\n<body>\n    <h1>{{project_name}}</h1>\n    <p>{{description}}</p>\n    <script src="app.js"></script>\n</body>\n</html>\n',
            "style.css": "/* {{project_name}} Styles */\n\n* {\n    margin: 0;\n    padding: 0;\n    box-sizing: border-box;\n}\n\nbody {\n    font-family: -apple-system, BlinkMacSystemFont, sans-serif;\n    padding: 20px;\n}\n",
            "app.js": "// {{project_name}} - Main Script\n\nconsole.log('{{project_name}} loaded');\n",
            "README.md": "# {{project_name}}\n\n{{description}}\n",
        },
    },
}


class CodeWriter:
    """代码编写服务"""

    def __init__(self):
        self._projects = {}  # project_id -> project info
        self._load_projects()

    def _load_projects(self):
        """加载已有项目列表"""
        index_file = os.path.join(PROJECTS_DIR, "index.json")
        if os.path.exists(index_file):
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    self._projects = json.load(f)
            except Exception as e:
                logger.error(f"加载项目索引失败: {e}")

    def _save_projects(self):
        """保存项目列表"""
        index_file = os.path.join(PROJECTS_DIR, "index.json")
        try:
            with open(index_file, "w", encoding="utf-8") as f:
                json.dump(self._projects, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存项目索引失败: {e}")

    def create_project(
        self,
        name: str,
        template: str = "python-script",
        description: str = "",
        files: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """创建项目

        Args:
            name: 项目名称
            template: 项目模板 (python-script / python-web / python-bot / html-page / custom)
            description: 项目描述
            files: 自定义文件列表 (template='custom' 时必须)

        Returns:
            Dict: 项目信息
        """
        import uuid

        project_id = uuid.uuid4().hex[:12]
        project_dir = os.path.join(PROJECTS_DIR, project_id)
        os.makedirs(project_dir, exist_ok=True)

        # 获取模板文件
        if template == "custom" and files:
            template_files = files
        elif template in PROJECT_TEMPLATES:
            template_files = PROJECT_TEMPLATES[template]["files"].copy()
        else:
            raise ValueError(f"未知模板: {template}，可用模板: {', '.join(PROJECT_TEMPLATES.keys())}")

        # 创建文件
        created_files = []
        for filename, content in template_files.items():
            # 替换模板变量
            content = content.replace("{{project_name}}", name)
            content = content.replace("{{description}}", description or "无描述")

            filepath = os.path.join(project_dir, filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            created_files.append(filename)

        # 记录项目信息
        project_info = {
            "id": project_id,
            "name": name,
            "template": template,
            "description": description,
            "path": project_dir,
            "files": created_files,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        self._projects[project_id] = project_info
        self._save_projects()

        return {
            "project_id": project_id,
            "name": name,
            "path": project_dir,
            "files": created_files,
            "message": f"项目 '{name}' 已创建",
        }

    def add_file(self, project_id: str, filename: str, content: str) -> Dict:
        """向项目添加文件"""
        project = self._projects.get(project_id)
        if not project:
            raise ValueError(f"项目不存在: {project_id}")

        filepath = os.path.join(project["path"], filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        if filename not in project["files"]:
            project["files"].append(filename)
        project["updated_at"] = datetime.now().isoformat()
        self._save_projects()

        return {"path": filepath, "message": f"文件已添加: {filename}"}

    def read_file(self, project_id: str, filename: str) -> Dict:
        """读取项目文件"""
        project = self._projects.get(project_id)
        if not project:
            raise ValueError(f"项目不存在: {project_id}")

        filepath = os.path.join(project["path"], filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filename}")

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        return {
            "filename": filename,
            "content": content,
            "size": len(content),
        }

    def edit_file(self, project_id: str, filename: str, content: str) -> Dict:
        """编辑项目文件"""
        project = self._projects.get(project_id)
        if not project:
            raise ValueError(f"项目不存在: {project_id}")

        filepath = os.path.join(project["path"], filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filename}")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        project["updated_at"] = datetime.now().isoformat()
        self._save_projects()

        return {"path": filepath, "message": f"文件已更新: {filename}"}

    def list_projects(self) -> List[Dict]:
        """列出所有项目"""
        return [
            {
                "id": p["id"],
                "name": p["name"],
                "template": p["template"],
                "description": p["description"],
                "files": p["files"],
                "created_at": p["created_at"],
                "updated_at": p["updated_at"],
            }
            for p in self._projects.values()
        ]

    def get_project(self, project_id: str) -> Dict:
        """获取项目详情"""
        project = self._projects.get(project_id)
        if not project:
            raise ValueError(f"项目不存在: {project_id}")
        return dict(project)

    def delete_project(self, project_id: str) -> Dict:
        """删除项目"""
        project = self._projects.get(project_id)
        if not project:
            raise ValueError(f"项目不存在: {project_id}")

        import shutil
        shutil.rmtree(project["path"], ignore_errors=True)
        del self._projects[project_id]
        self._save_projects()

        return {"message": f"项目已删除: {project['name']}"}

    def get_available_templates(self) -> List[Dict]:
        """获取可用模板列表"""
        return [
            {
                "id": tid,
                "name": tid,
                "description": tpl["description"],
                "files": list(tpl["files"].keys()),
            }
            for tid, tpl in PROJECT_TEMPLATES.items()
        ]

    def review_code(self, code: str, language: str = "python") -> Dict:
        """代码审查（静态分析）

        Args:
            code: 代码内容
            language: 编程语言

        Returns:
            Dict: 审查结果
        """
        issues = []
        lines = code.split("\n")

        # 基本检查
        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # 空行太多
            if i > 1 and stripped == "" and lines[i - 2].strip() == "" and lines[i - 3].strip() == "":
                issues.append({"line": i, "severity": "info", "message": "连续空行过多"})

            # 行太长
            if len(line) > 120:
                issues.append({"line": i, "severity": "warning", "message": f"行过长 ({len(line)} 字符, 建议 ≤120)"})

            # 尾随空格
            if line != stripped and stripped:
                issues.append({"line": i, "severity": "info", "message": "行尾有尾随空格"})

            # 硬编码密码
            if any(kw in stripped.lower() for kw in ["password", "secret", "api_key", "token"]):
                if "=" in stripped and not stripped.endswith("os.getenv"):
                    issues.append({"line": i, "severity": "warning", "message": "可能包含硬编码的密钥"})

            # 裸 except
            if stripped == "except:" or stripped == "except :":
                issues.append({"line": i, "severity": "warning", "message": "裸 except 会捕获所有异常，建议指定异常类型"})

            # TODO 注释
            if "TODO" in stripped or "FIXME" in stripped or "HACK" in stripped:
                issues.append({"line": i, "severity": "info", "message": f"未完成的标记: {stripped[:50]}"})

        return {
            "total_lines": len(lines),
            "total_issues": len(issues),
            "issues": issues,
            "summary": {
                "error": sum(1 for i in issues if i["severity"] == "error"),
                "warning": sum(1 for i in issues if i["severity"] == "warning"),
                "info": sum(1 for i in issues if i["severity"] == "info"),
            },
        }


# 全局单例
_code_writer: Optional[CodeWriter] = None


def get_code_writer() -> CodeWriter:
    global _code_writer
    if _code_writer is None:
        _code_writer = CodeWriter()
    return _code_writer