// The rows every writer's golden file is made from (w3-b-writers.test.js,
// golden/*). Covers: quotes, commas, CR/LF, edge spaces, unicode, nulls,
// whole and fractional numbers, booleans, nested objects, a conversation
// and tool calls.
export const GOLDEN_COLUMNS = [
  { key: "row_id", label: "row_id" },
  { key: "msg", label: "customer_message" },
  { key: "conv", label: "conversation" },
  { key: "exp", label: "expected_reply" },
  { key: "tools", label: "expected_tool_calls" },
  { key: "n", label: "order_total" },
  { key: "score", label: "Groundedness" },
  { key: "ok", label: "Correct?" },
  { key: "meta", label: "row_metadata" },
];

export const GOLDEN_ROWS = [
  {
    row_id: "r-1",
    msg: 'Order A-3920 says "delivered", but nothing arrived, sorry',
    conv: [],
    exp: "Sorry for the wait.\nA-3920 is delayed; the new estimate is Thursday.",
    tools: [{ name: "lookup_order", args: { order_id: "A-3920" } }],
    n: 64,
    score: 0.41,
    ok: true,
    meta: { channel: "chat", ticket_id: "T-1001" },
  },
  {
    row_id: "r-2",
    msg: "  Où est ma commande ? — Zoë  ",
    conv: [
      { role: "user", content: "I wrote last week about A-7781." },
      { role: "assistant", content: "Thanks for your patience." },
    ],
    exp: null,
    tools: [],
    n: -3,
    score: 1,
    ok: false,
    meta: null,
  },
  {
    row_id: "r-3",
    msg: "Refund\r\nplease, ✓ 日本",
    conv: null,
    exp: "Refunded $12.50, 5–7 business days.",
    tools: null,
    n: null,
    score: null,
    ok: null,
    meta: { nested: { a: [1, 2] } },
  },
];

export const GOLDEN_ROLES = {
  system: "You are the Acme support agent.",
  user: "msg",
  turns: "conv",
  assistant: "exp",
  toolCalls: "tools",
};

export const GOLDEN_META = {
  dataset: "Golden fixture",
  dataset_version: 3,
  exported_at: "2026-10-22T00:00:00.000Z",
};
