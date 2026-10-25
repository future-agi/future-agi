// Sinks the gateway flows read back (README "Gateway harness pieces").
// WebhookSink: e2e/stack/webhook-sink/server.mjs, reached by the product at
// hooks.e2e-public.test on the public-classified e2e-public network, read by the
// harness on its host port.
// MailSink: the stacks' smtp-sink (mailpit, STARTTLS with a self-signed
// certificate), reached by the product at smtp-sink:1025, read through mailpit's
// HTTP API on its host port.
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

  /** Every request the channel received, oldest first. */
  async received(channel: string): Promise<ReceivedWebhook[]> {
    const res = await fetch(`${this.readUrl}/__received/${checkName('channel', channel)}`);
    if (!res.ok) throw new Error(`webhook sink read ${res.status}`);
    return ((await res.json()) as { requests: ReceivedWebhook[] }).requests;
  }
}

export interface ReceivedMail { subject: string; to: string[]; html: string }

export class MailSink {
  /** What to save as an SMTP alert's provider_config host and port. */
  readonly host = 'smtp-sink';
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
