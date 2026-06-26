# bh-record Skill

## 用途

录制用户在浏览器中的操作（网络请求 + 步骤描述），产出带标注的 JSON 文件，供后续 AI 分析生成 CLI 工具。

---

## 前提条件

1. **开启浏览器远程调试**（每次启动浏览器时带参数）：
   ```bash
   # macOS Chrome
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222

   # macOS Edge
   /Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge --remote-debugging-port=9222
   ```

2. **安装工具**：
   ```bash
   pip install browser-record-and-play
   ```

---

## 录制操作

```bash
# 默认保存到 ~/.browser-harness/recordings/
bh-record

# 指定保存目录
bh-record --output ./recordings/
bh-record -o /tmp/my-session/
```

---

## 交互说明

启动后终端底部会显示实时状态栏：

```
  [live] 47 requests  tabs: [jagile.jd.com], google.com
```

- `[live] N requests`：当前已录制的请求总数，实时更新
- `tabs:`：当前打开的标签页域名，方括号 `[domain]` 表示正在录制的标签页
- 输入描述后按 Enter，将当前积累的请求标记为一个步骤
- 输入 `done` 或 `stop` 结束录制

**录制示例：**
```
描述已完成的操作 >>>> 输入账号密码并登录

✓ MARKED STEP 1 @ 00:14 "输入账号密码并登录"
  └─ 5 requests recorded

描述已完成的操作 >>>> 搜索 iPhone 15

✓ MARKED STEP 2 @ 00:31 "搜索 iPhone 15"
  └─ 4 requests recorded

描述已完成的操作 >>>> done

Recording saved → ~/.browser-harness/recordings/recording_20260626_143022.json
  2 steps · 9 requests · 00:31
```

---

## 录制文件格式

```json
{
  "version": "1",
  "recorded_at": "2026-06-26T14:30:22Z",
  "duration_s": 49.0,
  "total_requests": 11,
  "steps": [
    {
      "index": 1,
      "description": "输入账号密码并登录",
      "start_at": 0.0,
      "end_at": 14.2,
      "requests": [
        {
          "request_id": "...",
          "method": "POST",
          "url": "https://example.com/api/auth/login",
          "request_headers": { "Content-Type": "application/json" },
          "request_body": "{\"username\":\"...\"}",
          "response_status": 200,
          "response_headers": { "content-type": "application/json" },
          "response_body": "{\"token\":\"eyJ...\"}"
        }
      ]
    }
  ],
  "tail": {
    "description": null,
    "requests": []
  }
}
```

---

## 下一步：分析录制文件

录制完成后，参考 `SKILL-FORGE.md` 指导 AI 分析录制文件，生成可执行的 CLI 工具和对应的 SKILL 文档。

```
请按照 SKILL-FORGE.md 的步骤，分析这个录制文件：
录制文件: ~/.browser-harness/recordings/recording_20260626_143022.json
输出目录: ./my-api-tool/
```
