import { expect } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import { offered, rpc } from './mcp-agent';
import { MCP_MOCK } from './public-network';
import { POLL } from './state-probe';

// The next-release fixture is a different mcpmock instance. Its receipt must
// NOT be read from /__received (which observes only the original /mcp release).
const RECEIVED = `${new URL(MCP_MOCK.url).origin}/mcp-next/__received?method=tools/call`;
const ERROR_META = 'io.futureagi/error';
interface Receipt { seq: number; name: string; path: string }
interface HeldTool { tool: string; reason: 'changed' | 'new'; arguments: Record<string, string> }

// Authored walk assertion helper, shared RPC implementation from GW-E2E-029.
// The walk runner must run serially against its admitted local mock network.
// Never attach raw mock headers/tokens or replace the mock's receipt with the
// gateway audit log: the latter cannot prove that dispatch did not occur.
export class RevisionAgent {
  private session: string | undefined;
  private id = 10;
  constructor(
    private req: APIRequestContext,
    private mockReq: APIRequestContext,
    private path: string,
    private key: string,
    private serverId: string,
  ) {}

  private name(tool: string) { return `${this.serverId}__${tool}`; }

  async offered(tools: string[]) {
    let seen: Awaited<ReturnType<typeof offered>> | undefined;
    await expect.poll(async () => {
      seen = await offered(this.req, this.path, this.key);
      return seen.names;
    }, POLL.GATEWAY_SYNC).toEqual(tools.map(tool => this.name(tool)).sort());
    this.session = seen!.session;
    return seen!.descriptions;
  }

  private async received(): Promise<Receipt[]> {
    const response = await this.mockReq.get(RECEIVED, { maxRedirects: 0 });
    expect(response.status(), 'the next-release mock receipt route must be present').toBe(200);
    const body = await response.json();
    expect(Array.isArray(body.received)).toBe(true);
    // Select only value-free receipt fields. A served positive control below
    // also proves this is the active next-release journal, not an empty one.
    return body.received.map(({ seq, name, path }: Receipt) => ({ seq, name, path }));
  }

  private async call(tool: string, args: Record<string, string>) {
    return rpc(this.req, this.path, this.key, this.session, {
      jsonrpc: '2.0', id: this.id++, method: 'tools/call',
      params: { name: this.name(tool), arguments: args },
    });
  }

  async held(tools: HeldTool[]) {
    const baseline = Math.max(0, ...(await this.received()).map(row => row.seq));
    for (const { tool, reason, arguments: args } of tools) {
      const call = await this.call(tool, args);
      expect(call.status).toBe(200);
      expect(call.body?.result?.isError).toBe(true);
      const refusal = call.body?.result?._meta?.[ERROR_META];
      expect({ code: refusal?.code, reason: refusal?.metadata?.reason })
        .toEqual({ code: 'tool_quarantined', reason });
    }
    expect((await this.received()).filter(row => row.seq > baseline),
      'no quarantined call may reach the next-release mock').toEqual([]);
  }

  async answered(tool: string, args: Record<string, string>, text: string) {
    const baseline = Math.max(0, ...(await this.received()).map(row => row.seq));
    const call = await this.call(tool, args);
    expect(call.status).toBe(200);
    expect(call.body?.error).toBeUndefined();
    expect(call.body?.result?.isError ?? false).toBe(false);
    expect(call.body?.result?.content).toEqual([{ type: 'text', text }]);
    expect((await this.received()).filter(row => row.seq > baseline)
      .map(({ name, path }) => ({ name, path })))
      .toEqual([{ name: tool, path: '/mcp-next' }]);
  }
}
