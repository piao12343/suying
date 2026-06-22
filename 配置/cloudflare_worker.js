// ============================================================
// 速影 - Cloudflare Worker 远程链接中转
// 提交链接后直接触发 GitHub Actions workflow, 无需等待轮询
//
// 环境变量:
//   WORKER_SECRET  - 提交页面的访问密钥
//   GITHUB_TOKEN   - GitHub PAT (需 actions:write 权限)
//   GITHUB_REPO    - GitHub 仓库, 如 piao12343/suying
// ============================================================

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const SECRET = env.WORKER_SECRET || '';

    // CORS 预检
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders() });
    }

    // ---------- 手机端提交页面 ----------
    if (url.pathname === '/' && request.method === 'GET') {
      return new Response(HTML_PAGE, {
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      });
    }

    // ---------- 临时日志查看页 ----------
    if (url.pathname === '/log' && request.method === 'GET') {
      const secret = url.searchParams.get('secret') || url.searchParams.get('s') || '';
      if (SECRET && secret !== SECRET) {
        return new Response('密码错误', { status: 403 });
      }
      return new Response(LOG_PAGE, {
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      });
    }

    // ---------- API: 提交链接 ----------
    if (url.pathname === '/api/submit' && request.method === 'POST') {
      const body = await request.json();
      const secret = body.secret || '';
      if (SECRET && secret !== SECRET) {
        return jsonResponse({ error: '密码错误' }, 403);
      }
      const link = (body.link || '').trim();
      if (!link) {
        return jsonResponse({ error: '链接不能为空' }, 400);
      }

      const entry = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        link: link,
        time: new Date().toISOString(),
      };

      // 追加到待处理队列
      const pendingRaw = await env.LINKS.get('pending');
      const pending = pendingRaw ? JSON.parse(pendingRaw) : [];
      pending.push(entry);
      await env.LINKS.put('pending', JSON.stringify(pending));

      await writeCurrentLog(env, {
        status: 'waiting',
        lines: [
          `[${new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}] 已收到链接, 等待 GitHub Actions 启动...`,
          link,
        ],
        reset: true,
      });

      // 后台触发 GitHub Actions workflow (不阻塞响应)
      ctx.waitUntil(triggerGitHubWorkflow(env));

      return jsonResponse({
        ok: true,
        id: entry.id,
        logUrl: `/log?s=${encodeURIComponent(secret)}`,
      });
    }

    // ---------- API: 写入临时日志 ----------
    if (url.pathname === '/api/log' && request.method === 'POST') {
      const body = await request.json();
      const secret = body.secret || '';
      if (SECRET && secret !== SECRET) {
        return jsonResponse({ error: '密码错误' }, 403);
      }
      await writeCurrentLog(env, {
        status: body.status || 'running',
        lines: Array.isArray(body.lines) ? body.lines : [],
        reset: Boolean(body.reset),
      });
      return jsonResponse({ ok: true });
    }

    // ---------- API: 读取临时日志 ----------
    if (url.pathname === '/api/log' && request.method === 'GET') {
      const secret = url.searchParams.get('secret') || url.searchParams.get('s') || '';
      if (SECRET && secret !== SECRET) {
        return jsonResponse({ error: '密码错误' }, 403);
      }
      const log = await readCurrentLog(env);
      return jsonResponse({ ok: true, ...log });
    }

    // ---------- API: 轮询取链接 (保留兼容, 不再作为主触发方式) ----------
    if (url.pathname === '/api/poll' && request.method === 'GET') {
      const secret = url.searchParams.get('secret') || '';
      if (SECRET && secret !== SECRET) {
        return jsonResponse({ error: '密码错误' }, 403);
      }

      const pendingRaw = await env.LINKS.get('pending');
      const pending = pendingRaw ? JSON.parse(pendingRaw) : [];

      // 取走全部, 清空队列
      await env.LINKS.put('pending', '[]');

      return jsonResponse({ links: pending });
    }

    return new Response('404', { status: 404 });
  },
};

// ---- 触发 GitHub Actions ----

async function triggerGitHubWorkflow(env) {
  const token = env.GITHUB_TOKEN;
  const repo = env.GITHUB_REPO;
  if (!token || !repo) {
    console.log('GitHub 触发跳过: 未配置 GITHUB_TOKEN 或 GITHUB_REPO');
    return;
  }
  try {
    const resp = await fetch(
      `https://api.github.com/repos/${repo}/actions/workflows/suying.yml/dispatches`,
      {
        method: 'POST',
        headers: {
          'Authorization': `token ${token}`,
          'Accept': 'application/vnd.github+json',
          'Content-Type': 'application/json',
          'User-Agent': 'suying-worker',
        },
        body: JSON.stringify({ ref: 'master' }),
      }
    );
    if (resp.ok) {
      console.log('GitHub workflow 触发成功');
    } else {
      const text = await resp.text();
      console.log(`GitHub workflow 触发失败: ${resp.status} ${text}`);
    }
  } catch (e) {
    console.log(`GitHub workflow 触发异常: ${e.message}`);
  }
}

// ---- 工具函数 ----

async function readCurrentLog(env) {
  const raw = await env.LINKS.get('debug_log_current');
  if (!raw) {
    return {
      status: 'idle',
      updatedAt: '',
      lines: ['暂无运行日志。提交链接后, 这里会显示云端运行状态。'],
    };
  }
  try {
    return JSON.parse(raw);
  } catch (_) {
    return {
      status: 'unknown',
      updatedAt: '',
      lines: ['日志读取失败。'],
    };
  }
}

async function writeCurrentLog(env, update) {
  const oldLog = update.reset ? { lines: [] } : await readCurrentLog(env);
  const incoming = (update.lines || []).map(redactLogLine);
  const lines = [...(oldLog.lines || []), ...incoming].slice(-500);
  const log = {
    status: update.status || oldLog.status || 'running',
    updatedAt: new Date().toISOString(),
    lines,
  };
  await env.LINKS.put('debug_log_current', JSON.stringify(log), { expirationTtl: 21600 });
}

function redactLogLine(line) {
  return String(line)
    .replace(/(token|secret|key|cookie|authorization)(["'\s:=]+)[^\s"']+/ig, '$1$2***')
    .slice(0, 2000);
}

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
    },
  });
}

// ---- 手机端 HTML 页面 ----

const HTML_PAGE = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>速影 - 提交链接</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:#f5f5f5;padding:40px 20px}
.card{max-width:400px;margin:0 auto;background:#fff;border-radius:16px;
  padding:32px 24px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
h1{font-size:22px;text-align:center;margin-bottom:24px;color:#333}
textarea{width:100%;height:70px;border:1px solid #ddd;border-radius:10px;
  padding:12px;font-size:15px;resize:none;outline:none}
textarea:focus{border-color:#4a90d9}
button{width:100%;margin-top:16px;padding:14px;border:none;border-radius:10px;
  background:#4a90d9;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
button:active{background:#3a7bc8}
#status{margin-top:20px;text-align:center;font-size:14px;color:#888;
  min-height:20px}
.success{color:#2ecc71!important}
.error{color:#e74c3c!important}
</style>
</head>
<body>
<div class="card">
  <h1>速影 - 提交抖音视频链接</h1>
  <textarea id="link" placeholder="在抖音中复制链接, 粘贴到这里"></textarea>
  <button onclick="submit()">提交并生成视频</button>
  <p id="status"></p>
</div>
<script>
const SECRET = new URLSearchParams(location.search).get('s') || '';

async function submit(){
  const link = document.getElementById('link').value.trim();
  const st = document.getElementById('status');
  if(!link){st.textContent='请先粘贴链接';st.className='error';return}
  st.textContent='提交中...';st.className='';
  try{
    const r = await fetch('/api/submit',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({link:link,secret:SECRET})
    });
    const d = await r.json();
    if(d.ok){
      if(d.logUrl){
        st.innerHTML='提交成功, 正在自动生成视频...<br><a href="'+d.logUrl+'">查看云端日志</a>';
      }else{
        st.textContent='提交成功, 正在自动生成视频...';
      }
      st.className='success';
      document.getElementById('link').value='';
    }else{
      st.textContent='提交失败: '+(d.error||'未知错误');
      st.className='error';
    }
  }catch(e){
    st.textContent='网络错误, 请重试';st.className='error';
  }
}
</script>
</body>
</html>`;

const LOG_PAGE = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>速影 - 云端日志</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#111;color:#eee;font-family:Consolas,Menlo,monospace}
.bar{position:sticky;top:0;background:#1d1d1d;border-bottom:1px solid #333;
  padding:10px 12px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.status{font-weight:700}
.meta{color:#aaa;font-size:13px;margin-left:8px}
pre{margin:0;padding:12px;white-space:pre-wrap;word-break:break-word;
  font-size:13px;line-height:1.45}
</style>
</head>
<body>
<div class="bar">
  <span class="status" id="status">加载中</span>
  <span class="meta" id="meta"></span>
</div>
<pre id="log">加载中...</pre>
<script>
const params = new URLSearchParams(location.search);
const secret = params.get('secret') || params.get('s') || '';
async function refreshLog(){
  try{
    const r = await fetch('/api/log?s=' + encodeURIComponent(secret));
    const d = await r.json();
    if(!d.ok){
      document.getElementById('status').textContent = '读取失败';
      document.getElementById('log').textContent = d.error || '未知错误';
      return;
    }
    document.getElementById('status').textContent = d.status || 'unknown';
    document.getElementById('meta').textContent = d.updatedAt ? ('最后更新: ' + new Date(d.updatedAt).toLocaleString()) : '';
    document.getElementById('log').textContent = (d.lines || []).join('\\n');
    window.scrollTo(0, document.body.scrollHeight);
  }catch(e){
    document.getElementById('status').textContent = '网络错误';
    document.getElementById('log').textContent = String(e);
  }
}
refreshLog();
setInterval(refreshLog, 4000);
</script>
</body>
</html>`;
