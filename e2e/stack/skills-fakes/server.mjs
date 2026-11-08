// Test-only transport. Unknown scenarios/routes fail closed; nothing proxies the internet.
import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const hash = value => createHash('sha256').update(value).digest('hex');
const MAX_BODY = 15 * 1024 * 1024;
const MAX_LEDGER = 512;
const MAX_SCENARIOS = 128;
const TTL = 60 * 60 * 1000;
const CLOCK_START = 1893456000; // 2030-01-01T00:00:00Z; scoped to GitHub policy only
const MAX_CLOCK_ADVANCE = 7 * 24 * 3600;
const namespacePattern = /^e2e-skills-[a-z0-9-]{8,80}$/;
const json = (res, status, value) => {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(value));
};

function validateRecording(recording) {
  const url = new URL(recording.url);
  if (url.protocol !== 'https:' || url.port || url.username || url.password ||
      !['api.github.com', 'codeload.github.com'].includes(url.hostname) ||
      url.search || url.hash || !Number.isInteger(recording.status) ||
      recording.status < 200 || recording.status > 599 ||
      typeof recording.body_base64 !== 'string' ||
      Buffer.from(recording.body_base64, 'base64').length > 10 * 1024 * 1024) {
    throw new Error('invalid GitHub recording');
  }
}

function validateScenario(body) {
  if (!namespacePattern.test(body.namespace) || !Array.isArray(body.rules) || body.rules.length > 100 ||
      !Array.isArray(body.github) || body.github.length > 30) throw new Error('invalid scenario');
  for (const rule of body.rules) {
    if (!rule.match || typeof rule.match.request !== 'string' || rule.match.request.length > 16384 ||
        !Number.isInteger(rule.match.turn) || rule.match.turn < 0 || rule.match.turn > 6 ||
        (rule.delay_ms !== undefined && (!Number.isInteger(rule.delay_ms) || rule.delay_ms < 0 || rule.delay_ms > 5000)) ||
        (rule.status !== undefined && ![200, 401, 429, 500].includes(rule.status)) ||
        (rule.match.tool_text !== undefined && typeof rule.match.tool_text !== 'string') ||
        (!rule.response && !rule.status)) throw new Error('invalid response rule');
  }
  body.github.forEach(validateRecording);
  // A new incarnation never sees cached bytes/counters left by a disposed test or restart.
  return { ...structuredClone(body), ledger: [], clock: CLOCK_START, clockNamespace: `e2e-skills-${randomUUID()}`,
    clockEvents: [], created: Date.now() };
}

function requestInput(messages) {
  const user = messages.find(message => message.role === 'user');
  if (typeof user?.content !== 'string') throw new Error('missing user content');
  // runner.py: the fixed wrapper precedes JSON {request, fixtures}.
  const begin = user.content.indexOf('{');
  return JSON.parse(user.content.slice(begin)).request;
}

function expandResponse(response, messages) {
  const system = messages.find(message => message.role === 'system')?.content;
  const marker = 'Available skills:\n';
  let target;
  if (typeof system === 'string' && system.includes(marker)) {
    const inventory = JSON.parse(system.slice(system.indexOf(marker) + marker.length));
    if (inventory.length === 1) target = inventory[0].id;
  }
  const expand = value => {
    if (value === '$skill_id') {
      if (!target) throw new Error('template requires exactly one available skill');
      return target;
    }
    if (Array.isArray(value)) return value.map(expand);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, expand(item)]));
    return value;
  };
  const result = expand(response);
  for (const call of result.message?.tool_calls ?? []) {
    if (typeof call.function.arguments === 'object') call.function.arguments = JSON.stringify(call.function.arguments);
  }
  return result;
}

export function createFixtureHandler() {
  const scenarios = new Map();
  return async (req, res) => {
    try {
      let bytes = 0;
      const chunks = [];
      for await (const chunk of req) {
        bytes += chunk.length;
        if (bytes > MAX_BODY) return json(res, 413, { error: 'fixture body too large' });
        chunks.push(chunk);
      }
      const raw = Buffer.concat(chunks);
      const body = raw.length ? JSON.parse(raw.toString('utf8')) : {};
      const url = new URL(req.url, 'http://fixture.invalid');
      for (const [key, value] of scenarios) if (Date.now() - value.created > TTL) scenarios.delete(key);
      if (req.method === 'GET' && url.pathname === '/health') return json(res, 200, { fixture: 'skills-v11', schema: 1 });
      if (req.method === 'POST' && url.pathname === '/scenarios') {
        if (scenarios.size >= MAX_SCENARIOS) return json(res, 429, { error: 'fixture capacity reached' });
        const scenario = validateScenario(body);
        if (scenarios.has(scenario.namespace)) return json(res, 409, { error: 'scenario already registered' });
        // A recording belongs to one namespace. No shared URLs or cross-test overwrite.
        for (const existing of scenarios.values()) {
          if (scenario.github.some(row => existing.github.some(other => row.url === other.url))) {
            return json(res, 409, { error: 'recording URL already owned' });
          }
        }
        scenarios.set(scenario.namespace, scenario);
        return json(res, 201, { namespace: scenario.namespace, fixture_sha256: hash(raw) });
      }
      if (req.method === 'POST' && url.pathname === '/github/clock') {
        if (typeof body.repo !== 'string' || !/^synthetic\/[a-z0-9-]+$/.test(body.repo)) {
          return json(res, 400, { error: 'expected a synthetic repository' });
        }
        const metadataUrl = `https://api.github.com/repos/${body.repo}`;
        const owners = [...scenarios.values()].filter(scenario => scenario.github.some(row => row.url === metadataUrl));
        if (owners.length !== 1) return json(res, 404, { error: 'unrecorded repository clock' });
        return json(res, 200, { namespace: owners[0].clockNamespace, now: owners[0].clock });
      }
      const match = url.pathname.match(/^\/scenarios\/(e2e-skills-[a-z0-9-]+)(\/github|\/clock)?$/);
      if (match) {
        const scenario = scenarios.get(match[1]);
        if (!scenario) return json(res, 404, { error: 'unknown scenario' });
        if (req.method === 'DELETE' && !match[2]) {
          scenarios.delete(match[1]);
          return json(res, 200, { deleted: true });
        }
        if (req.method === 'GET' && !match[2]) return json(res, 200, { ledger: scenario.ledger, clock: scenario.clock, clockEvents: scenario.clockEvents });
        if (req.method === 'POST' && match[2] === '/clock') {
          if (Object.keys(body).length !== 1 || !Number.isInteger(body.advance_seconds) || body.advance_seconds <= 0 ||
              scenario.clock + body.advance_seconds > CLOCK_START + MAX_CLOCK_ADVANCE || scenario.clockEvents.length >= MAX_LEDGER) {
            return json(res, 400, { error: 'expected a bounded positive clock advance' });
          }
          scenario.clock += body.advance_seconds;
          scenario.clockEvents.push({ advance_seconds: body.advance_seconds, now: scenario.clock });
          return json(res, 200, { now: scenario.clock });
        }
        if (req.method === 'PUT' && match[2] === '/github') {
          validateRecording(body);
          const index = scenario.github.findIndex(row => row.url === body.url);
          if (index < 0) return json(res, 404, { error: 'recording not owned' });
          scenario.github[index] = structuredClone(body);
          return json(res, 200, { updated: true });
        }
        return json(res, 405, { error: 'method not allowed' });
      }
      if (req.method === 'POST' && url.pathname === '/github') {
        const owners = [...scenarios.values()].filter(scenario => scenario.github.some(row => row.url === body.url));
        if (owners.length !== 1) return json(res, 404, { error: 'unrecorded GitHub URL; live egress forbidden' });
        const scenario = owners[0];
        if (scenario.ledger.length >= MAX_LEDGER) return json(res, 429, { error: 'fixture ledger full' });
        const recording = scenario.github.find(row => row.url === body.url);
        scenario.ledger.push({ kind: 'github', url: body.url, status: recording.status, clock: scenario.clock,
          if_none_match: body.headers?.['If-None-Match'] ?? null,
          body_sha256: hash(Buffer.from(recording.body_base64, 'base64')), request_sha256: hash(raw) });
        return json(res, 200, recording);
      }
      if (req.method === 'POST' && url.pathname === '/v1/chat/completions') {
        const namespace = (req.headers.authorization ?? '').replace(/^Bearer /, '');
        const scenario = scenarios.get(namespace);
        if (!scenario) return json(res, 401, { error: { message: 'unknown synthetic credential', type: 'invalid_api_key' } });
        if (!Array.isArray(body.messages) || body.model !== 'gpt-4o-mini' || body.stream === true) {
          return json(res, 400, { error: { message: 'unsupported fixture request' } });
        }
        if (scenario.ledger.length >= MAX_LEDGER) return json(res, 429, { error: { message: 'fixture ledger full' } });
        const request = requestInput(body.messages);
        const turn = body.messages.filter(message => message.role === 'assistant').length;
        const toolText = body.messages.filter(message => message.role === 'tool').map(message => message.content).join('\n');
        const rules = scenario.rules.filter(rule => rule.match.request === request && rule.match.turn === turn &&
          (rule.match.tool_text === undefined || toolText.includes(rule.match.tool_text)));
        const receipt = { kind: 'model', request, turn, request_sha256: hash(raw),
          messages: body.messages, tools: body.tools ?? [], response_sha256: null, status: 409 };
        scenario.ledger.push(receipt);
        if (rules.length !== 1) return json(res, 409, { error: { message: 'expected exactly one fixture rule' } });
        const rule = rules[0];
        receipt.status = rule.status ?? 200;
        if (rule.delay_ms) await new Promise(resolve => setTimeout(resolve, rule.delay_ms));
        if (receipt.status !== 200) return json(res, receipt.status, { error: { message: 'synthetic provider failure', type: 'fixture_error' } });
        const result = expandResponse(rule.response, body.messages);
        const response = { id: `chatcmpl-${hash(raw).slice(0, 24)}`, object: 'chat.completion', created: 1,
          model: body.model, choices: [{ index: 0, message: result.message, finish_reason: result.finish_reason }],
          ...(result.omit_usage ? {} : { usage: { prompt_tokens: 17, completion_tokens: 9, total_tokens: 26 } }) };
        receipt.response_sha256 = hash(JSON.stringify(response));
        return json(res, 200, response);
      }
      return json(res, 404, { error: 'unrecorded fixture route' });
    } catch {
      if (!res.headersSent) return json(res, 400, { error: 'invalid fixture payload' });
      res.destroy();
    }
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  createServer(createFixtureHandler()).listen(Number(process.env.PORT ?? 8080));
}
