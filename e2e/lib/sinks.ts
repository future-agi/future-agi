// Sinks the gateway flows read back (README "Gateway harness pieces").
// WebhookSink (H1): e2e/stack/webhook-sink/server.mjs, reached by the product at
// hooks.e2e-public.test on the public-classified e2e-public network, read by the
// harness on its host port.
// MailSink (H3): the stacks' smtp-sink (mailpit, STARTTLS with a self-signed
// certificate), reached by the product at smtp.e2e-public.test:1025 on the
// public-classified e2e-public network (the product refuses a private SMTP host
// with its defaults on), read through mailpit's HTTP API on its host port.
import { E2E } from './env';
import { PUBLIC_NETWORK, SCOPE_NAME } from './public-network';

export interface ReceivedWebhook {
  at: string; method: string; path: string; status: number;
  headers: Record<string, string>; body: unknown;
}

const checkName = (kind: string, name: string) => {
  if (!SCOPE_NAME.test(name)) throw new Error(`${kind} ${JSON.stringify(name)} is not [A-Za-z0-9_.-]{1,80}`);
  return name;
};

/** A unique channel or scenario name: `<label>-<base36 time>-<random>`. */
export const mintScope = (label: string) =>
  checkName('scope', `${label}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`);

export class WebhookSink {
  constructor(private readonly readUrl: string = E2E.webhookSinkUrl) {}

  /** The URL to save in the product: in-stack, public-classified. failFirst: the channel's first n deliveries answer 500. */
  url(channel: string, o: { failFirst?: number } = {}): string {
    if (o.failFirst !== undefined && !(Number.isInteger(o.failFirst) && o.failFirst >= 0 && o.failFirst <= 999)) {
      throw new Error('failFirst is 0-999');
    }
    const fail = o.failFirst === undefined ? '' : `/fail/${o.failFirst}`;
    return `http://${PUBLIC_NETWORK.pinned['webhook-sink'].alias}:8080/r/${checkName('channel', channel)}${fail}/hook`;
  }

  /** Stops the channel answering (each request to it is recorded with status 0 and its
   * connection reset, as a stopped server's would be), or starts it again. Only that
   * channel: every other test's keeps answering. */
  async stopped(channel: string, stop: boolean): Promise<void> {
    const res = await fetch(`${this.readUrl}/__stopped/${checkName('channel', channel)}`,
      { method: stop ? 'PUT' : 'DELETE' });
    if (!res.ok) throw new Error(`webhook sink ${stop ? 'stop' : 'start'} ${res.status}`);
  }

  /** Revokes a credential at every vendor stand-in (each refuses it as its vendor refuses a
   * revoked key: Datadog 403, PostHog 401, Mixpanel /track 0 and /import 401), or accepts it
   * again. Only that credential: every other test's keeps working. */
  async revoked(credential: string, revoke: boolean): Promise<void> {
    const res = await fetch(`${this.readUrl}/__revoked/${checkName('credential', credential)}`,
      { method: revoke ? 'PUT' : 'DELETE' });
    if (!res.ok) throw new Error(`webhook sink ${revoke ? 'revoke' : 'restore'} ${res.status}`);
  }

  /** Every request the channel received, oldest first. */
  async received(channel: string): Promise<ReceivedWebhook[]> {
    const res = await fetch(`${this.readUrl}/__received/${checkName('channel', channel)}`);
    if (!res.ok) throw new Error(`webhook sink read ${res.status}`);
    return ((await res.json()) as { requests: ReceivedWebhook[] }).requests;
  }
}

export interface ReceivedMail { subject: string; to: string[]; html: string }

export class MailSink {
  /** What to save as an SMTP alert's provider_config host and port: in-stack, public-classified. */
  readonly host = PUBLIC_NETWORK.pinned['smtp-sink'].alias;
  readonly port = 1025;

  constructor(private readonly readUrl: string = E2E.mailSinkUrl) {}

  private async get<T>(path: string): Promise<T> {
    const res = await fetch(`${this.readUrl}${path}`);
    if (!res.ok) throw new Error(`mail sink read ${res.status} on ${path.split('?')[0]}`);
    return (await res.json()) as T;
  }

  /** Every message addressed to exactly `address` (To), oldest first. */
  async messagesTo(address: string): Promise<ReceivedMail[]> {
    // mailpit /api/v1/search narrows by To; the exact-address filter is ours.
    const found = await this.get<{ messages: { ID: string; Created: string; To: { Address: string }[] }[] }>(
      `/api/v1/search?query=${encodeURIComponent(`to:"${address}"`)}&limit=200`);
    const mine = found.messages.filter((m) => m.To.some((t) => t.Address.toLowerCase() === address.toLowerCase()))
      .sort((a, b) => Date.parse(a.Created) - Date.parse(b.Created));
    return Promise.all(mine.map(async (m) => {
      const full = await this.get<{ Subject: string; To: { Address: string }[]; HTML: string }>(
        `/api/v1/message/${encodeURIComponent(m.ID)}`);
      return { subject: full.Subject, to: full.To.map((t) => t.Address), html: full.HTML };
    }));
  }
}
