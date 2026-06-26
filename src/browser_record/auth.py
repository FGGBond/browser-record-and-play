"""browser_record.auth — 通用浏览器 cookie 鉴权模块。

从本地 Edge / Chrome 读取指定域名的 cookie，本地缓存 4 小时。
SKILL-FORGE 产出的 CLI 工具直接调用，无需复制任何鉴权代码。

用法：
    from browser_record.auth import auth_headers
    headers = auth_headers("jagile.jd.com")   # 返回 {"Cookie": "..."}
    headers = auth_headers("jagile.jd.com", refresh=True)  # 强制刷新
"""
import json, os, re, time
from pathlib import Path

try:
    import browser_cookie3
    _HAS_BROWSER_COOKIE3 = True
except ImportError:
    _HAS_BROWSER_COOKIE3 = False

CACHE_DIR = Path.home() / ".config" / "browser-record" / "auth"
CACHE_TTL = 4 * 3600  # 4 小时

# 进程内缓存，key 为域名
_process_cache: dict[str, dict] = {}


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _cache_path(domain: str) -> Path:
    safe = re.sub(r"[^\w.-]", "_", domain)
    return CACHE_DIR / f"{safe}.json"


def _cache_read(domain: str) -> dict:
    try:
        return json.loads(_cache_path(domain).read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _cache_write(domain: str, data: dict):
    p = _cache_path(domain)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _load_from_browser(domain: str) -> str:
    """从 Edge / Chrome 本地数据库读取指定域名的 cookie 字符串。"""
    if not _HAS_BROWSER_COOKIE3:
        raise ImportError(
            "browser_cookie3 未安装，请运行: pip install browser_cookie3"
        )

    # analytics / tracking cookie 名称前缀，排除噪声
    _SKIP_PREFIXES = ("_ga", "_gid", "_gat", "_fbp", "_fbc", "ajs_", "mp_", "mixpanel")

    errors = []
    for loader in (browser_cookie3.edge, browser_cookie3.chrome):
        try:
            jar = loader(domain_name=domain)
            pairs = [
                f"{c.name}={c.value}"
                for c in jar
                if domain in c.domain
                and not any(c.name.startswith(p) for p in _SKIP_PREFIXES)
            ]
            if pairs:
                return "; ".join(pairs)
        except Exception as e:
            errors.append(str(e))
            continue

    raise RuntimeError(
        f"未能从浏览器读取 {domain!r} 的 cookie。\n"
        f"请先在 Edge 或 Chrome 中登录该站点，然后重试。\n"
        + (f"详细错误: {'; '.join(errors)}" if errors else "")
    )


# ── 公开 API ──────────────────────────────────────────────────────────────────

def get_cookie_header(domain: str, refresh: bool = False) -> str:
    """获取指定域名的 Cookie header 字符串。

    优先级：进程内缓存 → 磁盘缓存（~/.config/browser-record/auth/）→ 浏览器实时读取。

    Args:
        domain:  目标域名，如 "jagile.jd.com"
        refresh: True 时跳过所有缓存，强制从浏览器重新读取

    Returns:
        Cookie header 字符串，如 "sessionid=abc; csrftoken=xyz"
    """
    now = time.time()

    if not refresh:
        pc = _process_cache.get(domain, {})
        if pc.get("cookie_header") and now < pc.get("expires_at", 0):
            return pc["cookie_header"]

        disk = _cache_read(domain)
        if disk.get("cookie_header") and now < disk.get("expires_at", 0):
            _process_cache[domain] = disk
            return disk["cookie_header"]

    cookie_header = _load_from_browser(domain)
    entry = {"cookie_header": cookie_header, "expires_at": now + CACHE_TTL}
    _process_cache[domain] = entry
    _cache_write(domain, entry)
    return cookie_header


def auth_headers(domain: str, refresh: bool = False) -> dict:
    """返回含 Cookie 的 headers dict，可直接传给 httpx / requests。

    Args:
        domain:  目标域名，如 "jagile.jd.com"
        refresh: True 时强制从浏览器重新读取 cookie

    Returns:
        {"Cookie": "sessionid=abc; csrftoken=xyz"}

    示例：
        import httpx
        from browser_record.auth import auth_headers

        resp = httpx.get("https://jagile.jd.com/api/env/list",
                         headers=auth_headers("jagile.jd.com"))
    """
    return {"Cookie": get_cookie_header(domain, refresh=refresh)}


def invalidate(domain: str):
    """清除指定域名的所有缓存（进程内 + 磁盘）。"""
    _process_cache.pop(domain, None)
    try:
        _cache_path(domain).unlink()
    except FileNotFoundError:
        pass


def check(domain: str) -> dict:
    """检查指定域名的鉴权状态，返回诊断信息。不抛出异常。

    Returns:
        {
            "domain": "jagile.jd.com",
            "status": "ok" | "expired" | "missing",
            "source": "process_cache" | "disk_cache" | "browser" | None,
            "expires_at": 1234567890.0 | None,
            "cookie_count": 5,
        }
    """
    now = time.time()

    pc = _process_cache.get(domain, {})
    if pc.get("cookie_header") and now < pc.get("expires_at", 0):
        count = pc["cookie_header"].count(";") + 1
        return {"domain": domain, "status": "ok", "source": "process_cache",
                "expires_at": pc["expires_at"], "cookie_count": count}

    disk = _cache_read(domain)
    if disk.get("cookie_header"):
        if now < disk.get("expires_at", 0):
            count = disk["cookie_header"].count(";") + 1
            return {"domain": domain, "status": "ok", "source": "disk_cache",
                    "expires_at": disk["expires_at"], "cookie_count": count}
        return {"domain": domain, "status": "expired", "source": "disk_cache",
                "expires_at": disk.get("expires_at"), "cookie_count": 0}

    return {"domain": domain, "status": "missing", "source": None,
            "expires_at": None, "cookie_count": 0}


# ── bh-auth CLI ───────────────────────────────────────────────────────────────

def _cli_main():
    import argparse, sys

    parser = argparse.ArgumentParser(
        prog="bh-auth",
        description="管理浏览器 cookie 鉴权缓存",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="检查指定域名的鉴权状态")
    p_check.add_argument("domain", help="目标域名，如 jagile.jd.com")

    p_refresh = sub.add_parser("refresh", help="强制从浏览器重新读取 cookie")
    p_refresh.add_argument("domain", help="目标域名，如 jagile.jd.com")

    p_clear = sub.add_parser("clear", help="清除指定域名的缓存")
    p_clear.add_argument("domain", help="目标域名，如 jagile.jd.com")

    args = parser.parse_args()

    if args.cmd == "check":
        info = check(args.domain)
        status = info["status"]
        mark = "✓" if status == "ok" else "✗"
        source = info.get("source") or "-"
        expires = info.get("expires_at")
        ttl = f"{int(expires - time.time())}s remaining" if expires and status == "ok" else "-"
        print(f"{mark}  {args.domain}")
        print(f"   status:  {status}")
        print(f"   source:  {source}")
        print(f"   cookies: {info['cookie_count']}")
        print(f"   ttl:     {ttl}")
        sys.exit(0 if status == "ok" else 1)

    elif args.cmd == "refresh":
        try:
            header = get_cookie_header(args.domain, refresh=True)
            count = header.count(";") + 1
            print(f"✓  {args.domain} — {count} cookies cached")
        except Exception as e:
            print(f"✗  {e}", file=sys.stderr)
            sys.exit(1)

    elif args.cmd == "clear":
        invalidate(args.domain)
        print(f"✓  cleared cache for {args.domain}")
