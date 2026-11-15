"""
HTML 幻灯片渲染

把教学大纲渲染成无第三方依赖的单文件 HTML 幻灯片：
- 键盘 ← → / 空格 / PageUp PageDown 翻页，F 全屏，N 显示讲稿，Esc 目录
- 顶部进度条 + 页码；点击左右半屏翻页
- 对外接口：window.eduDeck.goto(n) / total / current；postMessage {type:'edu-slide-goto', index}
- 翻页时向 parent / opener 广播 {type:'edu-slide-changed', index, total, title}，供悬浮球跟随

对外接口: render_html(outline, out_path, title) -> dict
"""
from __future__ import annotations

import html
import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger("ZhiYanWeb.Edu")

_THEMES = {
    "default": {"bg": "#ffffff", "fg": "#1f2937", "sub": "#4b5563", "accent": "#3d8bfd", "panel": "#f3f6fb"},
    "dark": {"bg": "#181c26", "fg": "#eef2f8", "sub": "#c3cbd9", "accent": "#60a5fa", "panel": "#222838"},
    "fresh": {"bg": "#f6fcf9", "fg": "#164e3e", "sub": "#2d5a4b", "accent": "#10b981", "panel": "#e7f7f0"},
    "academic": {"bg": "#faf8f4", "fg": "#4a3322", "sub": "#5e4a3a", "accent": "#b45309", "panel": "#f2ece1"},
}


def render_html(outline: Dict[str, Any], out_path: str, title: str = "") -> Dict[str, Any]:
    """渲染 HTML 幻灯片；返回 {"success", "path", "message"}"""
    try:
        slides = outline.get("slides") or []
        deck_title = title or str(outline.get("title") or "教学幻灯片")
        theme = _THEMES.get(str(outline.get("theme") or "default"), _THEMES["default"])
        data_json = json.dumps(
            {
                "title": deck_title,
                "subject": outline.get("subject") or "",
                "grade": outline.get("grade") or "",
                "slides": slides,
            },
            ensure_ascii=False,
        )

        page = _HTML_TEMPLATE.replace("__TITLE__", html.escape(deck_title))
        page = page.replace("__DATA__", data_json)
        page = page.replace("__BG__", theme["bg"]).replace("__FG__", theme["fg"])
        page = page.replace("__SUB__", theme["sub"]).replace("__ACCENT__", theme["accent"]).replace("__PANEL__", theme["panel"])

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page)
        return {"success": True, "path": out_path, "message": f"已生成 HTML 幻灯片（{len(slides)} 页）"}
    except Exception as e:
        logger.error(f"生成 HTML 幻灯片失败: {e}")
        return {"success": False, "path": "", "message": f"生成 HTML 幻灯片失败: {e}"}


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg: __BG__; --fg: __FG__; --sub: __SUB__; --accent: __ACCENT__; --panel: __PANEL__; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--fg);
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif; overflow: hidden; }
  #progress { position: fixed; top: 0; left: 0; height: 4px; background: var(--accent); width: 0; z-index: 30; transition: width .18s; }
  #stage { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 4.5vh 6vw; }
  .slide { width: 100%; max-width: 1180px; animation: fade .28s ease; }
  @keyframes fade { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
  .kicker { color: var(--accent); font-weight: 700; letter-spacing: 2px; font-size: 15px; margin-bottom: 10px; }
  h1 { font-size: 3.1rem; margin: 0 0 14px; line-height: 1.2; }
  h2 { font-size: 2.1rem; margin: 0 0 22px; line-height: 1.25; border-left: 6px solid var(--accent); padding-left: 16px; }
  ul { margin: 0; padding-left: 4px; list-style: none; }
  li { font-size: 1.42rem; line-height: 1.85; color: var(--sub); margin-bottom: 8px; padding-left: 30px; position: relative; }
  li:before { content: ""; position: absolute; left: 6px; top: .72em; width: 9px; height: 9px; border-radius: 50%; background: var(--accent); }
  .cover { text-align: center; }
  .cover h1 { font-size: 3.6rem; }
  .cover .meta { color: var(--sub); font-size: 1.25rem; margin-top: 12px; }
  .ex { background: var(--panel); border-radius: 16px; padding: 22px 26px; margin-top: 10px; }
  .ex .q { font-size: 1.3rem; font-weight: 700; color: var(--fg); margin-bottom: 12px; white-space: pre-wrap; }
  .ex .opt { font-size: 1.16rem; color: var(--sub); margin: 4px 0 4px 12px; }
  .ex .ans { margin-top: 12px; font-size: 1.2rem; font-weight: 700; color: var(--accent); }
  .ex .ana { margin-top: 8px; font-size: 1.08rem; color: var(--sub); white-space: pre-wrap; }
  #notes { position: fixed; left: 0; right: 0; bottom: 0; max-height: 34vh; overflow: auto;
    background: var(--panel); border-top: 1px solid rgba(128,128,128,.3); padding: 14px 22px;
    font-size: 15px; color: var(--sub); display: none; z-index: 25; white-space: pre-wrap; }
  #notes.show { display: block; }
  #hud { position: fixed; right: 18px; bottom: 14px; color: var(--sub); font-size: 14px; z-index: 28; user-select: none; }
  #toc { position: fixed; inset: 0; background: rgba(10,14,22,.62); display: none; z-index: 40; overflow: auto; padding: 6vh 8vw; }
  #toc.show { display: block; }
  #toc .inner { background: var(--bg); border-radius: 18px; padding: 26px 30px; max-width: 760px; margin: 0 auto; }
  #toc h3 { margin: 0 0 16px; color: var(--fg); }
  #toc .item { padding: 10px 12px; border-radius: 10px; cursor: pointer; color: var(--sub); font-size: 1.06rem; }
  #toc .item:hover { background: var(--panel); color: var(--fg); }
  .hint { position: fixed; left: 18px; bottom: 14px; color: var(--sub); font-size: 12.5px; opacity: .8; z-index: 28; }
</style>
</head>
<body>
<div id="progress"></div>
<div id="stage"></div>
<div id="notes"></div>
<div id="hud"></div>
<div class="hint">← → / 空格 翻页 · F 全屏 · N 讲稿 · Esc 目录</div>
<div id="toc"><div class="inner"><h3>目录</h3><div id="toc-list"></div></div></div>
<script>
const DECK = __DATA__;
const slides = DECK.slides || [];
let cur = 0;

function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g, c=>({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;" }[c])); }
function splitNotes(t){ return esc(t||""); }

function render(i){
  if (!slides.length) return;
  cur = Math.max(0, Math.min(i, slides.length - 1));
  const s = slides[cur] || {};
  const stage = document.getElementById("stage");
  const type = s.type || "content";
  let html = "";
  if (type === "cover") {
    html = '<div class="slide cover">'
      + '<div class="kicker">' + esc([DECK.subject, DECK.grade].filter(Boolean).join(" · ")) + '</div>'
      + '<h1>' + esc(s.title || DECK.title) + '</h1>'
      + (s.bullets && s.bullets.length ? '<div class="meta">' + esc(s.bullets.join("　")) + '</div>' : '')
      + '</div>';
  } else if (type === "example" && s.example) {
    const ex = s.example;
    let exH = '<div class="ex">';
    if (ex.question) exH += '<div class="q">' + esc(ex.question) + '</div>';
    (ex.options || []).forEach((o, k) => { exH += '<div class="opt">' + "ABCDEFGH"[k] + '. ' + esc(o) + '</div>'; });
    if (ex.answer) exH += '<div class="ans">答案：' + esc(ex.answer) + '</div>';
    if (ex.analysis) exH += '<div class="ana">解析：' + esc(ex.analysis) + '</div>';
    exH += '</div>';
    html = '<div class="slide"><h2>' + esc(s.title || "例题演练") + '</h2>' + exH + '</div>';
  } else {
    let li = (s.bullets || []).map(b => '<li>' + esc(b) + '</li>').join("");
    html = '<div class="slide"><h2>' + esc(s.title || "") + '</h2><ul>' + li + '</ul></div>';
  }
  stage.innerHTML = html;
  document.getElementById("progress").style.width = ((cur + 1) / slides.length * 100) + "%";
  document.getElementById("hud").textContent = (cur + 1) + " / " + slides.length;
  const notes = document.getElementById("notes");
  notes.innerHTML = s.notes ? '<b>讲稿：</b>' + splitNotes(s.notes) : "";
  try {
    const msg = { type: "edu-slide-changed", index: cur + 1, total: slides.length, title: s.title || "" };
    if (window.parent && window.parent !== window) window.parent.postMessage(msg, "*");
    if (window.opener) window.opener.postMessage(msg, "*");
  } catch (e) {}
}

function goto(i){ render(i - 1 >= 0 ? i - 1 : 0); }
function next(){ render(cur + 1); }
function prev(){ render(cur - 1); }

document.addEventListener("keydown", e => {
  const k = e.key;
  if (k === "ArrowRight" || k === "ArrowDown" || k === " " || k === "PageDown") { e.preventDefault(); next(); }
  else if (k === "ArrowLeft" || k === "ArrowUp" || k === "PageUp") { e.preventDefault(); prev(); }
  else if (k === "Home") render(0);
  else if (k === "End") render(slides.length - 1);
  else if (k === "f" || k === "F") { if (!document.fullscreenElement) document.documentElement.requestFullscreen(); else document.exitFullscreen(); }
  else if (k === "n" || k === "N") document.getElementById("notes").classList.toggle("show");
  else if (k === "Escape") document.getElementById("toc").classList.toggle("show");
});
document.getElementById("stage").addEventListener("click", e => {
  if (e.clientX < window.innerWidth * 0.32) prev();
  else next();
});
// 目录
const tocList = document.getElementById("toc-list");
tocList.innerHTML = slides.map((s, i) => '<div class="item" data-i="' + i + '">' + (i + 1) + ". " + esc(s.title || "") + '</div>').join("");
tocList.addEventListener("click", e => {
  const it = e.target.closest(".item"); if (!it) return;
  render(parseInt(it.dataset.i, 10)); document.getElementById("toc").classList.remove("show");
});

// 供悬浮球 / 外部控制
window.eduDeck = { goto: goto, next: next, prev: prev, get current(){ return cur + 1; }, get total(){ return slides.length; }, render: render };
window.eduSlideGoto = goto;
window.addEventListener("message", e => {
  const d = e.data || {};
  if (d.type === "edu-slide-goto" && d.index != null) goto(parseInt(d.index, 10));
  else if (d.type === "edu-slide-next") next();
  else if (d.type === "edu-slide-prev") prev();
});
render(0);
</script>
</body>
</html>
"""