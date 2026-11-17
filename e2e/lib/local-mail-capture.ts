// Test-only AF_UNIX sink. No HTTP, mail provider, content logger or persistence.
// Stage A owns/mounts the fixed transport directory and qualifies UID/guards.
// J02-ADOPT-01: each call owns one fresh session, not the directory's nonce.
import { createServer, type Socket } from 'node:net';
import { chmod, lstat, realpath } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join } from 'node:path';

export const MAX_MAIL_FRAME = 256 * 1024;
const noncePattern = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const refused = () => new Error('mail capture refused'); // Never interpolate input.
// Process-lifetime, memory-only admission/tombstones. Reserve before the first
// await; release a path ONLY after a successful drained close. Failed sessions
// block that cell, and even moving sockets cannot replay a consumed nonce.
const occupiedPaths = new Set<string>();
const usedNonces = new Set<string>();
const bindings = new WeakMap<LocalMailCapture, { socketPath: string; nonce: string; appOrigin: string; recipients: string[] }>();
export function mailCaptureBinding(capture: LocalMailCapture) {
  const binding = bindings.get(capture);
  if (!binding) throw refused();
  return { ...binding, recipients: [...binding.recipients] };
}
export interface LocalMailCapture {
  cursor(): number;
  takeInvite(input: { recipient: string; after: number; timeoutMs: number }): Promise<{ href: string; messageSequence: number }>;
  clear(recipient: string): Promise<void>;
  close(): Promise<void>;
}

export function localAppOrigin(value: string): string {
  let url: URL;
  try { url = new URL(value); } catch { throw refused(); }
  if (!['http:', 'https:'].includes(url.protocol) || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash || url.pathname !== '/' || url.origin !== value)
    throw refused();
  return value;
}

// Source-bound tokenizer for the balanced HTML emitted by Django's invite_user,
// billing/base_billing and billing/_button templates at a0372df842d3/0f047cc94a.
// Not a browser or general HTML repair parser: ambiguous/malformed markup refuses.
// Never look for URLs in comments, tag text, arbitrary attributes or raw elements.
function renderedLinks(body: string, plain: boolean): string[] {
  const links: string[] = [];
  const stack: { tag: string; hidden: boolean; href?: string; label: boolean }[] = [];
  const voids = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
  const raw = new Set(['script', 'style', 'title', 'textarea', 'xmp', 'iframe', 'noembed', 'noframes']);
  const inert = new Set(['head', 'template', 'noscript', 'object', 'svg', 'math']);
  const supported = new Set([...voids, ...raw, ...inert, 'html', 'body', 'a', 'span', 'div', 'p',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em', 'b', 'i', 'u', 'small', 'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th']);
  const hiding = /(?:^|[;{])\s*(?:display\s*:\s*none|(?:content-)?visibility\s*:\s*(?:hidden|collapse)|opacity\s*:\s*0(?:[;\s!}]|$)|font-size\s*:\s*0(?:px|em|rem|%|[;\s!}]|$))/i;
  let offset = 0, unsafeStyles = false;
  while (offset < body.length) {
    if (body.startsWith('<!--', offset)) {
      const end = body.indexOf('-->', offset + 4);
      if (end < 0) throw refused();
      offset = end + 3; continue;
    }
    if (body[offset] !== '<') {
      const end = body.indexOf('<', offset), text = body.slice(offset, end < 0 ? body.length : end);
      if (!stack.at(-1)?.hidden) {
        if (plain) for (const match of text.matchAll(/[^\s<>"']*\/auth\/jwt\/invitation\/accept\/[^\s<>"']+/g)) links.push(match[0]);
        if (text.replace(/&(?:nbsp|#0*160|#x0*a0);/gi, ' ').trim()) {
          const anchor = stack.find(node => node.tag === 'a');
          if (anchor) anchor.label = true;
        }
      }
      offset += text.length; continue;
    }
    const tail = body.slice(offset);
    const doctype = /^<!doctype\s+html\s*>/i.exec(tail);
    if (doctype) { offset += doctype[0].length; continue; }
    const close = /^<\/([a-z][a-z0-9:-]*)\s*>/i.exec(tail);
    if (close) {
      const node = stack.pop();
      if (!node || node.tag !== close[1].toLowerCase()) throw refused();
      if (!plain && !node.hidden && node.href && node.label) links.push(node.href);
      offset += close[0].length; continue;
    }
    const open = /^<([a-z][a-z0-9:-]*)\b((?:[^<>"']|"[^"]*"|'[^']*')*)>/i.exec(tail);
    if (!open) throw refused();
    const tag = open[1].toLowerCase(), attrs = new Map<string, string>();
    if (!supported.has(tag)) throw refused();
    const attrText = open[2].replace(/\s*\/\s*$/, '');
    const attr = /\s+([^\s=\/<>'"]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/y;
    let at = 0;
    while (at < attrText.length && attrText.slice(at).trim()) {
      attr.lastIndex = at;
      const match = attr.exec(attrText);
      if (!match || attrs.has(match[1].toLowerCase())) throw refused();
      attrs.set(match[1].toLowerCase(), match[2] ?? match[3] ?? match[4] ?? '');
      at = attr.lastIndex;
    }
    offset += open[0].length;
    // Styles with escapes/entities cannot establish visibility in this subset.
    const style = attrs.get('style') ?? '';
    const hidden = !!stack.at(-1)?.hidden || inert.has(tag) || attrs.has('hidden') || attrs.has('inert') ||
      attrs.get('aria-hidden')?.toLowerCase() === 'true' || hiding.test(style) || /[\\&]/.test(style);
    if (tag === 'link' && /stylesheet/i.test(attrs.get('rel') ?? '')) unsafeStyles = true;
    if (raw.has(tag)) {
      const end = new RegExp(`</${tag}\\s*>`, 'ig'); end.lastIndex = offset;
      const match = end.exec(body);
      if (!match) throw refused();
      if (tag === 'style' && (hiding.test(body.slice(offset, match.index)) || /[\\&]|@import/i.test(body.slice(offset, match.index)))) unsafeStyles = true;
      offset = end.lastIndex; continue;
    }
    if (voids.has(tag)) continue;
    if (/\/\s*$/.test(open[2]) || stack.length >= 128 || (tag === 'a' && stack.some(n => n.tag === 'a'))) throw refused();
    stack.push({ tag, hidden, href: tag === 'a' ? attrs.get('href') : undefined, label: false });
  }
  if (stack.length) throw refused();
  return !plain && unsafeStyles ? [] : links;
}

function inviteHref(envelope: unknown, recipients: Set<string>, origin: string) {
  if (!envelope || typeof envelope !== 'object') throw refused();
  const e = envelope as Record<string, unknown>;
  if (!Array.isArray(e.to) || e.to.length !== 1 || typeof e.to[0] !== 'string' || !recipients.has(e.to[0]) ||
      !Array.isArray(e.cc) || e.cc.length || !Array.isArray(e.bcc) || e.bcc.length ||
      !['from', 'subject', 'text', 'html'].every(k => typeof e[k] === 'string') ||
      !Array.isArray(e.replyTo) || e.replyTo.some(v => typeof v !== 'string')) throw refused();
  // Django emits this ASCII route. Usable HTML/text copies deduplicate exactly;
  // a URL merely mentioned in HTML is not an invitation href. Never rewrite it.
  const links = new Set<string>();
  for (const raw of [...renderedLinks(e.text as string, true), ...renderedLinks(e.html as string, false)]) {
    if (!raw.includes('/auth/jwt/invitation/accept/')) continue;
    let url: URL;
    try { url = new URL(raw); } catch { throw refused(); }
    if (url.origin !== origin || url.username || url.password || url.search || url.hash ||
        !/^\/auth\/jwt\/invitation\/accept\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+$/.test(url.pathname) ||
        url.href !== raw) throw refused();
    links.add(raw);
  }
  if (links.size !== 1) throw refused();
  return { recipient: e.to[0], href: [...links][0] };
}

export async function startLocalMailCapture(options: {
  socketPath: string; nonce: string; allowedRecipients: string[]; appOrigin: string;
}): Promise<LocalMailCapture> {
  const { socketPath, nonce } = options;
  const allowedRecipients = [...options.allowedRecipients];
  const origin = localAppOrigin(options.appOrigin), dir = dirname(socketPath);
  const transport = basename(dir);
  if (!noncePattern.test(nonce) || !isAbsolute(socketPath) || Buffer.byteLength(socketPath) > 103 ||
      basename(socketPath) !== 'mail.sock' || !transport.startsWith('e2e-mail-') || !noncePattern.test(transport.slice(9)) ||
      socketPath !== join(dir, 'mail.sock') || allowedRecipients.length < 1 || allowedRecipients.length > 32 ||
      new Set(allowedRecipients).size !== allowedRecipients.length ||
      allowedRecipients.some(v => !new RegExp(`^e2e-${nonce}-[a-z0-9-]+@example\\.invalid$`).test(v)) ||
      occupiedPaths.has(socketPath) || usedNonces.has(nonce)) throw refused();
  occupiedPaths.add(socketPath); usedNonces.add(nonce);
  try {
    const info = await lstat(dir);
    if (!info.isDirectory() || info.isSymbolicLink() || (info.mode & 0o777) !== 0o700 ||
        info.uid !== process.getuid?.() || await realpath(dir) !== dir) throw refused();
  } catch { occupiedPaths.delete(socketPath); throw refused(); }
  // Rebind the SAME path after close; Django's setting never changes. listen
  // refuses an existing endpoint; never unlink another process's socket.
  const recipients = new Set(allowedRecipients);
  const messages = new Map<string, { href: string; messageSequence: number; taken: boolean }>();
  const seen = new Set<string>(), sockets = new Set<Socket>(), pendingSockets = new Set<Socket>();
  const waiters = new Map<string, () => void>();
  let sequence = 0, failed = false, closed = false;
  let closeResult: Promise<void> | undefined;
  const poison = () => {
    failed = true; messages.clear();
    for (const notify of waiters.values()) notify();
  };
  const healthy = () => { if (failed || closed) throw refused(); };
  const server = createServer({ allowHalfOpen: true }, socket => {
    sockets.add(socket); pendingSockets.add(socket);
    let data = Buffer.alloc(0), length: number | undefined;
    const reject = () => { poison(); socket.destroy(); data.fill(0); data = Buffer.alloc(0); };
    socket.setTimeout(2000, reject);
    socket.on('error', () => poison());
    socket.on('close', () => {
      if (pendingSockets.delete(socket)) poison(); // An abandoned partial frame is not a drain.
      sockets.delete(socket); data.fill(0); data = Buffer.alloc(0);
    });
    if (failed || closed || sockets.size > 32) { reject(); return; }
    socket.on('data', chunk => {
      if (failed || data.length + chunk.length > MAX_MAIL_FRAME + 4) { reject(); return; }
      data = Buffer.concat([data, chunk]);
      if (data.length >= 4) {
        length = data.readUInt32BE(0);
        if (!length || length > MAX_MAIL_FRAME || data.length > length + 4) reject();
      }
    });
    socket.on('end', () => {
      try {
        healthy();
        if (length === undefined || data.length !== length + 4) throw refused();
        const text = new TextDecoder('utf-8', { fatal: true }).decode(data.subarray(4));
        const message = inviteHref(JSON.parse(text), recipients, origin);
        if (seen.has(message.recipient)) throw refused();
        seen.add(message.recipient);
        messages.set(message.recipient, { href: message.href, messageSequence: ++sequence, taken: false });
        data.fill(0); data = Buffer.alloc(0);
        pendingSockets.delete(socket);
        socket.end('OK\n');
        waiters.get(message.recipient)?.();
      } catch { reject(); }
    });
  });
  try {
    await new Promise<void>((resolve, reject) => {
      server.once('error', () => reject(refused()));
      server.listen(socketPath, resolve);
    });
  } catch { occupiedPaths.delete(socketPath); throw refused(); }
  server.on('error', poison);
  try { await chmod(socketPath, 0o600); healthy(); } catch {
    closed = true; poison();
    for (const socket of sockets) socket.destroy();
    await new Promise<void>(resolve => server.close(() => resolve())); throw refused();
  }
  const capture: LocalMailCapture = {
    cursor() { healthy(); return sequence; },
    async takeInvite({ recipient, after, timeoutMs }) {
      healthy();
      if (!recipients.has(recipient) || !Number.isSafeInteger(after) || after < 0 || after > sequence ||
          !Number.isFinite(timeoutMs) || timeoutMs < 1 || timeoutMs > 60_000 || waiters.has(recipient)) {
        poison(); throw refused();
      }
      if (!messages.has(recipient)) await new Promise<void>(resolve => {
        const timer = setTimeout(() => { poison(); resolve(); }, timeoutMs);
        waiters.set(recipient, () => { clearTimeout(timer); resolve(); });
      });
      waiters.delete(recipient);
      healthy();
      const message = messages.get(recipient);
      if (!message || message.taken || message.messageSequence <= after) { poison(); throw refused(); }
      message.taken = true;
      return { href: message.href, messageSequence: message.messageSequence };
    },
    async clear(recipient) {
      healthy();
      if (!recipients.has(recipient) || waiters.has(recipient) || messages.get(recipient)?.taken === false) {
        poison(); throw refused();
      }
      messages.delete(recipient); // Keep seen tombstone: late duplicates still fail.
    },
    async close() {
      if (!closeResult) {
        if (waiters.size || pendingSockets.size || [...messages.values()].some(message => !message.taken)) poison();
        closed = true; messages.clear(); seen.clear();
        // Complete accepted ACKs; abort faulty/unfinished traffic. A pending or
        // poisoned close is terminal for this path, never a way to reset it.
        if (failed) for (const socket of sockets) socket.destroy();
        closeResult = new Promise<void>((resolve, reject) => server.close(error => error ? reject(refused()) : resolve()))
          .then(() => { if (failed) throw refused(); occupiedPaths.delete(socketPath); });
      }
      await closeResult;
    },
  };
  bindings.set(capture, { socketPath, nonce, appOrigin: origin, recipients: [...recipients] });
  return capture;
}
