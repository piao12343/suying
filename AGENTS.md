# 速影项目维护手册

给以后接手这个项目的 AI 助手看。这个项目是个人使用程序，目标是功能稳定、结构简单、不要过度设计。

---

## 1. 项目做什么

速影输入一个抖音链接，自动完成：

```text
提取旁白 -> AI 改写 -> 分镜 -> 搜索配图 -> TTS 配音 -> ffmpeg 合成 -> 发布抖音
```

目前有两种运行方式：

- 本地 GUI：`源码/gui.py`
- 云端处理：手机提交链接 -> Cloudflare Pages/Worker -> GitHub Actions -> `源码/cli_pipeline.py`

---

## 2. 当前目录结构

```text
suying-github/
├── .github/workflows/
│   └── suying.yml              # 云端主流程
├── 源码/
│   ├── gui.py                  # 本地桌面程序
│   ├── cli_pipeline.py         # 云端 CLI 流水线
│   ├── video_pipeline.py       # 分镜、配图、TTS、视频工具函数
│   ├── extract_narration.py    # 抖音链接解析、音频下载、语音识别
│   ├── publisher.py            # 抖音发布封装
│   ├── cloud_patch_sau.py      # 云端 patch social-auto-upload
│   ├── cloud_log_relay.py      # 云端日志写入 Worker
│   └── tools/
│       ├── refresh_cookies.py
│       ├── sync_cloud_settings.py
│       └── 刷新抖音Cookie并同步云端.bat
├── 配置/
│   ├── config_template.json    # 默认配置模板
│   ├── config.json             # 本地真实配置，gitignored
│   ├── ai生故事模板.txt         # AI 改写模板
│   ├── cloudflare_worker.js    # Cloudflare Worker
│   ├── wrangler.toml           # Worker 部署配置
│   ├── pages/                  # Cloudflare Pages 前端和代理
│   ├── cookies/                # 抖音 cookie，gitignored
│   ├── social-auto-upload/     # 运行时 clone/cache，gitignored
│   └── 账户信息.md              # 密钥记录，gitignored
├── 缓存/                       # Whisper 模型、临时缓存
├── 作品/                       # 生成视频输出
├── 启动速影.vbs
└── AGENTS.md
```

根目录尽量保持干净，工具脚本放 `源码/tools/`，配置类文件放 `配置/`。

---

## 3. 云端链路

```text
手机浏览器
  -> https://suying-link.pages.dev
  -> Pages _worker.js 代理 /api/submit
  -> Cloudflare Worker
  -> KV 存链接
  -> Worker 后台触发 GitHub Actions workflow_dispatch
  -> GitHub Actions 运行 cli_pipeline.py --poll
```

注意：

- 浏览器端尽量只访问 `suying-link.pages.dev`，不要让手机直接请求 `workers.dev`，这样国内网络更稳。
- Worker 仍保留 `/api/poll`，本地远程监听和 GitHub Actions 取链接会用到。
- 云端不靠 cron 轮询，Worker 收到链接后直接触发 GitHub Actions。

---

## 4. 本地 GUI 当前设计

主窗口：

- 一键生成
- 分步调试
- 本地远程监听开关

分步调试的“发布视频”页目前只保留：

- 抖音状态
- 扫码登录
- 刷新状态

设置窗口目前分三块：

- 本地监听：本地监听轮询间隔
- 发布配置：发布间隔、发布话题、AI 改写模板、保存并同步云端
- 抖音账号：抖音 Cookie 同步云端

本地配置是主配置。修改设置后先保存到 `配置/config.json`，再通过 `源码/tools/sync_cloud_settings.py` 同步到 GitHub Secrets。

---

## 5. 配置同步

`保存并同步云端` 当前同步：

- `pub_desc` -> `SUYING_PUB_DESC`
- `auto_publish_douyin` -> `SUYING_AUTO_PUBLISH`
- `publish_interval_minutes` -> `SUYING_PUBLISH_INTERVAL_MINUTES`
- `配置/ai生故事模板.txt` 全文 -> `SUYING_REWRITE_TEMPLATE_TEXT`

`抖音 Cookie 同步云端` 当前流程：

```text
源码/tools/刷新抖音Cookie并同步云端.bat
  -> 源码/tools/refresh_cookies.py
  -> 更新 配置/cookies/douyin_creator.json
  -> 写入 GitHub Secret: DOUYIN_COOKIES_JSON
```

目前不放进设置窗口的固定项：

- TTS 语速、声音
- 分镜数量
- 视频尺寸、fps
- 背景音乐
- 转场开关

这些基本固定，避免界面变复杂。

---

## 6. 关键文件

### `源码/gui.py`

本地 tkinter 界面。改设置窗口、按钮、日志、分步调试页面时主要看这里。

修改后至少执行：

```bash
python -m py_compile 源码/gui.py
```

### `源码/cli_pipeline.py`

云端主流水线。GitHub Actions 主要跑这个文件。

配置加载顺序：

```text
配置/config_template.json -> GitHub Secrets 的 SUYING_* 环境变量 -> cloud_settings.json 部分覆盖
```

### `源码/video_pipeline.py`

分镜、关键词、Pexels/百度配图、TTS、ffmpeg 工具函数。

### `源码/cloud_patch_sau.py`

GitHub Actions clone/cache `social-auto-upload` 后会运行这个补丁。它负责适配云端发布抖音：

- 上传后增加等待
- 封面上传后增加等待
- 处理云端封面弹窗遮挡
- 默认选择“无需添加自主声明”

这个文件最容易受上游 `social-auto-upload` 变化影响。修改后要尽量在新 clone 或缓存目录里验证 patch 是否还能应用。

### `配置/cloudflare_worker.js`

Worker 逻辑：

- 接收 `/api/submit`
- 存链接到 KV
- 写云端日志
- 触发 GitHub Actions
- 保留 `/api/poll`

### `配置/pages/`

Cloudflare Pages 前端。现在通过 Pages Functions/`_worker.js` 做同域代理，避免浏览器直连 `workers.dev`。

### `.github/workflows/suying.yml`

云端主 workflow。已做缓存：

- pip cache
- faster-whisper 模型
- Playwright 浏览器
- social-auto-upload

---

## 7. 常用操作

启动本地 GUI：

```bash
python 源码/gui.py
```

本地跑 CLI：

```bash
python 源码/cli_pipeline.py <抖音链接>
```

部署 Worker：

```bash
cd 配置
set CLOUDFLARE_API_TOKEN=<api_token>
wrangler deploy
```

部署 Pages：

```bash
set CLOUDFLARE_API_TOKEN=<api_token>
wrangler pages deploy 配置/pages --project-name suying-link --branch main
```

普通本地提交：

```bash
git add .
git commit -m "改了什么"
git push
```

如果本机 `git push` 连不上 GitHub，按项目旧记录使用 GitHub Git Data API 或 `gh api` 推送。

---

## 8. 重要注意事项

- 修改项目代码前先做 git 提交备份，除非用户明确说不用备份。
- 本地项目代码修改完成并验证后，默认要提交并 `git push origin master`，让 GitHub 云端仓库和本地保持一致。
- 如果修改了会被云端读取的配置或提示词，例如 `配置/ai生故事模板.txt`、发布话题、发布间隔等，除了提交推送代码，还要运行对应同步脚本同步 GitHub Secrets。
- Python 文件改完必须跑 `py_compile`，至少覆盖本次改动文件。
- 含中文文件要用 UTF-8；`.ini` 用 `utf-8-sig`。
- 不要为了“更优雅”大重构。这个项目优先稳定、实用、简单。
- Worker 必须优先用 `wrangler deploy` 部署；不要直接 REST API 上传 Worker 脚本，否则 KV 绑定容易丢。
- Cloudflare Pages 更新后需要重新部署，Direct Upload 不能简单 retry 旧部署。
- 云端日志时间统一按北京时间显示。
- 不要提交 `配置/config.json`、`配置/cookies/`、`配置/账户信息.md`。
- `social-auto-upload` 是外部库，补丁失效时优先看 workflow 里 `cloud_patch_sau.py` 的输出。

---

## 9. 常见修改入口

- 改手机提交页面：`配置/pages/`
- 改 Worker 接口/日志：`配置/cloudflare_worker.js`
- 改本地界面：`源码/gui.py`
- 改云端流水线：`源码/cli_pipeline.py`
- 改视频生成效果：`源码/video_pipeline.py` 或 `cli_pipeline.py` 的渲染步骤
- 改抖音发布兼容：`源码/cloud_patch_sau.py`
- 改配置同步：`源码/tools/sync_cloud_settings.py`
- 改 Cookie 登录同步：`源码/tools/refresh_cookies.py`

---

最后更新：2026-06-23
