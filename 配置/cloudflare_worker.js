// ============================================================
// 速影 - Cloudflare Worker 远程链接中转
// 部署步骤:
//   1. 登录 https://dash.cloudflare.com
//   2. 左侧菜单 → Workers 和 Pages → 创建应用程序 → 创建 Worker
//   3. 点"编辑代码", 把本文件内容粘贴进去, 点部署
//   4. 回到 Worker 设置页 → KV 命名空间绑定 → 变量名填 LINKS
//      → 新建一个 KV 命名空间并绑定
//   5. 设置 → 变量 → 添加环境变量 WORKER_SECRET, 值自己设一个密码
//   6. 记下 Worker 的 URL (形如 https://xxx.workers.dev)
//   7. 填入速影 config.json 的 listener_worker_url 字段
//   8. 把 WORKER_SECRET 填入 config.json 的 listener_secret 字段
// ============================================================

export default {
  async fetch(request, env) {
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

    // ---------- API: 提交链接 (手机 → 云端) ----------
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

      return jsonResponse({ ok: true, id: entry.id });
    }

    // ---------- API: 速影轮询取链接 ----------
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

// ---- 工具函数 ----

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
// 从 URL 参数读取密钥 (首次使用时在地址栏加 ?s=你的密钥)
// 也可以直接在下方填入密钥:
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
      st.textContent='提交成功, 电脑端将自动生成视频';
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
