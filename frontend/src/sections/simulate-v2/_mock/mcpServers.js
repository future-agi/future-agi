/*
  MCP servers — where an agent's tools can come from, and where an
  environment can take more tools from.

  Agents on Vapi, Retell and ElevenLabs (and agents in code) attach MCP
  servers; their tools aren't in the agent's config, they're listed by the
  server when it runs. An environment reads them so its world can answer
  them, and can add tools from any MCP server — to test a tool the agent
  doesn't have yet, or to give a world with no agent something to act on.

  The prototype doesn't open a connection: "listing tools" reads a catalogue
  keyed by the server's host. Each tool carries MCP's own annotation,
  `readOnlyHint`, which decides whether it may ever run Live.
*/

/* What `tools/list` returns, per kind of server. */
const CATALOG = {
  shopify: {
    label: "Shopify",
    tools: [
      { name: "search_products", desc: "Find products by title, SKU or tag.", readOnly: true, args: [{ name: "query", type: "string", required: true }] },
      { name: "get_order", desc: "An order with its line items, status and shipping address.", readOnly: true, args: [{ name: "order_id", type: "string", required: true }] },
      { name: "create_refund", desc: "Refund some or all of an order to the original payment method.", readOnly: false, args: [{ name: "order_id", type: "string", required: true }, { name: "amount", type: "number", required: true }] },
      { name: "update_shipping_address", desc: "Change where an unfulfilled order ships.", readOnly: false, args: [{ name: "order_id", type: "string", required: true }, { name: "address", type: "object", required: true }] },
    ],
  },
  stripe: {
    label: "Stripe",
    tools: [
      { name: "list_charges", desc: "Charges on a customer, newest first.", readOnly: true, args: [{ name: "customer_id", type: "string", required: true }] },
      { name: "get_customer", desc: "A customer with their default payment method.", readOnly: true, args: [{ name: "customer_id", type: "string", required: true }] },
      { name: "create_refund", desc: "Refund a charge, fully or in part.", readOnly: false, args: [{ name: "charge_id", type: "string", required: true }, { name: "amount", type: "number", required: false }] },
      { name: "cancel_subscription", desc: "Cancel a subscription now or at period end.", readOnly: false, args: [{ name: "subscription_id", type: "string", required: true }, { name: "at_period_end", type: "boolean", required: false }] },
    ],
  },
  hubspot: {
    label: "HubSpot",
    tools: [
      { name: "search_contacts", desc: "Find contacts by name, email or company.", readOnly: true, args: [{ name: "query", type: "string", required: true }] },
      { name: "get_contact", desc: "A contact with their deals and recent activity.", readOnly: true, args: [{ name: "contact_id", type: "string", required: true }] },
      { name: "create_note", desc: "Log a note on a contact's timeline.", readOnly: false, args: [{ name: "contact_id", type: "string", required: true }, { name: "body", type: "string", required: true }] },
      { name: "update_deal_stage", desc: "Move a deal to another pipeline stage.", readOnly: false, args: [{ name: "deal_id", type: "string", required: true }, { name: "stage", type: "string", required: true }] },
    ],
  },
  zendesk: {
    label: "Zendesk",
    tools: [
      { name: "search_tickets", desc: "Tickets matching a requester, status or text.", readOnly: true, args: [{ name: "query", type: "string", required: true }] },
      { name: "get_ticket", desc: "A ticket with its comments and status.", readOnly: true, args: [{ name: "ticket_id", type: "string", required: true }] },
      { name: "create_ticket", desc: "Open a ticket for the requester.", readOnly: false, args: [{ name: "subject", type: "string", required: true }, { name: "body", type: "string", required: true }] },
      { name: "add_ticket_comment", desc: "Reply on a ticket, public or internal.", readOnly: false, args: [{ name: "ticket_id", type: "string", required: true }, { name: "body", type: "string", required: true }] },
    ],
  },
  calendar: {
    label: "Google Calendar",
    tools: [
      { name: "list_events", desc: "Events in a date range on a calendar.", readOnly: true, args: [{ name: "calendar_id", type: "string", required: true }, { name: "from", type: "string", required: true }] },
      { name: "find_free_slots", desc: "Open slots of a given length.", readOnly: true, args: [{ name: "duration_minutes", type: "number", required: true }] },
      { name: "create_event", desc: "Book an event and invite attendees.", readOnly: false, args: [{ name: "start", type: "string", required: true }, { name: "attendees", type: "array", required: false }] },
      { name: "cancel_event", desc: "Cancel an event and notify attendees.", readOnly: false, args: [{ name: "event_id", type: "string", required: true }] },
    ],
  },
  linear: {
    label: "Linear",
    tools: [
      { name: "list_issues", desc: "Issues for a team, filtered by state or assignee.", readOnly: true, args: [{ name: "team", type: "string", required: true }] },
      { name: "get_issue", desc: "An issue with its comments.", readOnly: true, args: [{ name: "issue_id", type: "string", required: true }] },
      { name: "create_issue", desc: "File an issue on a team.", readOnly: false, args: [{ name: "team", type: "string", required: true }, { name: "title", type: "string", required: true }] },
      { name: "update_issue", desc: "Change an issue's state, assignee or priority.", readOnly: false, args: [{ name: "issue_id", type: "string", required: true }] },
    ],
  },
};
const KIND_OF = [
  [/shopify|shop\b|store/i, "shopify"],
  [/stripe|billing|payment/i, "stripe"],
  [/hubspot|crm|salesforce/i, "hubspot"],
  [/zendesk|helpdesk|support|ticket/i, "zendesk"],
  [/calendar|gcal|booking|appointment|schedul/i, "calendar"],
  [/linear|jira|issue|github/i, "linear"],
];

/** The host an MCP server is reached at — or the command's first word, for stdio. */
export const mcpServerHost = (value = "") => {
  const first = String(value).trim().split(/\s+/)[0];
  try { return new URL(first).hostname; } catch { return first; }
};

const slug = (s) => String(s || "server").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "server";

/* A server we don't know: tools named after it, two that read and two that write. */
const genericTools = (host) => {
  const noun = slug(host.replace(/^(mcp|api|www)\./, "").split(".")[0]).replace(/_?mcp_?/g, "") || "record";
  return [
    { name: `search_${noun}`, desc: `Search ${noun} records.`, readOnly: true, args: [{ name: "query", type: "string", required: true }] },
    { name: `get_${noun}`, desc: `One ${noun} record by id.`, readOnly: true, args: [{ name: "id", type: "string", required: true }] },
    { name: `create_${noun}`, desc: `Create a ${noun} record.`, readOnly: false, args: [{ name: "fields", type: "object", required: true }] },
    { name: `update_${noun}`, desc: `Update a ${noun} record.`, readOnly: false, args: [{ name: "id", type: "string", required: true }, { name: "fields", type: "object", required: true }] },
  ];
};

/**
 * Connect to a server and list its tools (`tools/list`).
 *   { transport: "http" | "sse" | "stdio", value, tokenRef }
 *   → { ok: true, server: { id, host, label, transport, value, tokenRef }, tools }
 *   → { ok: false, error }
 */
export const listMcpTools = ({ transport = "http", value = "", tokenRef = null } = {}) => {
  const v = String(value).trim();
  if (!v) return { ok: false, error: transport === "stdio" ? "Add the command that starts the server." : "Add the server's URL." };
  if (transport !== "stdio" && !/^https?:\/\//i.test(v)) return { ok: false, error: "The URL should start with https://." };
  const host = mcpServerHost(v);
  const kind = (KIND_OF.find(([re]) => re.test(v)) || [])[1];
  const entry = kind ? CATALOG[kind] : null;
  const label = entry?.label || host;
  const tools = (entry?.tools || genericTools(host)).map((t) => ({ ...t, effect: t.readOnly ? "read" : "write" }));
  return { ok: true, server: { id: `mcp-${slug(host)}`, host, label, transport, value: v, ...(tokenRef && { tokenRef }) }, tools };
};

/** A tool as the world keeps it: where it came from, and whether it only reads. */
export const worldToolFromMcp = (tool, server) => ({
  name: tool.name,
  desc: tool.desc || "",
  ...(tool.args?.length && { args: tool.args }),
  effect: tool.readOnly ? "read" : "write",
  via: { kind: "mcp", serverId: server.id, host: server.host, label: server.label },
});

/* The MCP servers a hosted assistant has attached — what the platform's
   assistant config lists. Demo: read from the agent's name and domain. */
const PLATFORM_SERVERS = [
  [/storefront|returns|order|shop/i, "https://mcp.shopify.com/sse"],
  [/billing|copilot|payment/i, "https://mcp.stripe.com"],
  [/appointment|booking|clinic|guest/i, "https://calendarmcp.googleapis.com/mcp"],
];
const PLATFORMS_WITH_MCP = new Set(["vapi", "retell", "elevenlabs"]);

/**
 * The MCP servers an agent has attached, with the tools each lists:
 *   [{ server, tools }]
 * Code agents declare theirs (`mcpServers` in their definition); hosted
 * assistants on Vapi, Retell and ElevenLabs carry them in the assistant's
 * config.
 */
export const mcpServersOfAgent = (agent) => {
  if (!agent) return [];
  const declared = agent.definition?.mcpServers || agent.values?.mcpServers;
  const urls = declared?.length
    ? declared.map((d) => (typeof d === "string" ? d : d.url || d.value)).filter(Boolean)
    : PLATFORMS_WITH_MCP.has(agent.values?.provider)
      ? PLATFORM_SERVERS.filter(([re]) => re.test(agent.name || "")).map(([, url]) => url).slice(0, 1)
      : [];
  return urls.map((url) => {
    const read = listMcpTools({ transport: /\/sse$/.test(url) ? "sse" : "http", value: url });
    return read.ok ? { server: read.server, tools: read.tools } : null;
  }).filter(Boolean);
};

/** Every tool an agent gets from its MCP servers, as world tools. */
export const agentMcpTools = (agent) => mcpServersOfAgent(agent)
  .flatMap(({ server, tools }) => tools.map((t) => worldToolFromMcp(t, server)));
