import http from 'node:http';
import { randomUUID } from 'node:crypto';

const PORT = Number(process.env.PORT || 8080);
const TOOLBOX = process.env.TOOLBOX_URL || `http://mock-daytona:${PORT}/toolbox`;
const READING_DIR = '/work/reading';

const SCAN = {
  schema_version: 'futureagi.agent-scan.v1',
  status: 'found',
  commit_sha: null,
  truncated: false,
  error: null,
  agents: [{
    name: 'support_agent',
    subfolder: '',
    entry: 'main.py',
    framework: { name: 'langgraph', version: null, label: null },
    confidence: 'high',
    evidence: ['main.py'],
    env_names: ['ACME_API_KEY'],
    estimates: { tools: 0, prompt_files: 0 },
  }],
};

const AGENT = {
  sdk_version: 'e2e-mock',
  agent: {
    schema_version: '1.0',
    type: 'chat',
    kind: 'source',
    name: 'support_agent',
    provider_ref: null,
    behaviour: [{ id: 'main', shape: 'external', instructions: 'You answer support questions.',
      tools: [], transitions: [] }],
    tools: [],
    model: null,
    voice: null,
    telephony: [],
    supports: [],
    direction_answer: null,
    gaps: ['flow_not_described'],
    instrumentation: 'traceai',
    extension: {},
    setup_hash: `sh1:${'e'.repeat(64)}`,
  },
};

const FILES = {
  [`${READING_DIR}/agents.json`]: SCAN,
  [`${READING_DIR}/agent.json`]: AGENT,
  [`${READING_DIR}/contract.json`]: {},
  [`${READING_DIR}/cost.json`]: {},
};

const sandboxes = new Map();
const calls = [];

function reply(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}

function sandbox(id, labels = {}, state = 'started') {
  return {
    id, organizationId: 'e2e-org', name: id, user: 'svc-control', env: {}, labels, public: false,
    networkBlockAll: false, target: 'e2e', cpu: 1, gpu: 0, memory: 1, disk: 1, state,
    toolboxProxyUrl: TOOLBOX,
  };
}

function multipart(paths) {
  const boundary = `e2e-${randomUUID()}`;
  const parts = paths.map((path) => {
    const content = FILES[path];
    if (content === undefined) {
      return `--${boundary}\r\nContent-Disposition: form-data; name="error"; filename="${path}"\r\n`
        + 'Content-Type: application/json\r\n\r\n'
        + JSON.stringify({ message: `no such file: ${path}`, statusCode: 404, code: 'FILE_NOT_FOUND' }) + '\r\n';
    }
    return `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${path}"\r\n`
      + 'Content-Type: application/octet-stream\r\n\r\n' + JSON.stringify(content) + '\r\n';
  });
  return { boundary, body: Buffer.from(parts.join('') + `--${boundary}--\r\n`) };
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const path = url.pathname;
  calls.push(`${req.method} ${path}`);
  if (path === '/health') return reply(res, 200, { ok: true });
  if (path === '/calls' && req.method === 'GET') return reply(res, 200, { calls });

  if (path === '/api/sandbox' && req.method === 'POST') {
    const body = JSON.parse((await readBody(req)).toString() || '{}');
    const id = `e2e-${randomUUID()}`;
    sandboxes.set(id, body.labels || {});
    return reply(res, 200, sandbox(id, body.labels));
  }
  const control = path.match(/^\/api\/sandbox\/([^/]+)(\/toolbox-proxy-url)?$/);
  if (control) {
    const [, id, proxy] = control;
    if (proxy) return reply(res, 200, { url: TOOLBOX });
    if (req.method === 'DELETE') {
      sandboxes.delete(id);
      return reply(res, 200, sandbox(id, {}, 'destroyed'));
    }
    if (!sandboxes.has(id)) return reply(res, 404, { message: 'sandbox not found', statusCode: 404 });
    return reply(res, 200, sandbox(id, sandboxes.get(id)));
  }

  const toolbox = path.match(/^\/toolbox\/([^/]+)(\/.*)$/);
  if (toolbox) {
    const [, , route] = toolbox;
    if (route === '/files/bulk-upload' && req.method === 'POST') {
      await readBody(req);
      return reply(res, 200, {});
    }
    if (route === '/process/execute' && req.method === 'POST') {
      await readBody(req);
      return reply(res, 200, { exitCode: 0, result: '' });
    }
    if (route === '/files/bulk-download' && req.method === 'POST') {
      const { paths = [] } = JSON.parse((await readBody(req)).toString() || '{}');
      const { boundary, body } = multipart(paths);
      res.writeHead(200, { 'content-type': `multipart/form-data; boundary=${boundary}` });
      return res.end(body);
    }
  }
  return reply(res, 404, { message: `mock-daytona has no route ${req.method} ${path}`, statusCode: 404 });
});

server.listen(PORT, '0.0.0.0');
