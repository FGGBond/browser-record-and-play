# browser-record-and-play: Skill Forge

## 用途

分析 `bh-record` 产出的 JSON 录制文件，提取关键 API 调用链，生成：
1. 一套可执行的 Python CLI 脚本（`cli/` 目录）
2. 一份指导 AI agent 使用这套 CLI 的 skill 文档（`SKILL.md`）

---

## 调用方式

用户提供录制文件路径，你完成以下全部步骤：

```
请帮我分析这个录制文件并生成工具：
录制文件: ./recordings/recording_20260626_143022.json
输出目录: ./my-tool/
```

---

## 执行步骤

### Step 1：读取并理解录制文件

读取 JSON 文件，理解整体结构：

```python
import json
data = json.loads(open("recording_xxx.json").read())
# data["steps"]  — 每个用户标注的步骤
# data["tail"]   — 最后一步之后的请求
# 每个 step 包含 description + requests[]
```

对每个 step，输出简要摘要：
- step 描述是什么
- 包含几条请求
- 其中 POST/PUT/DELETE 各几条（这些是状态变更操作，优先分析）

---

### Step 2：识别关键请求

对全部请求按以下规则过滤，只保留**业务请求**：

**排除**：
- URL 包含 `.png` `.jpg` `.svg` `.css` `.js` `.woff` `.ico`
- URL 包含 `analytics` `track` `beacon` `telemetry` `sentry` `datadog` `gtm` `ga.` `fbq`
- method 为 `GET` 且 URL 包含静态资源路径（`/static/` `/assets/` `/dist/`）
- 响应状态码 >= 400（错误请求，不代表正常流程）

**保留并重点分析**：
- 所有 `POST` / `PUT` / `PATCH` / `DELETE` 请求
- 返回 JSON 且包含 token/id/list 等业务字段的 `GET` 请求

---

### Step 3：分析请求依赖关系

检查请求之间的数据依赖：

1. **Token 传递**：step A 的响应体中的某个字段值，出现在了 step B 的请求头或请求体中
2. **ID 传递**：step A 返回的 `id`/`xxx_id` 字段，成为 step B 的路径参数或 body 参数
3. **Cookie/Session**：是否依赖登录 cookie（检查请求头中是否有 `Cookie:` 或 `Authorization:`）

输出依赖图：
```
STEP 1 [登录] POST /api/auth/login
  └─ 响应: token="eyJ..."
     ↓ 被 STEP 2、3、4 的 Authorization header 使用

STEP 2 [查询列表] GET /api/items
  └─ 响应: items[0].id="abc123"
     ↓ 被 STEP 3 的路径参数使用
```

---

### Step 4：识别动态参数

找出请求中需要参数化的字段（不能硬编码）：

- **时间戳**：值为当前时间附近的数字
- **随机 ID / nonce**：每次都不同的字符串（UUID、随机数）
- **CSRF token**：来自 cookie 或页面 meta 标签
- **用户输入**：在用户描述中提到的搜索词、名称等

对每个动态参数标注：
- 参数名
- 来源（从哪个响应获取 / 用户输入 / 运行时生成）
- 如何获取（代码示例）

---

### Step 5：生成 CLI 工具

在 `{output_dir}/cli/` 下生成 Python 脚本，每个 step 对应一个函数，使用 `httpx` 发起请求：

**文件结构：**
```
{output_dir}/
├── cli/
│   ├── __init__.py
│   ├── auth.py          # 认证相关（登录、token 刷新）
│   ├── {step_name}.py   # 每个业务步骤一个文件
│   └── run_all.py       # 串联所有步骤的完整流程
├── SKILL.md             # 使用说明（Step 6 生成）
└── README.md            # 人类可读的安装和使用说明
```

**代码规范：**
- 使用 `httpx` 同步客户端（`import httpx`）
- Cookie 使用 `httpx.Client` 的 `cookies` 参数持久化
- 动态参数通过函数参数传入，不硬编码
- 请求头完整复制录制中的 headers（去掉 `content-length`、`host`）
- 每个函数返回响应的 JSON 或文本，调用方决定如何使用

**示例输出（auth.py）：**
```python
import httpx

def login(username: str, password: str) -> dict:
    """登录并返回 token。对应录制 STEP 1: 输入账号密码并登录"""
    resp = httpx.post(
        "https://example.com/api/auth/login",
        json={"username": username, "password": password},
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()  # {"token": "eyJ..."}
```

---

### Step 6：生成 SKILL.md

在 `{output_dir}/SKILL.md` 写一份供 AI agent 使用的技能文档：

```markdown
# {工具名} Skill

## 用途
[一句话描述这套工具能做什么]

## 前提条件
- Python 3.11+
- httpx: pip install httpx
- [其他依赖]
- [需要的环境变量或配置]

## 工具清单

| 脚本 | 函数 | 用途 | 必填参数 |
|------|------|------|---------|
| cli/auth.py | login(username, password) | 登录获取 token | username, password |
| cli/search.py | search_items(keyword, token) | 搜索商品列表 | keyword, token |

## 完整流程示例

\`\`\`python
from cli.auth import login
from cli.search import search_items
from cli.cart import add_to_cart

# Step 1: 登录
result = login("user@example.com", "password")
token = result["token"]

# Step 2: 搜索
items = search_items("iPhone 15", token)
item_id = items[0]["id"]

# Step 3: 加购
add_to_cart(item_id, token)
\`\`\`

## 注意事项
- token 有效期约 1 小时，过期需重新调用 login()
- [其他限制]
```

---

### Step 7：验证与输出摘要

生成完毕后输出：

```
✓ 分析完成

录制概览:
  - 3 个步骤，47 条业务请求
  - 认证方式: Bearer Token（来自 POST /api/auth/login）
  - 发现 2 个动态参数: timestamp, csrf_token

生成文件:
  - cli/auth.py         (login, refresh_token)
  - cli/search.py       (search_items)
  - cli/cart.py         (add_to_cart, get_cart)
  - cli/run_all.py      (完整流程)
  - SKILL.md
  - README.md

运行示例:
  python cli/run_all.py --username user@example.com --password xxx
```

---

## 注意事项

- **不要硬编码密码或 token**，改用函数参数或环境变量 `os.environ.get("TOKEN")`
- **响应体可能很大**，只需提取依赖字段，不必完整复制到 SKILL.md
- **如果某步骤请求数 > 20**，只分析 POST/PUT/DELETE，GET 请求仅做摘要
- **如果用户描述含糊**（如"点了一下"），根据请求内容推断实际业务含义
- 生成的代码需要能**直接运行**，不能有未填写的占位符
