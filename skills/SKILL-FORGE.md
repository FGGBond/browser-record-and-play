# browser-record-and-play: Skill Forge

## 用途

分析 `bh-record` 产出的 JSON 录制文件，提取关键 API 调用链，生成：
1. 一套可执行的 Python CLI 工具包（每条录制请求对应一个 CLI 命令）
2. 一份指导 AI agent 使用这套工具的 SKILL.md

---

## 调用方式

用户提供录制文件路径，你完成以下全部步骤：

```
请按照 SKILL-FORGE.md 的步骤，分析这个录制文件并生成工具：
录制文件: ./recordings/recording_20260626_143022.json
输出目录: ./my-tool/
工具名称: jagile-cli（可选，默认从域名推断）
```

---

## 执行步骤

### Step 1：读取并理解录制文件

读取 JSON，理解结构，输出摘要：

```python
import json
data = json.loads(open("recording_xxx.json").read())
# data["steps"]  — 每个用户标注的步骤，含 description + requests[]
# data["tail"]   — 最后一步之后的请求
```

对每个 step 输出：
- step 描述、请求总数
- POST/PUT/DELETE 数量（状态变更，优先分析）
- 涉及的主要域名

---

### Step 2：识别关键请求

过滤掉噪声，只保留**业务请求**：

**排除**：
- URL 后缀：`.png` `.jpg` `.svg` `.css` `.js` `.woff` `.ico` `.map`
- URL 含关键词：`analytics` `track` `beacon` `telemetry` `sentry` `datadog` `gtm` `ga.` `fbq` `/static/` `/assets/` `/dist/`
- 响应状态码 >= 400

**保留**：
- 所有 `POST` / `PUT` / `PATCH` / `DELETE`
- 返回 JSON 且含业务字段（`id` `list` `data` `token` `result`）的 `GET`

---

### Step 3：分析鉴权方式

检查录制请求头，识别鉴权类型，按优先级：

**类型 A — Cookie 鉴权**（请求头含 `Cookie:`）

这是最常见的内网/企业系统鉴权方式，对应 taishan-sql 的 `browser_cookie3` 模式：
- 确认 cookie 所属域名（如 `jagile.jd.com`）
- 记录关键 cookie 名称（忽略 `_ga` `_gid` 等 analytics cookie）
- **生成 `auth.py` 时使用 `browser_cookie3` 从本地浏览器读取**，不要求用户手动输入

**类型 B — Bearer Token**（请求头含 `Authorization: Bearer ...`）

- 确认 token 来源：是否有某个 `POST /login` 或 `/auth` 请求返回了 token
- 如果有登录请求：生成 `auth.py` 包含 `login(username, password)` 函数
- 如果 token 来源不明（如 SSO）：生成 `auth.py` 读取环境变量 `TOKEN`

**类型 C — API Key**（请求头含 `X-Api-Key` / `X-Token` 等）

- 从录制 headers 提取 key 名称
- 生成 `auth.py` 读取对应环境变量

**Cookie 鉴权的 auth.py 模板（参考 taishan-sql 实现）：**

```python
"""auth.py — 从本地浏览器读取 cookie，本地缓存 4 小时。"""
import json, os, time
from pathlib import Path

import browser_cookie3

COOKIE_DOMAIN = "example.jd.com"        # 从录制中提取的域名
CACHE_PATH = Path.home() / ".config" / "{tool_name}" / "auth-session.json"
CACHE_TTL = 4 * 3600                     # 4 小时

_process_cache: dict = {}


def _load_from_browser() -> str:
    """从 Edge/Chrome 本地数据库读取 cookie 字符串。"""
    for loader in (browser_cookie3.edge, browser_cookie3.chrome):
        try:
            jar = loader(domain_name=COOKIE_DOMAIN)
            pairs = [f"{c.name}={c.value}" for c in jar if COOKIE_DOMAIN in c.domain]
            if pairs:
                return "; ".join(pairs)
        except Exception:
            continue
    raise RuntimeError(
        f"未能从浏览器读取 {COOKIE_DOMAIN} 的 cookie。\n"
        "请先在浏览器中登录，然后重试。\n"
        "支持的浏览器：Edge、Chrome（需已登录且 cookie 未过期）"
    )


def _cache_read() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _cache_write(data: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data))
    try:
        os.chmod(CACHE_PATH, 0o600)
    except OSError:
        pass


def get_cookie_header(refresh: bool = False) -> str:
    """获取 cookie header 字符串，优先使用缓存。"""
    if not refresh:
        if _process_cache.get("cookie_header") and time.time() < _process_cache.get("expires_at", 0):
            return _process_cache["cookie_header"]
        cache = _cache_read()
        if cache.get("cookie_header") and time.time() < cache.get("expires_at", 0):
            _process_cache.update(cache)
            return cache["cookie_header"]

    cookie_header = _load_from_browser()
    expires_at = time.time() + CACHE_TTL
    entry = {"cookie_header": cookie_header, "expires_at": expires_at}
    _process_cache.update(entry)
    _cache_write(entry)
    return cookie_header


def auth_headers(refresh: bool = False) -> dict:
    """返回包含 Cookie 的 headers dict，可直接传给 httpx。"""
    return {"Cookie": get_cookie_header(refresh=refresh)}
```

---

### Step 4：建立请求 → CLI 命令的映射

**核心原则：每条业务请求对应一个 CLI 命令。**

对 Step 2 保留的每条请求，生成一个映射表：

| 录制位置 | 请求 | CLI 命令 | 函数名 | 所在文件 |
|---------|------|---------|--------|---------|
| STEP 1 "查询环境应用列表" | GET /api/env/list | `list-envs` | `list_envs()` | `env.py` |
| STEP 1 "查询环境应用列表" | GET /api/app/list?envId=xxx | `list-apps` | `list_apps(env_id)` | `app.py` |
| STEP 2 "选择分支" | GET /api/branch/list?appId=xxx | `list-branches` | `list_branches(app_id)` | `branch.py` |
| STEP 2 "选择分支" | POST /api/deploy/create | `create-deploy` | `create_deploy(app_id, branch)` | `deploy.py` |

**映射规则：**
- 同一个录制 step 的请求，按 URL path 归入相同或相近的 `.py` 文件
- URL path 中的动态段（`/api/app/{id}/detail`）→ 函数参数
- Query string 参数 → 函数参数（有默认值的可选参数）
- POST body 中的字段 → 函数参数

---

### Step 5：分析请求间依赖关系

检查请求之间的数据流：

1. **响应字段 → 下一请求参数**：response_body 中某字段值出现在后续请求的 URL / headers / body 中
2. **Cookie 跨请求复用**：所有请求共享同一 cookie，用 `httpx.Client` session 管理
3. **CSRF token**：来自 cookie 或响应 header 的 `X-CSRF-Token`，需要在每次请求时提取并回传

输出依赖图：
```
GET /api/env/list → response: [{id: "prod", name: "生产"}]
  └─ envId="prod" → GET /api/app/list?envId=prod

GET /api/app/list → response: [{id: "app-123"}]
  └─ appId="app-123" → GET /api/branch/list?appId=app-123
  └─ appId="app-123" → POST /api/deploy/create {appId: "app-123", ...}
```

---

### Step 6：识别动态参数

找出每个请求中需要参数化的字段：

- **业务 ID**：来自上一步响应的 id 字段（已在 Step 5 标出）
- **时间戳**：值接近录制时间的数字 → `int(time.time() * 1000)`
- **CSRF token**：从 cookie 或响应 header 提取
- **用户输入值**：在 step 描述中被用户提到的词（如"选择了生产环境"→ envId 是用户选择的）

---

### Step 7：生成 CLI 工具包

在 `{output_dir}/` 下生成文件，**每个 `.py` 文件同时是库模块和可执行 CLI**：

**目录结构：**
```
{output_dir}/
├── auth.py              # cookie / token 获取与缓存
├── {domain}.py          # 按业务域分组，每个录制请求一个函数 + 一个 CLI 子命令
├── run_all.py           # 串联所有步骤的完整流程
├── SKILL.md             # AI agent 使用说明（Step 8 生成）
└── README.md            # 人类可读的安装和使用说明
```

**代码规范：**

每个 `.py` 文件同时支持 import 和 `python xxx.py` 直接运行：

```python
# env.py — 对应录制 STEP 1 "查询环境应用列表" 中的 GET /api/env/list
"""
录制来源: STEP 1 - 查询环境应用列表
请求: GET https://jagile.jd.com/api/env/list
"""
import argparse, json
import httpx
from auth import auth_headers

BASE_URL = "https://jagile.jd.com"


def list_envs() -> list:
    """列出所有可用环境。
    
    对应录制: STEP 1 GET /api/env/list
    返回: [{"id": "prod", "name": "生产环境"}, ...]
    """
    resp = httpx.get(
        f"{BASE_URL}/api/env/list",
        headers={
            **auth_headers(),
            "Accept": "application/json",
        },
    )
    resp.raise_for_status()
    return resp.json()["data"]


def list_apps(env_id: str) -> list:
    """列出指定环境下的应用列表。
    
    对应录制: STEP 1 GET /api/app/list?envId={env_id}
    参数:
      env_id: 环境 ID，从 list_envs() 结果的 id 字段获取
    返回: [{"id": "app-123", "name": "order-service"}, ...]
    """
    resp = httpx.get(
        f"{BASE_URL}/api/app/list",
        params={"envId": env_id},
        headers={**auth_headers(), "Accept": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()["data"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="环境和应用管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-envs", help="列出所有环境")

    p = sub.add_parser("list-apps", help="列出指定环境下的应用")
    p.add_argument("--env-id", required=True, help="环境 ID（来自 list-envs）")

    args = parser.parse_args()
    if args.cmd == "list-envs":
        print(json.dumps(list_envs(), ensure_ascii=False, indent=2))
    elif args.cmd == "list-apps":
        print(json.dumps(list_apps(args.env_id), ensure_ascii=False, indent=2))
```

**run_all.py 模板：**

```python
# run_all.py — 串联录制的完整操作流程
"""
复现录制的完整流程:
  STEP 1: 查询环境应用列表
  STEP 2: 选择分支
  STEP 3: 创建部署
"""
import argparse, json
from env import list_envs, list_apps
from branch import list_branches
from deploy import create_deploy


def run(env_name: str, app_name: str, branch: str):
    # STEP 1: 查询环境
    envs = list_envs()
    env = next((e for e in envs if e["name"] == env_name), None)
    if not env:
        raise ValueError(f"环境 '{env_name}' 不存在，可用: {[e['name'] for e in envs]}")

    # STEP 1: 查询应用
    apps = list_apps(env["id"])
    app = next((a for a in apps if a["name"] == app_name), None)
    if not app:
        raise ValueError(f"应用 '{app_name}' 不存在")

    # STEP 2: 查询分支
    branches = list_branches(app["id"])
    if branch not in [b["name"] for b in branches]:
        raise ValueError(f"分支 '{branch}' 不存在")

    # STEP 3: 创建部署
    result = create_deploy(app["id"], branch)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="执行完整的部署流程")
    parser.add_argument("--env", required=True, help="环境名称（如：生产环境）")
    parser.add_argument("--app", required=True, help="应用名称（如：order-service）")
    parser.add_argument("--branch", required=True, help="分支名称（如：main）")
    args = parser.parse_args()
    run(args.env, args.app, args.branch)
```

**httpx 使用规范：**
- 所有请求使用 `httpx.get` / `httpx.post`（同步）
- 复制录制中的 headers，去掉 `content-length` `host` `content-encoding`
- 不要硬编码 cookie 值，始终通过 `auth_headers()` 注入

---

### Step 8：生成 SKILL.md

```markdown
# {工具名} Skill

## 用途
[从录制描述推断的一句话总结]

## 安装
\`\`\`bash
pip install httpx browser_cookie3
\`\`\`

## 前提条件
- 在 Edge 或 Chrome 中已登录 {domain}（工具自动读取浏览器 cookie，无需手动配置）
- 如 cookie 失效，在浏览器重新登录后重试，缓存会自动刷新

## CLI 命令速查

每个命令均可 `python {文件}.py --help` 查看参数。

| 文件 | 命令 | 对应录制步骤 | 说明 |
|------|------|------------|------|
| env.py | `list-envs` | STEP 1 GET /api/env/list | 列出所有环境 |
| env.py | `list-apps --env-id ENV_ID` | STEP 1 GET /api/app/list | 列出指定环境的应用 |
| branch.py | `list-branches --app-id APP_ID` | STEP 2 GET /api/branch/list | 列出应用的分支 |
| deploy.py | `create --app-id APP_ID --branch BRANCH` | STEP 3 POST /api/deploy/create | 创建部署 |

## 完整流程示例

\`\`\`python
from env import list_envs, list_apps
from branch import list_branches
from deploy import create_deploy

# 查询可用环境（对应录制 STEP 1）
envs = list_envs()
env_id = envs[0]["id"]

# 查询应用列表（对应录制 STEP 1）
apps = list_apps(env_id)
app_id = apps[0]["id"]

# 查询分支（对应录制 STEP 2）
branches = list_branches(app_id)

# 创建部署（对应录制 STEP 3）
result = create_deploy(app_id, branch="main")
\`\`\`

或一键运行完整流程：
\`\`\`bash
python run_all.py --env 生产环境 --app order-service --branch main
\`\`\`

## 注意事项
- cookie 本地缓存 4 小时，路径：`~/.config/{tool_name}/auth-session.json`
- 强制刷新 cookie：在 auth.py 中调用 `get_cookie_header(refresh=True)`
- [其他从录制中发现的限制]
```

---

### Step 9：验证与输出摘要

```
✓ 分析完成

录制概览:
  - 3 个步骤，47 条请求，过滤后 12 条业务请求
  - 鉴权方式: Cookie（browser_cookie3 从 Edge/Chrome 读取，域名: jagile.jd.com）
  - 发现 3 条依赖链: env→app, app→branch, app→deploy

CLI 命令映射（录制请求 → 生成命令）:
  STEP 1 GET  /api/env/list          → python env.py list-envs
  STEP 1 GET  /api/app/list          → python env.py list-apps --env-id ENV_ID
  STEP 2 GET  /api/branch/list       → python branch.py list-branches --app-id APP_ID
  STEP 3 POST /api/deploy/create     → python deploy.py create --app-id APP_ID --branch BRANCH

生成文件:
  auth.py       — cookie 读取与缓存（browser_cookie3）
  env.py        — list_envs(), list_apps(env_id)
  branch.py     — list_branches(app_id)
  deploy.py     — create_deploy(app_id, branch)
  run_all.py    — 完整流程串联
  SKILL.md
  README.md

快速测试:
  python env.py list-envs
```

---

## 注意事项

- **不要硬编码 cookie 值或 token**，始终通过 `auth_headers()` 动态读取
- **录制请求 → CLI 命令的映射必须在 SKILL.md 的表格中体现**，方便 agent 按需调用单个命令
- **如果某步骤请求数 > 20**，只为 POST/PUT/DELETE 和关键 GET（有业务依赖的）生成命令，其余做注释说明
- **`browser_cookie3` 的依赖**：需要 `pip install browser_cookie3`；在 macOS 上读取 Chrome 可能需要授权 Terminal 访问 Keychain
- **生成的代码必须能直接运行**，不允许出现 `TODO` 或 `YOUR_VALUE_HERE` 占位符
