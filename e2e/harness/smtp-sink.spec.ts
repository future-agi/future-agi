import { test as base, expect as baseExpect } from '@playwright/test';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { test, expect } from '../lib/fixtures';
import { ApiError } from '../lib/api-client';
import { MailSink, mintScope } from '../lib/sinks';

// MailSink's own tests. Offline: MailSink against a stand-in that answers mailpit's
// API in the shapes captured from axllent/mailpit:v1.31.4 (search -> messages[]
// with ID, Created, To[].Address; message/<ID> -> Subject, To, HTML). Live: an
// email alert saved with the console's own wire body, sent through the product's
// STARTTLS SMTP path into the stack's smtp-sink.

base('mail sink offline: exact recipient only, oldest first, with subject and HTML', async () => {
  const messages = [
    { ID: 'm2', Created: '2026-10-22T11:00:02Z', To: [{ Address: 'r@e2e.invalid' }], Subject: 'second', HTML: '<p>2</p>' },
    { ID: 'm1', Created: '2026-10-22T11:00:01Z', To: [{ Address: 'R@e2e.invalid' }], Subject: 'first', HTML: '<p>1</p>' },
    { ID: 'm3', Created: '2026-10-22T11:00:03Z', To: [{ Address: 'xr@e2e.invalid' }], Subject: 'other', HTML: '<p>3</p>' },
  ];
  const seen: string[] = [];
  const server = createServer((req, res) => {
    seen.push(req.url!);
    const url = new URL(req.url!, 'http://x');
    res.writeHead(200, { 'Content-Type': 'application/json' });
    if (url.pathname === '/api/v1/search') return res.end(JSON.stringify({ messages: messages.map(({ HTML, Subject, ...m }) => m) }));
    const m = messages.find((x) => url.pathname === `/api/v1/message/${x.ID}`);
    res.end(JSON.stringify(m ? { Subject: m.Subject, To: m.To, HTML: m.HTML } : {}));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const sink = new MailSink(`http://127.0.0.1:${(server.address() as AddressInfo).port}`);
    baseExpect(await sink.messagesTo('r@e2e.invalid')).toEqual([
      { subject: 'first', to: ['R@e2e.invalid'], html: '<p>1</p>' },
      { subject: 'second', to: ['r@e2e.invalid'], html: '<p>2</p>' },
    ]);
    baseExpect(new URL(seen[0], 'http://x').searchParams.get('query')).toBe('to:"r@e2e.invalid"');
    baseExpect(sink.host).toBe('smtp-sink');
    baseExpect(sink.port).toBe(1025);
  } finally {
    server.close();
  }
});

type Created = { result: { id: string } };

// The console's wire body (frontend settings/EmailAlertDialog.jsx handleSave +
// buildProviderConfig: SMTP always use_tls true).
const alertBody = (name: string, recipient: string, port: number) => ({
  name, recipients: [recipient], events: ['budget.exceeded'], thresholds: {}, provider: 'smtp',
  provider_config: { host: 'smtp-sink', port, from_email: 'alerts@e2e.invalid', use_tls: true },
  cooldown_minutes: 0, is_active: true,
});

test('mail sink live: the product\'s test email reaches the sink over STARTTLS, once', async ({ actor }, testInfo) => {
  const sink = new MailSink();
  const scope = mintScope('mail');
  const recipient = `e2e-${scope}@e2e.invalid`;
  const created = await actor.api.post<Created>('/agentcc/email-alerts/', alertBody(`e2e-${scope}`, recipient, sink.port));
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ alertId: created.result.id, recipient }) });
  const sent = await actor.api.post<{ result: { message: string } }>(`/agentcc/email-alerts/${created.result.id}/test/`, {});
  expect(sent.result.message).toBe('Test email sent successfully.');
  const mail = await sink.messagesTo(recipient);
  expect(mail.map(({ subject, to }) => ({ subject, to }))).toEqual([{ subject: '[Agentcc] Alert: Test Email', to: [recipient] }]);
  expect(mail[0].html).toContain('This is a test email from Agentcc Gateway.');
});

test('mail sink live: NEGATIVE CONTROL, the same alert on a closed port is refused and nothing arrives', async ({ actor }) => {
  const sink = new MailSink();
  const scope = mintScope('mail-closed');
  const recipient = `e2e-${scope}@e2e.invalid`;
  const created = await actor.api.post<Created>('/agentcc/email-alerts/', alertBody(`e2e-${scope}`, recipient, 1026));
  const error = await actor.api.post(`/agentcc/email-alerts/${created.result.id}/test/`, {}).catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect((error as ApiError).status).toBe(400);
  expect(await sink.messagesTo(recipient)).toEqual([]);
});
