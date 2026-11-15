"""直接请求 npm registry，查询缺失 @deepseek-ai 包的 0.0.1-rc.x 版本，输出 name@version 供加入 package.json。"""
import json, urllib.request

REG = "https://registry.npmjs.org/{pkg}"
MISSING = [
    "dsh-anonymous-user-id", "dsh-atomic-write", "dsh-bash", "dsh-bash-env",
    "dsh-bash-local", "dsh-code-runtime", "dsh-compaction", "dsh-environment",
    "dsh-fs", "dsh-goal-session", "dsh-output-retention", "dsh-paths",
    "dsh-sandbox", "dsh-scope", "dsh-sdk-protocol", "dsh-session-telemetry",
    "dsh-session-title-llm", "dsh-shell", "dsh-skill-local", "dsh-spill",
    "dsh-subagent-in-process-driver", "dsh-tasks-local", "dsh-timeout",
    "dsh-tool-tasks", "dsh-workflow", "dsh-workspace-context",
]

def latest_000x(pkg):
    url = REG.format(pkg=pkg.replace("@", "%40", 1).replace("/", "%2F"))
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        vers = list(data.get("versions", {}).keys())
    except Exception as e:
        print(f"  !! {pkg}: registry failed: {e}")
        return None
    if not vers:
        print(f"  !! {pkg}: no versions (not published?)")
        return None
    zeros = [v for v in vers if v.startswith("0.0.")]
    pick = zeros[-1] if zeros else vers[-1]
    return pick

def main():
    lines = []
    for short in MISSING:
        pkg = "@deepseek-ai/" + short
        v = latest_000x(pkg)
        if v:
            lines.append(f'    "@deepseek-ai/{short}": "^{v}"')
            print(f"OK  {short} -> {v}")
    print("\n--- copy into package.json dependencies ---")
    print(",\n".join(lines))

if __name__ == "__main__":
    main()
