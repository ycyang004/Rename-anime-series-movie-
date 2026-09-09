"""
OpenList 剧集重命名工具 - Web 服务

FastAPI 后端：为前端 Web UI 提供 API，并托管静态页面。
运行: python server.py
"""
import os
import sys
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Any
import re
import requests

from core import InteractiveEpisodeRenamer

# 前端目录
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend')

# 全局单例（单用户模式）
renamer: InteractiveEpisodeRenamer | None = None

app = FastAPI(title="OpenList 剧集重命名工具")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ==================== 认证与配置 ====================

class LoginReq(BaseModel):
    base_url: str
    username: str
    password: str

@app.post("/api/login")
def api_login(req: LoginReq):
    global renamer
    try:
        r = InteractiveEpisodeRenamer(req.base_url, req.username, req.password)
        r.load_token()
        if r.validate_current_user():
            r.save_config(req.base_url)
            renamer = r
            return {"success": True, "message": f"以 {r.username} 身份连接成功"}
        ok = r.login()
        if ok:
            r.save_config(req.base_url)
            renamer = r
            return {"success": True, "message": f"登录成功，欢迎 {r.username}"}
        return {"success": False, "message": "登录失败，请检查账号密码"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/config")
def api_get_config():
    """获取 OpenList 连接 + 用户设置"""
    if renamer:
        base_url = renamer.base_url
        settings = renamer.settings
    else:
        base_url = "http://127.0.0.1:5244"
        settings = dict(InteractiveEpisodeRenamer.DEFAULT_SETTINGS)
    return {"base_url": base_url, "settings": settings}

@app.post("/api/logout")
def api_logout():
    """退出登录：清除当前会话"""
    global renamer
    renamer = None
    return {"success": True, "message": "已退出登录"}

@app.post("/api/config")
def api_set_config(req: dict):
    """保存用户设置（TMDB Key、分隔符等），持久化到 settings.json"""
    if not renamer:
        return JSONResponse({"success": False, "message": "尚未登录"}, status_code=400)
    if "settings" in req and isinstance(req["settings"], dict):
        data = req["settings"]
    else:
        data = req
    settings = renamer.save_settings(data)
    return {"success": True, "settings": settings}

@app.get("/api/health")
def api_health():
    """健康检查：OpenList 可达性 + 当前登录状态"""
    status = {"logged_in": bool(renamer), "openlist_ok": False, "user": None}
    if renamer:
        status["user"] = renamer.username
        try:
            ok = renamer.validate_current_user()
            status["openlist_ok"] = ok
            if not ok:
                # token 过期则尝试重登
                ok = renamer.login()
                status["openlist_ok"] = ok
        except Exception:
            pass
    return status


# ==================== 目录与文件 ====================

@app.post("/api/list")
def api_list(req: dict):
    """列出指定路径下的文件与目录"""
    if not renamer:
        return JSONResponse({"error": "未登录"}, status_code=401)
    path = req.get("path", "/")
    try:
        base = path.rstrip("/")
        def enrich(items):
            out = []
            for it in items:
                it = dict(it)
                if not it.get("path"):
                    it["path"] = (base + "/" + (it.get("name") or "")) if base else "/" + (it.get("name") or "")
                out.append(it)
            return out
        dirs = enrich(renamer.list_directories(path))
        files = enrich(renamer.list_files(path))
        return {"directories": dirs, "files": files, "path": path}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/extract_info")
def api_extract_info(req: dict):
    """批量从文件名提取剧集信息"""
    if not renamer:
        return JSONResponse({"error": "未登录"}, status_code=401)
    names = req.get("filenames", [])
    results = []
    for name in names:
        try:
            info = renamer.extract_episode_info(name)
            results.append({"filename": name, "valid": bool(info), "info": info})
        except Exception as e:
            results.append({"filename": name, "valid": False, "error": str(e)})
    return {"results": results}


# ==================== TMDB（API 优先，网页抓取兜底） ====================

def _tmdb_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

@app.post("/api/tmdb_search")
def api_tmdb_search(req: dict):
    """搜索 TMDB 剧集或电影。配置了 API Key 走 REST API，否则网页抓取。"""
    keyword = req.get("keyword", "").strip()
    if not keyword:
        return JSONResponse({"error": "缺少 keyword"}, status_code=400)
    media_type = (req.get("media_type") or req.get("type") or "tv").strip().lower()
    if media_type not in {"tv", "movie"}:
        return JSONResponse({"error": "media_type 只能是 tv 或 movie"}, status_code=400)
    api_key = (renamer.settings.get("tmdb_api_key", "") if renamer else "").strip()
    if api_key:
        try:
            results = renamer.tmdb_api_search(keyword, api_key, media_type)
            return {"results": results, "source": "api"}
        except Exception as e:
            return JSONResponse({"error": f"TMDB API 调用失败: {e}"}, status_code=502)
    # 网页抓取兜底
    try:
        from bs4 import BeautifulSoup
        path_prefix = "tv" if media_type == "tv" else "movie"
        res = requests.get(
            f"https://www.themoviedb.org/search/{path_prefix}",
            params={"query": keyword, "language": "zh-CN"},
            headers=_tmdb_headers(), timeout=15,
        )
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        results = []
        seen = set()
        for card in soup.select('div[class*="media-card"]'):
            link = card.select_one(f'a[href^="/{path_prefix}/"]')
            if not link:
                continue
            href = link.get("href", "")
            m = href.split(f"/{path_prefix}/")[-1].split("?")[0]
            if not m:
                continue
            tmdb_id = m.split("-")[0]
            if not tmdb_id.isdigit() or tmdb_id in seen:
                continue
            title_el = card.select_one("h2")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                img = card.select_one("img[alt]")
                title = img.get("alt", "") if img else ""
            poster = ""
            img_el = card.select_one("img[src], img[data-src]")
            if img_el:
                src = img_el.get("src") or img_el.get("data-src") or ""
                if src.startswith("//"):
                    src = "https:" + src
                poster = src
            overview_el = card.select_one("p")
            overview = overview_el.get_text(strip=True)[:120] if overview_el else ""
            year_el = card.select_one("span.release_date")
            year = ""
            if year_el:
                m = re.search(r"(19|20)\d{2}", year_el.get_text())
                year = m.group(0) if m else ""
            seen.add(tmdb_id)
            results.append({"id": int(tmdb_id), "name": title, "overview": overview, "year": year,
                            "poster": poster, "media_type": media_type})
            if len(seen) >= 10:
                break
        return {"results": results, "source": "scrape"}
    except Exception as e:
        return JSONResponse({"error": f"TMDB 搜索失败: {e}"}, status_code=502)

@app.post("/api/tmdb_fetch")
def api_tmdb_fetch(req: dict):
    """获取季的分集名称。
    方式 1（API）: {"series_id": 1396, "season": 1}  —— 需要 tmdb_api_key
    方式 2（抓取）: {"url": "www.themoviedb.org/tv/1396/season/1"} —— 网页抓取兜底
    """
    media_type = (req.get("media_type") or req.get("type") or "tv").strip().lower()
    if media_type != "tv":
        return JSONResponse({"error": "电影没有季/集信息，请直接应用电影名"}, status_code=400)
    api_key = (renamer.settings.get("tmdb_api_key", "") if renamer else "").strip()
    series_id = req.get("series_id")
    season = req.get("season")
    if series_id and season is not None:
        if api_key:
            try:
                episodes = renamer.tmdb_api_season(int(series_id), int(season), api_key)
                return {"episodes": episodes, "source": "api"}
            except Exception as e:
                return JSONResponse({"error": f"TMDB API 调用失败: {e}"}, status_code=502)
        # 无 API Key：自动构造 Season 页面 URL 走网页抓取兜底
        url = f"https://www.themoviedb.org/tv/{int(series_id)}/season/{int(season)}?language=zh-CN"
    else:
        url = req.get("url", "").strip()
        if not url:
            return JSONResponse({"error": "缺少 series_id/season 或 url"}, status_code=400)
    try:
        if url.startswith("http"):
            url_full = url
        elif url.startswith("www."):
            url_full = "https://" + url
        else:
            url_full = "https://www.themoviedb.org/" + url.lstrip("/")
        res = requests.get(url_full, headers=_tmdb_headers(), timeout=15)
        res.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(res.text, 'html.parser')
        episodes = {}
        for card in soup.select('.episode_list .card'):
            num_el = card.select_one('.episode_number')
            name_el = card.select_one('.episode_title h3 a')
            if num_el and name_el:
                episodes[num_el.get_text(strip=True)] = name_el.get_text(strip=True)
        return {"episodes": episodes, "source": "scrape"}
    except Exception as e:
        return JSONResponse({"error": f"TMDB 抓取失败: {e}"}, status_code=502)


# ==================== 重命名 ====================

@app.post("/api/rename")
def api_rename(req: dict):
    """批量重命名。逐个调用单文件重命名以获取每项结果"""
    if not renamer:
        return JSONResponse({"error": "未登录"}, status_code=401)
    path = req.get("path", "/")
    base = path.rstrip("/")
    renames = req.get("renames", [])
    errors = []
    success = 0
    for item in renames:
        old = (item.get("old_name") or "").strip()
        new = (item.get("new_name") or "").strip()
        if not old or not new or old == new:
            continue
        full = (base + "/" + old) if base else "/" + old
        ok = bool(renamer.rename_single_item(full, new))
        if ok:
            success += 1
        else:
            errors.append(f"「{old}」重命名失败")
    total = len(errors) + success
    failed = total - success
    return {
        "success": failed == 0 and total > 0,
        "renamed_count": success,
        "total": total,
        "failed": failed,
        "errors": errors,
        "message": f"成功 {success} 个，失败 {failed} 个" if failed else f"成功重命名 {success} 个",
    }


# ==================== 静态文件与页面 ====================

@app.get("/", response_class=HTMLResponse)
async def index():
    try:
        with open(os.path.join(FRONTEND_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>前端文件未找到</h1><p>请检查 frontend/index.html 是否存在</p>"

try:
    if os.path.isdir(FRONTEND_DIR):
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
except Exception:
    pass

if __name__ == "__main__":
    import uvicorn
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    browser_url = f"http://127.0.0.1:{port}/"
    print("=" * 50)
    print("OpenList 交互式剧集重命名工具 (Web 服务)")
    print("=" * 50)
    print(f"浏览器访问: {browser_url}")
    print(f"API 文档: http://{host}:{port}/docs")
    print("=" * 50)
    auto_open = getattr(sys, "frozen", False) or os.environ.get("EPISODE_OPEN_BROWSER", "").lower() in ("1", "true", "yes")
    if auto_open:
        import threading, time, webbrowser
        def open_browser():
            time.sleep(0.5)
            try:
                webbrowser.open(browser_url)
                print(f"已自动打开浏览器: {browser_url}")
            except Exception:
                pass
        threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host=host, port=port)
