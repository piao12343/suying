const WORKER_ORIGIN = 'https://suying.313833815.workers.dev';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

  if (url.pathname.startsWith('/api/') || url.pathname === '/log') {
      try {
        return await proxyToWorker(request, url);
      } catch (e) {
        return jsonResponse({ ok: false, error: '提交服务暂时不可用' }, 502);
      }
    }

    return env.ASSETS.fetch(request);
  },
};

async function proxyToWorker(request, url) {
  const targetUrl = new URL(url.pathname + url.search, WORKER_ORIGIN);
  const proxyRequest = new Request(targetUrl.toString(), request);
  const response = await fetch(proxyRequest);

  if (url.pathname === '/api/submit' && request.method === 'POST') {
    return rewriteSubmitResponse(response);
  }

  return response;
}

async function rewriteSubmitResponse(response) {
  const contentType = response.headers.get('Content-Type') || '';
  if (!contentType.includes('application/json')) {
    return response;
  }

  const data = await response.json();
  if (data.logUrl && typeof data.logUrl === 'string') {
    const logUrl = new URL(data.logUrl, WORKER_ORIGIN);
    data.logUrl = logUrl.pathname + logUrl.search;
  }

  return jsonResponse(data, response.status);
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
