/*
  The workspace's Gateway → MCP: the MCP servers registered there, the tools
  the Gateway lists from them, and its guardrails — in the shapes dev's
  Gateway uses (sections/gateway/mcp: config.mcp.servers keyed by server id,
  a status per server, one tool catalogue with each tool's server and MCP
  annotations, guardrails with blocked_tools and allowed_servers).

  An environment adds tools from a Gateway server without asking for its
  URL or token again: the credentials stay in the Gateway, and a tool on
  Live is called through it, so its rate limits and checks apply. A tool the
  Gateway's guardrails block can still be added — the world answers it, which
  calls nothing — but it never goes Live. The prototype assumes one gateway
  per workspace.
*/
import { CATALOG, mcpNameOf, mcpServerHost } from "./mcpServers";

export const GATEWAY_MCP_PATH = "/dashboard/gateway/mcp/servers";

/* config.mcp.servers — what Add MCP server saved. */
const SERVERS = {
  shopify: { transport: "http", url: "https://mcp.shopify.com/mcp", auth: { type: "bearer" } },
  stripe: { transport: "http", url: "https://mcp.stripe.com", auth: { type: "bearer" } },
  zendesk: { transport: "http", url: "https://acme.zendesk.com/api/mcp", auth: { type: "api_key", header: "X-API-Key" } },
  calendar: { transport: "http", url: "https://calendarmcp.googleapis.com/mcp", auth: { type: "bearer" } },
  linear: { transport: "http", url: "https://mcp.linear.app/mcp", auth: { type: "bearer" } },
};

/* mcp status — one entry per server. */
const STATUS = {
  shopify: { status: "connected", checkedAgo: "2 minutes ago" },
  stripe: { status: "connected", checkedAgo: "2 minutes ago" },
  zendesk: { status: "connected", checkedAgo: "2 minutes ago" },
  calendar: { status: "connected", checkedAgo: "2 minutes ago" },
  linear: { status: "error", error: "401 from the server — its token was revoked", checkedAgo: "4 minutes ago" },
};

/* What the Gateway lists beyond the shared catalogue, and what it deprecates. */
const EXTRA_TOOLS = {
  zendesk: [
    { name: "reply_to_ticket", desc: "Reply on a ticket; public or internal has to be said.", readOnly: false, openWorld: true, version: "2", args: [{ name: "ticket_id", type: "string", required: true }, { name: "body", type: "string", required: true }, { name: "public", type: "boolean", required: true }] },
  ],
};
const DEPRECATED = {
  add_ticket_comment: { message: "Replies default to public. Use reply_to_ticket, which asks.", replacedBy: "reply_to_ticket" },
};

/* mcp.guardrails */
const GUARDRAILS = { enabled: true, blocked_tools: ["cancel_subscription"], allowed_servers: [] };

const labelOf = (id) => CATALOG[id]?.label || id;
const allowed = (id) => !GUARDRAILS.allowed_servers.length || GUARDRAILS.allowed_servers.includes(id);

/* A Gateway server as the add-tools drawer and the world keep it. */
const asServer = (id) => {
  const cfg = SERVERS[id];
  return {
    id: `gw-${id}`, host: mcpServerHost(cfg.url), label: labelOf(id), transport: cfg.transport, value: cfg.url,
    gateway: { serverId: id },
  };
};

const listedTools = (id) => [...(CATALOG[id]?.tools || []), ...(EXTRA_TOOLS[id] || [])];

/**
 * The servers registered in the Gateway, as the drawer lists them:
 *   [{ id, label, host, url, authType, status, error, checkedAgo, toolCount }]
 * Servers outside the guardrails' allowed_servers are left out.
 */
export const gatewayServersOf = () => Object.keys(SERVERS).filter(allowed).map((id) => {
  const cfg = SERVERS[id];
  const st = STATUS[id] || { status: "unknown" };
  return {
    id, label: labelOf(id), host: mcpServerHost(cfg.url), url: cfg.url, authType: cfg.auth?.type || "none",
    status: st.status, error: st.error || null, checkedAgo: st.checkedAgo || null,
    toolCount: st.status === "connected" ? listedTools(id).length : 0,
  };
});

/** How many servers the guardrails hide from environments. */
export const gatewayHiddenCount = () => Object.keys(SERVERS).filter((id) => !allowed(id)).length;

/**
 * One server's tools, from the Gateway's catalogue — nothing to connect to:
 *   → { ok: true, server, tools } | { ok: false, error }
 * Each tool carries the Gateway's say: blocked by its guardrails, or
 * deprecated with what replaces it.
 */
export const gatewayServerTools = (id) => {
  if (!SERVERS[id]) return { ok: false, error: "That server isn't in the Gateway any more." };
  const st = STATUS[id];
  if (st?.status !== "connected") return { ok: false, error: `The Gateway can't reach ${labelOf(id)}: ${st?.error || "no status yet"}.` };
  const tools = listedTools(id).map((t) => ({
    ...t,
    effect: t.readOnly ? "read" : "write",
    ...(GUARDRAILS.enabled && GUARDRAILS.blocked_tools.includes(t.name) && { blocked: true }),
    ...(DEPRECATED[t.name] && { deprecated: DEPRECATED[t.name] }),
  }));
  return { ok: true, server: asServer(id), tools };
};

/** The Gateway server reached at this host, if one is registered there. */
export const gatewayServerForHost = (host) => {
  const id = Object.keys(SERVERS).find((k) => allowed(k) && mcpServerHost(SERVERS[k].url) === host);
  return id ? asServer(id) : null;
};

/**
 * What the Gateway says now about a tool the world took from it — the
 * Gateway can change after the tool was added:
 *   → null | { kind: "blocked" | "deprecated" | "gone" | "unreachable", label, tip }
 */
export const gatewayToolStatusOf = (tool) => {
  const id = tool?.via?.gateway?.serverId;
  if (!id) return null;
  const name = mcpNameOf(tool);
  if (!SERVERS[id] || !allowed(id)) return { kind: "gone", label: "Not in Gateway", tip: `${tool.via.label} isn't registered in the Gateway any more. The world still answers ${tool.name}; it can't go Live.` };
  if (STATUS[id]?.status !== "connected") return { kind: "unreachable", label: "Gateway can't reach it", tip: `${STATUS[id]?.error || "No status"}. The world still answers ${tool.name}; it can't go Live until the Gateway reaches ${tool.via.label} again.` };
  if (!listedTools(id).some((t) => t.name === name)) return { kind: "gone", label: "Not in Gateway", tip: `${tool.via.label} no longer lists ${name}. The world still answers it; it can't go Live.` };
  if (GUARDRAILS.enabled && GUARDRAILS.blocked_tools.includes(name)) return { kind: "blocked", label: "Blocked in Gateway", tip: `The Gateway's guardrails block ${name}, so it's answered by the world only — Simulated, never Live.` };
  const dep = DEPRECATED[name];
  if (dep) return { kind: "deprecated", label: `Deprecated · use ${dep.replacedBy}`, tip: `${dep.message} Add ${dep.replacedBy} from the Gateway and write scenarios for it.` };
  return null;
};
