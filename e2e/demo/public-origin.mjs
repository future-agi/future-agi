// Recording only: serve the Standalone stack under public-looking HTTPS host names so nothing on
// camera (API host, install command) shows a loopback address. Not used by the E2E suite.
// Usage (stack host, as root for port 443): node demo/public-origin.mjs <cert.pem> <key.pem>
//   app.<domain> -> 127.0.0.1:FRONTEND_PORT (3100), api.<domain> -> 127.0.0.1:BACKEND_PORT (8100)
// The Host header is passed through unchanged (the backend allows any host); WebSocket upgrades are piped.
import { readFileSync } from 'node:fs';
import { createServer } from 'node:https';
import { request } from 'node:http';
import { connect } from 'node:net';

const [cert, key] = process.argv.slice(2);
const DOMAIN = process.env.DEMO_PUBLIC_DOMAIN ?? 'acme-support.example';
const upstreams = { [`app.${DOMAIN}`]: Number(process.env.FRONTEND_PORT ?? 3100), [`api.${DOMAIN}`]: Number(process.env.BACKEND_PORT ?? 8100) };
const portFor = (host) => upstreams[String(host ?? '').split(':')[0].toLowerCase()];

const server = createServer({ cert: readFileSync(cert), key: readFileSync(key) }, (req, res) => {
  const port = portFor(req.headers.host);
  if (!port) { res.writeHead(421).end('unknown host'); return; }
  const upstream = request({ host: '127.0.0.1', port, method: req.method, path: req.url,
    headers: { ...req.headers, 'x-forwarded-proto': 'https' } }, (up) => { res.writeHead(up.statusCode ?? 502, up.headers); up.pipe(res); });
  upstream.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end(); });
  req.pipe(upstream);
});
server.on('upgrade', (req, socket, head) => {
  const port = portFor(req.headers.host);
  if (!port) { socket.destroy(); return; }
  const up = connect(port, '127.0.0.1', () => {
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n${Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`).join('\r\n')}\r\n\r\n`);
    if (head?.length) up.write(head);
    up.pipe(socket); socket.pipe(up);
  });
  up.on('error', () => socket.destroy()); socket.on('error', () => up.destroy());
});
server.listen(Number(process.env.DEMO_PUBLIC_PORT ?? 443), '127.0.0.1', () => console.log('public origin proxy ready', Object.keys(upstreams).join(', ')));
