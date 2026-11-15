"""
Web 浏览器自动化工具

提供网页浏览、搜索、截图等能力。
使用 requests + webbrowser 实现轻量级操作，
无需安装 Selenium 等重型依赖。
"""
from __future__ import annotations

import json
import logging
import webbrowser
from typing import Optional

logger = logging.getLogger("ZhiYanWeb.Computer.WebOps")


async def open_url(url: str) -> dict:
    """在默认浏览器中打开指定 URL

    Args:
        url: 要打开的 URL

    Returns:
        dict: {success, message}
    """
    try:
        webbrowser.open(url)
        logger.info(f"在浏览器中打开: {url}")
        return {"success": True, "message": f"已打开: {url}"}
    except Exception as e:
        logger.error(f"打开 URL 失败: {e}")
        return {"success": False, "error": str(e)}


async def web_fetch(url: str, timeout: int = 15) -> dict:
    """获取网页内容（文本模式）

    支持多个真实浏览器 UA 依次尝试，绕过常见的反爬 403 拦截，
    并自动处理 gzip/编码，返回去标签后的可读文本。

    Args:
        url: 网页 URL
        timeout: 超时时间（秒）

    Returns:
        dict: {success, content, title, headers}
    """
    import requests
    import re

    ua_list = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    ]

    last_error = None
    resp = None
    for ua in ua_list:
        try:
            headers = {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Referer": "https://www.baidu.com/",
            }
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 403:
                last_error = f"403 Forbidden（尝试其他 UA）"
                continue
            resp.raise_for_status()
            break
        except Exception as e:
            last_error = str(e)

    if resp is None:
        return {"success": False, "error": f"网页获取失败: {last_error}"}

    try:
        # 自动检测编码（百度百科等常为 gbk/utf-8）
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        text = resp.text
    except Exception:
        text = ""

    # 提取标题
    title = ""
    content_type = resp.headers.get("Content-Type", "")
    if "text/html" in content_type or "application/json" in content_type:
        title_match = re.search(r'<title>(.*?)</title>', text, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = _strip_html(title_match.group(1))

    return {
        "success": True,
        "url": url,
        "title": title,
        "status_code": resp.status_code,
        "content_length": len(text),
        "content_preview": _strip_html(text)[:3000] if "text" in content_type else "(非文本内容)",
    }


_SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _strip_html(html: str) -> str:
    """去除 HTML 标签并合并空白"""
    import re
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


async def _search_bing(query: str, num_results: int) -> list:
    """Bing 搜索引擎（cn.bing.com，国内可访问）"""
    import requests
    import re
    url = f"https://cn.bing.com/search?q={requests.utils.quote(query)}"
    resp = requests.get(url, headers=_SEARCH_HEADERS, timeout=12)
    resp.raise_for_status()
    html = resp.text

    results = []
    # Bing 结果：<li class="b_algo"><h2><a href="URL">TITLE</a></h2>...<p>SNIPPET</p>
    blocks = re.split(r'class="b_algo"', html)[1:num_results + 1]
    for block in blocks:
        href_match = re.search(r'<a[^>]*href="(https?://[^"]+)"', block)
        title_match = re.search(r'<h2[^>]*>.*?<a[^>]*>(.*?)</a>', block, re.DOTALL)
        if not href_match or not title_match:
            continue
        title = _strip_html(title_match.group(1))
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        snippet = _strip_html(snippet_match.group(1)) if snippet_match else ""
        results.append({"title": title, "url": href_match.group(1), "snippet": snippet})
        if len(results) >= num_results:
            break
    return results


async def _search_baidu(query: str, num_results: int) -> list:
    """百度搜索引擎（国内可访问）"""
    import requests
    import re
    url = f"https://www.baidu.com/s?wd={requests.utils.quote(query)}"
    resp = requests.get(url, headers=_SEARCH_HEADERS, timeout=12)
    resp.raise_for_status()
    html = resp.text

    results = []
    # 百度结果：<h3 class="t"><a href="URL">TITLE</a></h3> ... <span class="content-right_8Zs40">SNIPPET</span>
    blocks = re.split(r'class="result', html)[1:]
    for block in blocks:
        href_match = re.search(r'<a[^>]*href="(https?://[^"]+)"', block)
        title_match = re.search(r'<h3[^>]*>.*?<a[^>]*>(.*?)</a>', block, re.DOTALL)
        if not href_match or not title_match:
            continue
        title = _strip_html(title_match.group(1))
        snippet_match = re.search(r'<span[^>]*content-right[^>]*>(.*?)</span>', block, re.DOTALL)
        snippet = _strip_html(snippet_match.group(1)) if snippet_match else ""
        results.append({"title": title, "url": href_match.group(1), "snippet": snippet})
        if len(results) >= num_results:
            break
    return results


async def _search_ddg(query: str, num_results: int) -> list:
    """DuckDuckGo（备用，国内可能超时）"""
    import requests
    import re
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    resp = requests.get(url, headers=_SEARCH_HEADERS, timeout=10)
    resp.raise_for_status()
    html = resp.text
    results = []
    for match in re.finditer(
        r'class="result__a"[^>]*href="(.*?)"[^>]*>(.*?)</a>', html, re.DOTALL
    ):
        if len(results) >= num_results:
            break
        title = _strip_html(match.group(2))
        if title:
            results.append({"title": title, "url": match.group(1), "snippet": ""})
    return results


async def web_search(query: str, num_results: int = 5) -> dict:
    """执行网页搜索（多引擎容错：Bing → 百度 → DuckDuckGo）

    依次尝试各搜索引擎，直至成功返回结果或全部失败。

    Args:
        query: 搜索关键词
        num_results: 返回结果数量

    Returns:
        dict: {success, query, results: [{title, url, snippet}], total, engine}
    """
    import asyncio
    engines = [
        ("Bing", _search_bing),
        ("百度", _search_baidu),
        ("DuckDuckGo", _search_ddg),
    ]
    last_error = None
    for engine_name, engine_func in engines:
        try:
            results = await engine_func(query, num_results)
            if results:
                logger.info(f"搜索引擎 [{engine_name}] 返回 {len(results)} 条结果")
                return {
                    "success": True,
                    "query": query,
                    "results": results,
                    "total": len(results),
                    "engine": engine_name,
                }
            last_error = f"{engine_name} 未返回结果"
            logger.warning(f"搜索引擎 {engine_name} 未返回结果")
        except Exception as e:
            last_error = f"{engine_name} 失败: {e}"
            logger.error(f"搜索引擎 {engine_name} 调用失败: {e}")
    return {"success": False, "query": query, "error": str(last_error), "results": []}


async def get_page_screenshot(url: str) -> dict:
    """获取网页截图（使用外部 API）

    注意：此功能需要网络连接，且截图质量取决于外部服务。

    Args:
        url: 要截图的网页 URL

    Returns:
        dict: {success, image_url, message}
    """
    try:
        # 使用免费截图 API
        screenshot_url = f"https://api.miniature.io/?url={url}&width=1280&height=720"
        return {
            "success": True,
            "image_url": screenshot_url,
            "message": f"网页截图生成链接: {screenshot_url}",
            "note": "此截图由外部服务生成，可能需要网络连接",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}