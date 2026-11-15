"""扫描 npm-core 已安装的 @deepseek-ai 包，列出代码中 import 了但 node_modules 缺失的 @deepseek-ai/* 包。"""
import json, os, re, sys

NPM_CORE = r"d:\Python\NEW_start\ZHIYAN AGENT WEB\dsh-suite\npm-core"
NM = os.path.join(NPM_CORE, "node_modules")
DSA = os.path.join(NM, "@deepseek-ai")

IMPORT_RE = re.compile(r"(?:import|export)[^'\"]*?from\s*['\"](@deepseek-ai/[^'\"]+)['\"]")
DYNAMIC_RE = re.compile(r"import\(\s*['\"](@deepseek-ai/[^'\"]+)['\"]")

def installed():
    if not os.path.isdir(DSA):
        return set()
    return {name for name in os.listdir(DSA) if os.path.isdir(os.path.join(DSA, name))}

def main():
    have = installed()
    print("installed @deepseek-ai:", len(have))
    missing = {}
    root = os.path.join(NPM_CORE, "node_modules")
    for dirpath, dirnames, filenames in os.walk(root):
        # 跳过嵌套 node_modules（顶层 node_modules 只算一次；出现 >1 次即嵌套副本，各自完整）
        if dirpath.split(os.sep).count("node_modules") > 1:
            continue
        # 只扫描 lib 目录下的 js（运行时代码）
        if not ("\\lib" in dirpath):
            continue
        for fn in filenames:
            if not fn.endswith(".js"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    txt = f.read()
            except Exception:
                continue
            for m in IMPORT_RE.finditer(txt) or []:
                dep = m.group(1).split("/")[0:2]
                dep = dep[0] if len(dep) == 1 else "@" + dep[0][1:] + "/" + dep[1]
                if dep.startswith("@deepseek-ai/"):
                    short = dep.split("/")[1]
                    if short not in have:
                        missing.setdefault(dep, set()).add(os.path.relpath(p, root))
            for m in DYNAMIC_RE.finditer(txt) or []:
                dep = m.group(1)
                short = dep.split("/")[1]
                if short not in have:
                    missing.setdefault(dep, set()).add(os.path.relpath(p, root))
    if not missing:
        print("OK: no missing @deepseek-ai imports found in installed packages.")
        return
    for dep, srcs in sorted(missing.items()):
        print(f"MISSING {dep}  (imported by {len(srcs)} file(s))")
        for s in sorted(srcs)[:3]:
            print(f"    {s}")

if __name__ == "__main__":
    main()
