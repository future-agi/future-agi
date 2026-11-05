/*
  A world from a supplied image (T40; alk.md "longer-term direction"): a
  pinned, supported container image is inspected in isolation, what we found
  is reviewed, and reset and grading are proved before it becomes a private
  release. Not every image works, and we never claim one will.
*/

/** Runtimes we can inspect. Anything else is refused, not guessed at. */
export const IMAGE_RUNTIMES = [
  { id: "postgres-rest", label: "Postgres database + REST API" },
  { id: "mysql-rest", label: "MySQL database + REST API" },
  { id: "sqlite-mcp", label: "SQLite database + MCP server" },
];

/** "registry/name@sha256:<64 hex>" — a tag alone can change under you. */
export const isPinnedImage = (s) =>
  /^[\w.-]+(\/[\w.-]+)+@sha256:[a-f0-9]{64}$/i.test(String(s || "").trim());
export const hasTagOnly = (s) =>
  /^[\w.-]+(\/[\w.-]+)+:[\w.-]+$/i.test(String(s || "").trim());

export const SAMPLE_IMAGE =
  "ghcr.io/acme/returns-desk@sha256:9f2c4e1a7b3d5f60e8a1c2d3b4e5f60718293a4b5c6d7e8f9012a3b4c5d6e7f8";

/** What inspecting the image found, by part, each to be reviewed. */
export const inspectionOf = () => ({
  tables: [
    { name: "customers", columns: 9, rows: 4200 },
    { name: "orders", columns: 12, rows: 18300 },
    { name: "order_items", columns: 7, rows: 41000 },
    { name: "refunds", columns: 8, rows: 950 },
  ],
  tools: [
    { name: "GET /orders/{id}", effect: "Reads an order" },
    { name: "GET /customers/{id}", effect: "Reads a customer" },
    { name: "POST /refunds", effect: "Writes a refund" },
    { name: "PATCH /orders/{id}", effect: "Changes an order's status" },
  ],
  personal: [
    { column: "customers.email", kind: "Email address" },
    { column: "customers.phone", kind: "Phone number" },
    { column: "customers.address", kind: "Postal address" },
    { column: "orders.card_last4", kind: "Card digits" },
  ],
  relations: [
    { from: "orders.customer_id", to: "customers.id" },
    { from: "order_items.order_id", to: "orders.id" },
    { from: "refunds.order_id", to: "orders.id" },
  ],
});

/** What a personal-data column becomes in the generated world. */
export const PII_ACTIONS = [
  { id: "generate", label: "Replace with generated data" },
  { id: "drop", label: "Leave the column out" },
];

/** The checks that must pass before a private release (catalogue.md release checks). */
export const IMAGE_CHECKS = [
  { id: "starts", label: "It starts from the pinned image" },
  { id: "resets", label: "It resets to the same state twice" },
  { id: "reference", label: "The reference solution passes" },
  { id: "wrong", label: "A wrong answer fails" },
];
