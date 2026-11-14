import http from 'node:http';

const channel = { id: 'CE2EALERTS', name: 'e2e-alerts', is_private: false, is_member: true };
const messages = [];

function reply(res, status, body, headers = {}) {
  res.writeHead(status, { 'content-type': 'application/json', ...headers });
  res.end(JSON.stringify(body));
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost:8080');
  if (url.pathname === '/health') return reply(res, 200, { ok: true });
  if (url.pathname === '/oauth/v2/authorize') {
    const redirect = new URL(url.searchParams.get('redirect_uri'));
    redirect.searchParams.set('state', url.searchParams.get('state') || '');
    if (url.searchParams.get('deny') === '1') redirect.searchParams.set('error', 'access_denied');
    else redirect.searchParams.set('code', 'e2e-authorized');
    res.writeHead(302, { location: redirect.toString() });
    return res.end();
  }
  if (url.pathname === '/api/oauth.v2.access') {
    return reply(res, 200, {
      ok: true, access_token: 'xoxb-e2e-bot', token_type: 'bot',
      scope: 'channels:read,channels:join,groups:read,chat:write',
      team: { id: 'TE2E', name: 'E2E Slack Workspace' }, bot_user_id: 'UE2EBOT',
    });
  }
  if (url.pathname === '/api/auth.test') {
    return reply(res, 200, { ok: true, team_id: 'TE2E', team: 'E2E Slack Workspace', user_id: 'UE2EBOT' });
  }
  if (url.pathname === '/api/conversations.list') {
    return reply(res, 200, { ok: true, channels: [channel], response_metadata: { next_cursor: '' } });
  }
  if (url.pathname === '/api/conversations.info' || url.pathname === '/api/conversations.join') {
    return reply(res, 200, { ok: true, channel });
  }
  if (url.pathname === '/api/chat.postMessage' && req.method === 'POST') {
    let data = '';
    for await (const chunk of req) data += chunk;
    const body = JSON.parse(data);
    const ts = `${Date.now() / 1000}`;
    messages.push({ ...body, ts });
    return reply(res, 200, { ok: true, channel: body.channel, ts });
  }
  if (url.pathname === '/messages' && req.method === 'GET') {
    return reply(res, 200, { messages });
  }
  return reply(res, 404, { ok: false, error: 'not_found' });
});

server.listen(8080, '0.0.0.0');
