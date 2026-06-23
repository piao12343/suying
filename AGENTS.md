# 速影 (Suying) — 项目技术文档

> 给 AI 助手 (Codex / QoderWork / 其他) 的完整参考，用于理解和修改本项目。

---

## 1. 项目概述

**速影**是一个抖音短视频自动生成与发布系统。输入一个抖音视频链接，系统自动完成：提取旁白 → AI改写文案 → 生成分镜 → 搜索配图 → TTS配音 → ffmpeg合成视频 → 自动发布到抖音。

支持两种运行模式：
- **本地 GUI** (`源码/gui.py`)：桌面 tkinter 应用，一键生成 + 分步调试
- **云端 CLI** (`源码/cli_pipeline.py`)：GitHub Actions 上无头运行，通过 Cloudflare Worker 接收链接

---

## 2. 架构与数据流

```
手机浏览器
    │
    │ 提交抖音链接
    ▼
┌──────────────────────────┐
│  Cloudflare Pages        │  suying-link.pages.dev
│  (静态 HTML 页面)         │  前端提交页面
│                          │  直接 POST 到 Worker
└──────────┬───────────────┘
           │ POST /api/submit (含 link + secret)
           ▼
┌──────────────────────────┐
│  Cloudflare Worker       │  suying.313833815.workers.dev
│  (KV 存储链接队列)        │
│                          │  存链接到 KV → ctx.waitUntil 触发 GitHub Actions
└──────────┬───────────────┘
           │ POST /repos/{repo}/actions/workflows/suying.yml/dispatches
           ▼
┌──────────────────────────┐
│  GitHub Actions          │  .github/workflows/suying.yml
│  (ubuntu-latest + xvfb)  │
│                          │  1. poll Worker 取链接
│                          │  2. 运行 cli_pipeline.py --poll
│                          │  3. 处理视频 + 发布抖音
└──────────────────────────┘
```

### 视频处理流水线 (cli_pipeline.py)

```
输入: 抖音视频链接
  ↓
extract_narration.py — 下载音频 + faster-whisper 语音转文字
  ↓
AI改写 — OpenRouter API (google/gemma-4-31b-it:free) 改写成口播文案
  ↓
分镜拆分 — 按句子边界拆成约10个镜头
  ↓
Pexels 图片搜索 — AI提取关键词 → 搜索 → 本地关键词映射兜底 → 百度图片兜底
  ↓
Edge-TTS 配音 — zh-CN-YunjianNeural 语音 + 字级别时间戳
  ↓
ffmpeg 合成 — Ken Burns 动效 + 转场淡入淡出 + 字幕烧录 + 标题栏 + 竖屏/横屏封面
  ↓
publisher.py — social-auto-upload (Playwright) 自动发布到抖音创作者平台
  ↓
输出: 作品/{视频名}/ 目录 (含成品视频 + 所有过程文件)
```

---

## 3. 目录结构

```
suying-github/
├── .github/workflows/
│   ├── suying.yml              ← 主流水线 (poll + 处理 + 发布)
│   └── publish-only.yml        ← 单独发布 (从已有 artifact 发布)
├── 源码/
│   ├── cli_pipeline.py         ← CLI 入口 (云端/无头)
│   ├── video_pipeline.py       ← 视频生成核心逻辑
│   ├── extract_narration.py    ← 语音转文字
│   ├── publisher.py            ← 抖音发布 (调用 social-auto-upload)
│   ├── publish_only.py         ← 仅发布模式
│   ├── cloud_patch_sau.py      ← ★ 云端补丁: 修改 social-auto-upload 适配云端
│   ├── gui.py                  ← 桌面 GUI (tkinter)
│   └── tools/refresh_cookies.py
├── 配置/
│   ├── cloudflare_worker.js    ← Cloudflare Worker 源码
│   ├── wrangler.toml           ← Worker 部署配置
│   ├── config_template.json    ← 配置模板 (API key 留空)
│   ├── cloud_settings.json     ← 云端专用覆盖配置
│   ├── config.json             ← 本地实际配置 (gitignored)
│   ├── requirements.txt        ← Python 依赖
│   ├── ai生故事模板.txt         ← AI 改写 prompt 模板
│   ├── cookies/                ← 抖音登录 cookie (gitignored)
│   ├── 账户信息.md              ← 所有账户凭据 (gitignored)
│   └── social-auto-upload/     ← 第三方发布库 (运行时 clone, gitignored)
├── 作品/                       ← 生成的视频输出
├── 缓存/                       ← whisper 模型等缓存
├── 启动速影.vbs                 ← Windows 静默启动脚本
└── .gitignore
```

---

## 4. 关键文件详解

### 4.1 `源码/cloud_patch_sau.py` ★ 最常修改

这个脚本在 GitHub Actions 里 clone `social-auto-upload` 之后运行，对其进行猴子补丁，解决云端环境特有的问题：

- **步骤1**: 视频上传后额外等 5 秒（云端上传慢）
- **步骤2**: 封面图上传后等待从 3 秒加到 30 秒
- **步骤3**: 封面"完成"按钮 — 按行匹配，点击后 JS 强制移除弹窗

**步骤3 的核心问题**: 抖音创作者平台的封面弹窗在云端无法自然关闭（`.dy-creator-content-modal` 系列 DOM 元素遮挡发布按钮）。解决方案是点击"完成"后等 10 秒，检查弹窗是否还在，如果还在就用 JS `document.querySelectorAll().forEach(e => e.remove())` 移除 4 种遮挡元素：
- `.dy-creator-content-modal-wrap`
- `.dy-creator-content-modal-mask`
- `.dy-creator-content-modal`
- `.dy-creator-content-portal`

**备选方案**（已在注释中）: 跳过自定义封面，让抖音自动选推荐封面。

**注意事项**:
- social-auto-upload 上游会不定期更新，日志消息文本可能变化
- 步骤1/2 用字符串精确匹配，步骤3 用按行匹配（更健壮）
- 补丁静默失败时不会报错但会导致发布失败 — 检查 workflow 日志里的 `[FAIL]`

### 4.2 `配置/cloudflare_worker.js`

Cloudflare Worker 脚本，功能：
- `GET /` — 返回手机端 HTML 提交页面
- `POST /api/submit` — 接收链接，存入 KV，后台触发 GitHub Actions
- `GET /api/poll` — 轮询接口（保留兼容，取走并清空队列）

关键实现：
- `ctx.waitUntil(triggerGitHubWorkflow(env))` — 不阻塞响应，后台触发
- 环境变量: `WORKER_SECRET`（访问密码）、`GITHUB_TOKEN`、`GITHUB_REPO`
- KV 命名空间绑定: `env.LINKS`

### 4.3 `配置/wrangler.toml`

```toml
name = "suying"
account_id = "ceb1f7b1e844d8e45e6595bf5126d57a"
main = "cloudflare_worker.js"
compatibility_date = "2024-01-01"

[[kv_namespaces]]
binding = "LINKS"
id = "9b74bc087f58473099c529fa56456fed"
```

### 4.4 `.github/workflows/suying.yml`

主流水线，只有 `workflow_dispatch` 触发（无 cron），两阶段设计：
- **Phase 1（轻量轮询）**: checkout + Python → poll Worker 取链接 → 无链接则 early exit
- **Phase 2（完整流水线）**: 安装系统依赖 → pip install → clone social-auto-upload → 应用补丁 → 写 cookie → xvfb-run 运行 pipeline

关键环境变量通过 GitHub Secrets 注入（`SUYING_*`），`SUYING_PENDING_LINKS` 在 poll 步骤写入输出。

### 4.5 `源码/cli_pipeline.py`

CLI 入口，支持：
- `python cli_pipeline.py <url>` — 处理单个链接
- `python cli_pipeline.py --poll` — 从 `SUYING_PENDING_LINKS` 环境变量或 Worker poll 取链接

配置加载优先级: `config_template.json` → `SUYING_*` 环境变量覆盖 → `cloud_settings.json` 覆盖

---

## 5. 云端基础设施

### 5.1 Cloudflare Worker

| 项目 | 值 |
|------|-----|
| Worker 名称 | suying |
| URL | https://suying.313833815.workers.dev |
| Account ID | ceb1f7b1e844d8e45e6595bf5126d57a |
| KV Namespace ID | 9b74bc087f58473099c529fa56456fed |
| 访问密码 | wang5201314@ |
| 配置文件 | 配置/wrangler.toml + 配置/cloudflare_worker.js |

**Worker Secrets**（通过 wrangler 设置）:
- `WORKER_SECRET` — HTML 页面访问密码
- `GITHUB_TOKEN` — GitHub PAT (触发 workflow 用)
- `GITHUB_REPO` — 仓库名 `piao12343/suying`

### 5.2 Cloudflare Pages

| 项目 | 值 |
|------|-----|
| 项目名称 | suying-link |
| URL | https://suying-link.pages.dev |
| 用途 | 手机端提交页面（静态 HTML） |
| HTML 中的 fetch 目标 | `https://suying.313833815.workers.dev/api/submit`（直连 Worker） |

### 5.3 GitHub

| 项目 | 值 |
|------|-----|
| 仓库 | piao12343/suying |
| 默认分支 | master |
| 主 workflow | suying.yml |
| 辅 workflow | publish-only.yml |

### 5.4 凭据存放位置

所有凭据在 `配置/账户信息.md`（gitignored）。包括：
- GitHub Token
- OpenRouter API Key
- Pexels API Key
- Cloudflare API Token + Global API Key
- Worker 访问密码

---

## 6. 部署操作

### 6.1 部署 Worker 代码

```bash
cd 配置/
set CLOUDFLARE_API_TOKEN=<api_token>
wrangler deploy
```

**重要**: 必须用 wrangler 部署，不要用 Cloudflare REST API 直接上传脚本，否则 KV 命名空间绑定会丢失！wrangler.toml 里的 `[[kv_namespaces]]` 配置确保绑定保留。

### 6.2 设置 Worker Secrets

```bash
set CLOUDFLARE_API_TOKEN=<api_token>
echo piao12343/suying | wrangler secret put GITHUB_REPO --name suying
echo <github_token> | wrangler secret put GITHUB_TOKEN --name suying
```

### 6.3 部署 Pages 前端

```bash
set CLOUDFLARE_API_TOKEN=<api_token>
wrangler pages deploy <html目录> --project-name suying-link --branch main
```

Pages 项目的 HTML 直接调用 Worker 的 `/api/submit`，不需要 Pages Functions。

### 6.4 推送代码到 GitHub

本地修改代码后的普通提交流程:

```bash
cd D:\Personal\Desktop\suying-github
git add .
git commit -m "改了什么"
git push
```

git push 到 github.com:443 在本机被封。使用 GitHub Git Data API：

```python
# 通过 gh api 推送 (适用于被墙环境)
# 步骤: get HEAD sha → get tree sha → create blob → create tree → create commit → update ref
gh api repos/piao12343/suying/git/refs/heads/master --jq .object.sha
gh api repos/{repo}/git/blobs -X POST --input -  # base64 编码文件内容
gh api repos/{repo}/git/trees -X POST --input -
gh api repos/{repo}/git/commits -X POST --input -
gh api repos/{repo}/git/refs/heads/master -X PATCH --input -
```

### 6.5 部署 Cloudflare Pages 项目的环境变量

通过 Cloudflare REST API 更新:
```bash
curl -X PATCH "https://api.cloudflare.com/client/v4/accounts/{account_id}/pages/projects/suying-link" \
  -H "X-Auth-Email: <email>" -H "X-Auth-Key: <global_api_key>" \
  -H "Content-Type: application/json" \
  -d '{"deployment_configs":{"production":{"env_vars":{"WORKER_SECRET":{"type":"plain_text","value":"新密码"}}}}}'
```
更新后需要重新部署才能生效（Direct Upload 不支持 retry，需要新部署）。

---

## 7. GitHub Actions 环境

### Secrets 列表

| Secret 名 | 用途 |
|-----------|------|
| SUYING_OPENROUTER_API_KEY | OpenRouter AI API 密钥 |
| SUYING_PEXELS_API_KEY | Pexels 图片搜索 API 密钥 |
| SUYING_LISTENER_SECRET | Worker 访问密码 |
| SUYING_LISTENER_WORKER_URL | Worker URL |
| SUYING_PUSHPLUS_TOKEN | 微信推送通知 token |
| SUYING_TTS_VOICE | Edge-TTS 语音名 |
| SUYING_TTS_RATE | TTS 语速 |
| SUYING_OPENROUTER_MODEL | AI 模型名 |
| SUYING_PUB_DESC | 发布描述/标签 |
| SUYING_AUTO_PUBLISH | 是否自动发布 (true/false) |
| DOUYIN_COOKIES_JSON | 抖音登录 cookie JSON |

### 运行环境

- Ubuntu Latest + xvfb (`xvfb-run --auto-servernum --server-args="-screen 0 1280x960x24"`)
- Python 3.11
- Playwright + Chromium（用于 social-auto-upload 的浏览器自动化）
- Patchright + Chromium（social-auto-upload 使用 patchright 而非 playwright）
- ffmpeg + fonts-noto-cjk（CJK 字体）

---

## 8. 已知问题与注意事项

### 8.1 抖音封面弹窗问题
云端环境中 `.dy-creator-content-modal` 系列 DOM 元素无法自然关闭，会遮挡发布按钮。通过 JS 强制移除解决。详见 `cloud_patch_sau.py`。

### 8.2 social-auto-upload 上游变更
该库不定期更新，日志消息文本会变化。`cloud_patch_sau.py` 的步骤1/2 用精确字符串匹配，可能因上游更新而失败（静默失败，不报错）。步骤3 用按行匹配更健壮。修改补丁时务必检查 social-auto-upload 的最新代码。

### 8.3 GitHub Actions cron 不可靠
GitHub Actions 的 cron 调度实际延迟 1-2.5 小时，不适合做定时轮询。已改为 Worker 直接触发 workflow_dispatch，完全去掉了 cron。

### 8.4 git push 被封
本机网络无法直接 push 到 github.com:443。所有代码推送必须通过 GitHub Git Data API (`gh api`) 完成。

### 8.5 Wrangler vs REST API 部署 Worker
通过 Cloudflare REST API 上传 Worker 脚本会丢失 KV 命名空间绑定。必须用 `wrangler deploy`（它会读取 wrangler.toml 里的绑定配置）。

### 8.6 Cloudflare API 认证
- Bearer Token (`cfut_...`)：用于 wrangler 和部分 API
- Global API Key (`cfk_...`) + X-Auth-Email + X-Auth-Key：用于 Pages 管理等 API
- 两种认证方式不完全通用，部分 API 只支持其中一种

### 8.7 Pages Direct Upload 不支持 retry
Cloudflare Pages 的 Direct Upload 部署不能用 retry 端点。要更新环境变量或代码，必须创建新部署。

### 8.8 f-string 陷阱
`cloud_patch_sau.py` 里生成 JS 代码时使用 Python f-string，需要注意：
- JS 的 `{}` 在 f-string 里要双写 `{{}}`
- 引号嵌套需要转义或用不同引号类型

---

## 9. 常见修改任务

### 修改 Worker 逻辑
1. 编辑 `配置/cloudflare_worker.js`
2. `cd 配置 && wrangler deploy`（需设 CLOUDFLARE_API_TOKEN）

### 修改云端补丁
1. 编辑 `源码/cloud_patch_sau.py`
2. 通过 Git Data API 推送到 GitHub
3. 手动触发 workflow 测试

### 修改提交页面 HTML
1. 准备好新的 index.html
2. `wrangler pages deploy <目录> --project-name suying-link`

### 修改视频生成逻辑
1. 编辑 `源码/` 下对应文件
2. 通过 Git Data API 推送
3. 手动触发 workflow 或提交链接测试

### 更换 AI 模型 / TTS 语音
1. 修改 GitHub Secrets（SUYING_OPENROUTER_MODEL / SUYING_TTS_VOICE）
2. 或修改 `配置/config_template.json` 的默认值

### 更新抖音 Cookie
1. 本地运行 `源码/tools/refresh_cookies.py` 扫码登录
2. 将 `配置/cookies/douyin_creator.json` 内容更新到 GitHub Secret `DOUYIN_COOKIES_JSON`

---

## 10. 第三方依赖

| 依赖 | 来源 | 用途 |
|------|------|------|
| social-auto-upload | github.com/dreammis/social-auto-upload | 抖音/快手/B站等视频发布 |
| faster-whisper | PyPI | 语音转文字 |
| edge-tts | PyPI | 微软 Edge TTS 语音合成 |
| patchright | PyPI | Playwright 反检测版 |
| OpenRouter | openrouter.ai | AI 改写 API |
| Pexels | pexels.com | 免费配图搜索 |
| PushPlus | pushplus.plus | 微信推送通知 |

---

## 11. 本地开发

### 启动 GUI
```bash
python 源码/gui.py
# 或双击 启动速影.vbs
```

### 本地运行 CLI
```bash
python 源码/cli_pipeline.py <抖音链接>
```

### 本地配置
`配置/config.json`（gitignored）包含本地 API key 和路径，优先级高于 config_template.json。

---

*文档最后更新: 2026-06-22*
