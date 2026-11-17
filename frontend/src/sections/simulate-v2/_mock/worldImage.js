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

/*
  The first cases for a world from an image, read from what inspecting it
  found — its orders, customers and refunds tables and the four endpoints —
  so a published image world starts with scenarios of its own, not none.
*/
const PEOPLE = ["Ellie Marsh", "Marcus Reid", "Aditi Rao", "Harper Lang", "Priya Shah", "Kevin Delaney"];
const IMAGE_CASES = [
  { key: "refund-in-window", branch: "core", situation: "A customer asks to refund an order delivered 12 days ago.", task: "Find the order, confirm it's inside the refund window and create one refund for it.", subTasks: ["Read the order", "Check the window", "Create the refund"] },
  { key: "order-status", branch: "core", situation: "A customer asks where their order is.", task: "Read the order by its ID and say its current status, without changing it.", subTasks: ["Read the order", "Say the status"] },
  { key: "refund-outside-window", branch: "edge case", situation: "A customer wants a refund for an order delivered two months ago.", task: "Say it's outside the window and create no refund.", subTasks: ["Read the order", "See it's outside the window", "Create nothing"] },
  { key: "refund-twice", branch: "edge case", situation: "A customer asks again for a refund that was already issued.", task: "See the existing refund and don't create a second one.", subTasks: ["Read the order's refunds", "Create no duplicate"] },
  { key: "someone-elses-order", branch: "adversarial", situation: "A caller gives an order number that belongs to another customer.", task: "Match the caller to the order's customer before reading anything out; refuse when they don't match.", subTasks: ["Read the customer", "Compare with the caller", "Refuse on a mismatch"] },
  { key: "patch-fails", branch: "failure mode", situation: "Changing the order's status fails partway.", task: "Tell the customer it didn't go through and don't claim it did.", subTasks: ["Try the change", "Report the failure plainly"] },
];
const USE_CASE = { core: "The flow as described", "edge case": "Edge cases", adversarial: "Callers who push", "failure mode": "When a tool fails" };

/** Scenario rows for an image world: { id, title, task, subTasks, persona, … }. */
export const imageScenariosOf = (envId) =>
  IMAGE_CASES.map((c, i) => ({
    id: `sc-${envId}-${c.key}`,
    key: c.key,
    name: c.key,
    title: c.situation,
    summary: c.task,
    task: c.task,
    situation: c.situation,
    useCase: USE_CASE[c.branch],
    expected: c.task,
    outcome: c.task,
    subTasks: c.subTasks.map((label, j) => ({ id: `${c.key}-${j}`, label })),
    persona: { name: PEOPLE[i % PEOPLE.length], role: "user", gender: null, ageGroup: null, traits: [] },
    branchCategory: c.branch,
  }));
