// Sample data shaped like the environment sources API, until the backend
// ships it. A file's content is decided by its name, so the same name always
// reads the same way. Fields the API doesn't send live under `ui`.
const MB = 1024 * 1024;

export const SAMPLE_VERSION_ID = "5d0c7a52-3a8e-4f43-9a51-2f0e6c1b7d10";

const limits = (formats, maxFileBytes) => ({
  enabled: true,
  formats,
  max_files: 100,
  max_file_bytes: maxFileBytes,
  max_total_bytes: 200 * MB,
});

export const sampleCapabilities = () => ({
  upload: limits(["pdf", "txt", "csv", "json"], 25 * MB),
  knowledge_base: limits(["pdf", "docx", "rtf", "txt"], 5 * MB),
  environment_version_id: SAMPLE_VERSION_ID,
  draft: { id: null, revision: 0 },
  participant_visibility_enabled: false,
  participant_visibility_reason:
    "The current runtime cannot prove per-participant source isolation.",
});

export const sampleSourceSummary = () => ({
  draft: false,
  snapshot_digest: null,
  sources: [],
  validation_status: null,
  // `data_mode` is real_records or synthetic; `saved_at` is set on accept.
  ui: { data_mode: "real_records", saved_at: null },
});

// How long after a copy is saved its Knowledge base file changes, for files
// in KB_CHANGES. The World & tools tab flags the copy then (see
// useKnowledgeBaseChanges). Add a file id to KB_CHANGES to make it change.
export const KB_CHANGE_AFTER_MS = 20000;

// What a Knowledge base file says once it changes, per file id. `passage` is
// the copy's passage a rule change replaces; the "from" side is read from the
// saved copy. `rows` is how many rows a row change adds or removes.
const KB_CHANGES = {
  "7a2e4c6b-1d3f-4a5b-8c7d-9e0f1a2b3c01": [
    {
      id: "c1",
      kind: "rule-changed",
      label: "Return window",
      ref: "p.1",
      passage: "p1",
      to: "Items can be returned within 21 days of delivery for a full refund.",
      rule: "Refunds are allowed within 21 days of delivery.",
      scenarios: [
        "Refund asked on day 15",
        "Refund asked on day 21",
        "Late delivery, refund on day 20",
      ],
    },
    {
      id: "c2",
      kind: "rule-changed",
      label: "Supervisor approval amount",
      ref: "p.3",
      passage: "p5",
      to: "Refunds over $250 need a supervisor's approval before they are issued.",
      rule: "Refunds over $250 wait for a supervisor's approval.",
      scenarios: ["Refund of $280 on one order"],
    },
    {
      id: "c3",
      kind: "rule-added",
      label: "Damaged items",
      ref: "p.2",
      passage: "p7",
      page: 2,
      to: "Damaged items are refunded without a return once the caller sends a photo.",
      rule: "Damaged items are refunded without a return, with a photo.",
      scenarios: [],
    },
  ],
  "7a2e4c6b-1d3f-4a5b-8c7d-9e0f1a2b3c02": [
    {
      id: "c1",
      kind: "row-added",
      label: "40 orders",
      detail: "O-501 to O-540",
      rows: 40,
      scenarios: ["Refund for a late order"],
    },
    {
      id: "c2",
      kind: "row-changed",
      label: "3 orders",
      detail: "O-104, O-118, O-230: delivery date and price fixed",
      rows: 0,
      scenarios: ["Duplicate refund retry", "Refund asked on day 15"],
    },
    {
      id: "c3",
      kind: "row-removed",
      label: "2 orders",
      detail: "O-377, O-412: cancelled before dispatch",
      rows: -2,
      scenarios: [],
    },
  ],
};

const passagesOf = (source) => source.ui?.parsed?.passages || [];

// The changes waiting for a copy, each rule change's "from" read from it.
export const kbChangesOf = (source) =>
  (source.origin?.kind === "knowledge_base"
    ? KB_CHANGES[source.origin.file_id] || []
    : []
  ).map((change) => ({
    ...change,
    from: passagesOf(source).find((one) => one.id === change.passage)?.text,
  }));

// The copy after a refresh: accepted rule changes replace or add passages,
// row changes move the row count; the version goes up and the flag clears.
export const kbRefreshed = (source, accepted) => {
  const parsed = source.ui?.parsed;
  let next = parsed;
  if (parsed?.kind === "text") {
    const passages = [...parsed.passages];
    accepted.forEach((change) => {
      const at = passages.findIndex((one) => one.id === change.passage);
      const passage = {
        ...(at >= 0 ? passages[at] : { id: change.passage, page: change.page }),
        text: change.to,
        rule: change.rule,
      };
      if (at >= 0) passages[at] = passage;
      else passages.push(passage);
    });
    next = { ...parsed, passages };
  } else if (parsed?.kind === "table") {
    const delta = accepted.reduce((sum, change) => sum + (change.rows || 0), 0);
    next = {
      ...parsed,
      rows: parsed.rows + delta,
      accepted: parsed.accepted + delta,
    };
  }
  return {
    ...source,
    version: source.version + 1,
    ui: { ...source.ui, parsed: next, changed_upstream: false },
  };
};

// Knowledge bases the picker lists, one per collection.
export const KNOWLEDGE_BASES = [
  {
    kb_id: "0b6f8d1e-6c1a-4f7e-9d2b-1a3c5e7f9b01",
    name: "Policies",
    owner: "Legal",
    files: [
      {
        file_id: "7a2e4c6b-1d3f-4a5b-8c7d-9e0f1a2b3c01",
        name: "Refund policy 2026.pdf",
        size: 182 * 1024,
        access: true,
      },
      {
        file_id: "7a2e4c6b-1d3f-4a5b-8c7d-9e0f1a2b3c05",
        name: "Returns FAQ.pdf",
        size: 64 * 1024,
        access: true,
      },
    ],
  },
  {
    kb_id: "0b6f8d1e-6c1a-4f7e-9d2b-1a3c5e7f9b02",
    name: "Order exports",
    owner: "Data team",
    files: [
      {
        file_id: "7a2e4c6b-1d3f-4a5b-8c7d-9e0f1a2b3c02",
        name: "orders-q3-sample.csv",
        size: 640 * 1024,
        access: true,
      },
    ],
  },
  {
    kb_id: "0b6f8d1e-6c1a-4f7e-9d2b-1a3c5e7f9b03",
    name: "Support",
    owner: "Support team",
    files: [
      {
        file_id: "7a2e4c6b-1d3f-4a5b-8c7d-9e0f1a2b3c03",
        name: "Support macros.docx",
        size: 96 * 1024,
        access: true,
      },
    ],
  },
  {
    kb_id: "0b6f8d1e-6c1a-4f7e-9d2b-1a3c5e7f9b04",
    name: "People",
    owner: "People team",
    files: [
      {
        file_id: "7a2e4c6b-1d3f-4a5b-8c7d-9e0f1a2b3c04",
        name: "HR payroll 2026.xlsx",
        size: 310 * 1024,
        access: false,
      },
    ],
  },
];

// What the reader understands. Wider than either route's accept list, so a
// file from chat or the build forms still reads.
const READABLE = [
  "pdf",
  "docx",
  "xlsx",
  "csv",
  "tsv",
  "json",
  "jsonl",
  "txt",
  "md",
  "rtf",
];
// A return window only from an explicit number ("-45", "_45", "45d",
// "45 days"), never from a year like 2026.
const windowOf = (lower) => {
  const found = lower.match(
    /[-_](\d{1,3})(?!\d)|(?:^|\D)(\d{1,3})\s?d(?:ays?)?\b/,
  );
  return found ? found[1] || found[2] : "30";
};
const READABLE_NAMES = READABLE.map((ext) => ext.toUpperCase()).join(", ");

const extOf = (name) => String(name).split(".").pop().toLowerCase();
const isTable = (ext) => ["csv", "tsv", "json", "jsonl", "xlsx"].includes(ext);

// Same key, same id: picking the same file twice lands on one row.
export const sourceIdOf = (key) =>
  `src_${[...key]
    .reduce((hash, char) => ((hash * 33) ^ char.charCodeAt(0)) >>> 0, 5381)
    .toString(16)
    .padStart(8, "0")}`;

const placeOf = (source, n) =>
  ["pdf", "docx"].includes(source.ext) ? `p.${n}` : `section ${n}`;

// A sentence the agent is graded on; setup notes for people changing code aren't.
const RULE_SENTENCE =
  /\b(must|never|always|may only|can only|only (?:if|when|after|before|one)|requires? (?:a |an )?(?:approval|sign-?off)|not allowed|cannot|can't|up to \$\d|over \$\d|within \d+ (?:days?|hours?))\b/i;
const DEV_PROCESS =
  /\b(pull requests?|PRs?|commits?|mypy|lint(ing|er)?|ruff|black|pre-commit|type hints?|docstrings?|unit tests?|test suite|code review|branch(es)?|merge|CI|baseline|changelog|semver|maintainers?|contributors?)\b/i;
const isSentence = (text) =>
  text.split(/\s+/).length >= 5 && !/[<>{}|]/.test(text) && /[a-z]/.test(text);

// Real text as passages by section: headings start a section.
const passagesOfText = (raw) => {
  const out = [];
  let section = 1;
  let seenHeading = false;
  String(raw)
    .replace(/```[\s\S]*?```/g, " ")
    .split(/\n/)
    .forEach((line) => {
      if (/^\s*\|/.test(line) || /^\s*</.test(line)) return;
      if (/^\s*#{1,6}\s/.test(line)) {
        if (seenHeading) section += 1;
        seenHeading = true;
        return;
      }
      line
        .replace(/<[^>]*>?/g, " ")
        .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
        .replace(/^\s*([-*+]|\d+[.)])\s+/, "")
        .replace(/[`*_>]/g, "")
        .trim()
        .split(/(?<=[.!?])\s+/)
        .map((sentence) => sentence.trim())
        .filter((sentence) => sentence.length > 3)
        .forEach((sentence) =>
          out.push({
            id: `t${out.length + 1}`,
            page: section,
            text: sentence,
            ...(RULE_SENTENCE.test(sentence) &&
              !DEV_PROCESS.test(sentence) &&
              isSentence(sentence) &&
              sentence.length <= 200 && {
                rule: sentence.replace(/\s+/g, " "),
              }),
          }),
        );
    });
  return out.slice(0, 400);
};

const POLICY_PASSAGES = (days, { approval = 500, extra = false } = {}) => [
  {
    id: "p1",
    page: 1,
    text: `Items can be returned within ${days} days of delivery for a full refund.`,
    rule: `Refunds are allowed within ${days} days of delivery.`,
  },
  {
    id: "p2",
    page: 1,
    text: "A refund goes back to the original payment method.",
  },
  {
    id: "p3",
    page: 2,
    text: "Only one refund may exist per order. A retried request reuses the refund already issued.",
    rule: "Only one refund per order. A retry reuses the existing refund.",
  },
  {
    id: "p4",
    page: 2,
    text: "Final-sale items cannot be returned or refunded.",
  },
  {
    id: "p5",
    page: 3,
    text: `Refunds over $${approval} need a supervisor's approval before they are issued.`,
    rule: `Refunds over $${approval} wait for a supervisor's approval.`,
    approval: { above: approval, by: "Supervisor" },
  },
  ...(extra
    ? [
        {
          id: "p6",
          page: 3,
          text: "Gift-card purchases are refunded as store credit, never to the card.",
          rule: "Gift-card purchases are refunded as store credit only.",
        },
      ]
    : []),
];

const REFERENCE_PASSAGES = [
  {
    id: "p1",
    page: 1,
    text: "Greet the caller by name and confirm the order number before anything else.",
  },
  {
    id: "p2",
    page: 1,
    text: "When a refund is declined, offer store credit and explain the window in one sentence.",
  },
  {
    id: "p3",
    page: 2,
    text: "Hand the caller to a supervisor when they ask for one twice.",
  },
];

const ANSWER_PASSAGES = [
  {
    id: "p1",
    page: 1,
    text: "O-104 retry: reuse RF-7; never issue a second refund.",
  },
  {
    id: "p2",
    page: 1,
    text: "Day-31 return: refuse, explain the 30-day window, offer store credit.",
  },
];

// Orders call the customer buyer_id; customers call it customer_id. Only an
// orders file names buyers the customers file doesn't have.
const TABLES = [
  {
    match: /orders?[-_ ]?q3|buyer/,
    table: "orders",
    columns: [
      { name: "order_id", type: "string", key: true, stableId: true },
      {
        name: "buyer_id",
        type: "string",
        proposal: {
          to: "customers.customer_id",
          id: "C17",
          mismatches: [
            { row: 104, reason: "buyer_id C-881 isn't in customers" },
            { row: 377, reason: "buyer_id C-902 isn't in customers" },
          ],
        },
      },
      { name: "delivered_at", type: "date" },
      {
        name: "price",
        type: "string",
        textRows: [
          { row: 212, value: "$45.99", reads: 45.99 },
          { row: 340, value: "120.00 USD", reads: 120 },
          { row: 466, value: "1,299.00", reads: 1299 },
        ],
      },
      {
        name: "promo_code",
        type: "string",
        unmatched: true,
        suggest: "discount_code",
      },
      {
        name: "loyalty_tier",
        type: "string",
        unmatched: true,
        suggest: "customer_tier",
      },
      { name: "email", type: "string", sensitive: true },
    ],
    rows: 500,
    rejected: [],
    sample: {
      order_id: "O-104",
      buyer_id: "C17",
      delivered_at: "2026-09-28",
      price: "$45.99",
      promo_code: "FALL10",
      loyalty_tier: "gold",
      email: "dana.liu@example.com",
    },
  },
  {
    match: /order/,
    table: "orders",
    columns: [
      { name: "order_id", type: "string", key: true },
      { name: "customer_id", type: "string", ref: "customers.customer_id" },
      { name: "delivered_at", type: "date" },
      { name: "amount_cents", type: "integer" },
      { name: "email", type: "string", sensitive: true },
    ],
    rows: 500,
    rejected: [
      { row: 104, reason: "customer_id C-881 isn't in customers" },
      { row: 377, reason: "customer_id C-902 isn't in customers" },
    ],
    sample: {
      order_id: "O-104",
      customer_id: "C17",
      delivered_at: "2026-09-28",
      amount_cents: 4599,
      email: "dana.liu@example.com",
    },
  },
  {
    match: /customer|account|user/,
    table: "customers",
    columns: [
      { name: "customer_id", type: "string", key: true, stableId: true },
      { name: "name", type: "string", sensitive: true },
      { name: "email", type: "string", sensitive: true },
      { name: "tier", type: "string" },
    ],
    rows: 200,
    rejected: [],
    sample: {
      customer_id: "C17",
      name: "Dana Liu",
      email: "dana.liu@example.com",
      tier: "gold",
    },
  },
  {
    match: /refund/,
    table: "refunds",
    columns: [
      { name: "refund_id", type: "string", key: true },
      { name: "order_id", type: "string", ref: "orders.order_id" },
      { name: "amount_cents", type: "integer" },
      { name: "status", type: "string" },
    ],
    rows: 85,
    rejected: [],
    sample: {
      refund_id: "RF-7",
      order_id: "O-104",
      amount_cents: 4599,
      status: "committed",
    },
  },
];

const FORMATS = {
  csv: "CSV · comma · header row · UTF-8",
  tsv: "TSV · tab · header row · UTF-8",
  xlsx: "XLSX · first sheet · header row",
};

const tableOf = (lower, ext) => {
  const found = TABLES.find((one) => one.match.test(lower)) || {
    table: lower.replace(/\.[^.]+$/, "").replace(/[^a-z0-9]+/g, "_"),
    columns: [
      { name: "id", type: "string", key: true },
      { name: "value", type: "string" },
    ],
    rows: 120,
    rejected: [],
    sample: { id: "1", value: "1" },
  };
  return {
    kind: "table",
    table: found.table,
    columns: found.columns,
    rows: found.rows,
    accepted: found.rows - found.rejected.length,
    rejected: found.rejected,
    sample: found.sample,
    format: FORMATS[ext] || "JSON · array of objects",
  };
};

/**
 * What reading a file found, settled: `not_saved` when it read, otherwise
 * `unreadable` or `unsupported`. `text` is the file's words for md and txt.
 */
export const readSource = ({ name, size, text, origin }) => {
  const lower = name.toLowerCase();
  const ext = extOf(name);
  const base = {
    source_id: sourceIdOf(
      origin.kind === "knowledge_base"
        ? `kb:${origin.file_id}`
        : `upload:${name}:${size}`,
    ),
    name,
    ext,
    size,
    version: 1,
    origin,
    status: "not_saved",
    status_note: null,
  };
  const ui = (fields = {}) => ({
    parsed: null,
    privacy: {},
    personal_choice: null,
    // Set once the Knowledge base file changes after the copy; see KB_CHANGES.
    changed_upstream: false,
    ...fields,
  });
  const as = (role, visibility, fields) => ({
    ...base,
    role,
    purpose: ["policy", "reference", "seed_records"].includes(role)
      ? role
      : null,
    visibility,
    ...fields,
  });

  if (!READABLE.includes(ext)) {
    return as(null, null, {
      status: "unsupported",
      status_note: `Can't read .${ext} files. Supported: ${READABLE_NAMES}.`,
      ui: ui(),
    });
  }
  if (typeof text === "string" && ["md", "txt"].includes(ext)) {
    const passages = passagesOfText(text);
    const policy = passages.some((one) => one.rule);
    return as(policy ? "policy" : "reference", policy ? "tool" : "agent", {
      ui: ui({ parsed: { kind: "text", passages } }),
    });
  }
  if (/scan/.test(lower)) {
    return as("policy", "tool", {
      status: "unreadable",
      status_note:
        "Scanned pages with no text. Upload a text PDF or paste the policy into chat.",
      ui: ui(),
    });
  }
  if (/expected|outcome|answer|golden/.test(lower)) {
    return as("evaluation_reference", "evaluator", {
      ui: ui({ parsed: { kind: "text", passages: ANSWER_PASSAGES } }),
    });
  }
  if (isTable(ext)) {
    const parsed = tableOf(lower, ext);
    return as("seed_records", "tool", {
      status_note: parsed.rejected.length
        ? `${parsed.rejected.length} of ${parsed.rows} rows rejected`
        : null,
      ui: ui({
        parsed,
        privacy: Object.fromEntries(
          parsed.columns
            .filter((column) => column.sensitive)
            .map((column) => [column.name, "replace"]),
        ),
      }),
    });
  }
  // A Knowledge base policy reads as its latest revision; an upload takes
  // its return window from the number in its name.
  const policy =
    origin.kind === "knowledge_base"
      ? POLICY_PASSAGES(14, { approval: 300, extra: true })
      : POLICY_PASSAGES(windowOf(lower));
  const policyish =
    /polic|terms|rule|sop|procedure|guideline|sla|returns?|refund/.test(lower);
  const reference =
    /chat|conversation|transcript|guide|faq|handbook|reference|catalog|macro|manual|readme|notes?\b|changelog|license|contributing/.test(
      lower,
    ) ||
    (["md", "txt"].includes(ext) && !policyish);
  return as(reference ? "reference" : "policy", reference ? "agent" : "tool", {
    ui: ui({
      parsed: {
        kind: "text",
        pages: 2,
        passages: reference ? REFERENCE_PASSAGES : policy,
      },
      ...(!reference && { effective_from: "2026-01-01" }),
    }),
  });
};

// What the sources API's prepare returns in `draft` for the files that read.
export const draftOfSources = (sources, digest) => {
  const pinned = sources.filter(
    (source) =>
      source.status !== "unsupported" &&
      source.status !== "unreadable" &&
      source.purpose,
  );
  const tables = new Map(
    pinned
      .filter((source) => source.ui.parsed?.kind === "table")
      .map((source) => [source.ui.parsed.table, source]),
  );
  const statementsOf = (source) =>
    source.role === "policy"
      ? source.ui.parsed.passages
          .filter((passage) => passage.rule)
          .map((passage) => ({
            statement_id: `${source.source_id}:${passage.id}`,
            text: passage.rule,
            locator: placeOf(source, passage.page),
          }))
      : [];
  const columnsOf = (source) =>
    source.ui.parsed?.kind === "table" ? source.ui.parsed.columns : [];
  return {
    draft_digest: digest,
    sources: pinned.map((source) => ({
      pin: { source_id: source.source_id, role: source.role },
      statements: statementsOf(source),
    })),
    proposals: {
      field_mappings: pinned.flatMap((source) =>
        columnsOf(source).map((column) => ({
          source_id: source.source_id,
          mapping: {
            source_field: column.name,
            target_field: column.name,
            personal_data: Boolean(column.sensitive),
          },
        })),
      ),
      policy_rules: pinned.flatMap((source) =>
        statementsOf(source).map((statement) => ({
          statement_id: statement.statement_id,
          status: "supported",
          rule: statement.text,
        })),
      ),
      relationships: pinned.flatMap((source) =>
        columnsOf(source)
          .filter(
            (column) => column.ref && tables.has(column.ref.split(".")[0]),
          )
          .map((column) => ({
            relationship: {
              from_source_id: source.source_id,
              from_field: column.name,
              to_source_id: tables.get(column.ref.split(".")[0]).source_id,
              to_field: column.ref.split(".")[1],
            },
          })),
      ),
    },
    diagnostics: [],
  };
};

const personalColumnsOf = (source) =>
  (source.ui.parsed?.columns || [])
    .filter((column) => column.sensitive)
    .map((column) => column.name);

/**
 * What the builder proposes from these files, each item citing where it came
 * from. Blocking items can't be added until the person resolves them.
 */
export const proposalItemsOf = (sources) => {
  const items = [];
  const cite = (source, passage) => ({
    sourceId: source.source_id,
    ref: passage
      ? `${source.name} · ${placeOf(source, passage.page)}`
      : source.name,
    ...(passage && { excerpt: passage.text, at: passage.id }),
  });
  const readable = sources.filter((source) => source.status === "not_saved");
  const policies = readable.filter((source) => source.role === "policy");
  const tables = readable.filter((source) => source.role === "seed_records");
  const tableNames = new Set(tables.map((source) => source.ui.parsed.table));

  // Two policies that set the return window differently: the person picks.
  const windows = policies
    .map((source) => ({
      source,
      passage: source.ui.parsed.passages.find((one) => one.id === "p1"),
    }))
    .filter((one) => one.passage?.rule);
  const conflict = new Set(windows.map((one) => one.passage.rule)).size > 1;
  if (conflict) {
    items.push({
      id: "conflict-window",
      kind: "conflict",
      title: "Two policies set the return window differently",
      detail: windows
        .map(
          ({ source, passage }) =>
            `${source.name} p.${passage.page}: “${passage.text}”`,
        )
        .join(" · "),
      options: windows.map(({ source, passage }) => ({
        sourceId: source.source_id,
        label: source.name,
        rule: passage.rule,
      })),
      blocking: true,
    });
  }
  policies.forEach((source) =>
    source.ui.parsed.passages
      .filter((passage) => passage.rule && !(conflict && passage.id === "p1"))
      .forEach((passage) =>
        items.push({
          id: `rule-${source.source_id}-${passage.id}`,
          kind: "rule",
          title: `Rule: ${passage.rule}`,
          cite: cite(source, passage),
          rule: passage.rule,
        }),
      ),
  );
  tables.forEach((source) => {
    const parsed = source.ui.parsed;
    items.push({
      id: `table-${source.source_id}`,
      kind: "table",
      title: `New table ${parsed.table}: ${parsed.accepted.toLocaleString()} rows${parsed.rejected.length ? `, ${parsed.rejected.length} rejected` : ""}`,
      cite: {
        ...cite(source),
        excerpt: parsed.columns.map((column) => column.name).join(", "),
      },
    });
    parsed.columns
      .filter((column) => column.ref)
      .forEach((column) => {
        const to = column.ref.split(".")[0];
        const present = tableNames.has(to);
        if (present && !parsed.rejected.length) return;
        items.push({
          id: `missing-${source.source_id}-${column.name}`,
          kind: "missing",
          title: present
            ? `${parsed.rejected.length} rows of ${parsed.table} point at missing ${to}`
            : `Missing table: ${to}`,
          detail: present
            ? parsed.rejected
                .map((row) => `row ${row.row}: ${row.reason}`)
                .join(" · ")
            : `No ${to} table for ${parsed.table}.${column.name}.`,
          cite: { ...cite(source), ref: `${source.name} · ${column.name}` },
          sourceId: source.source_id,
          field: column.name,
          present,
          ...(!present && { needs: to }),
          affects: [],
          blocking: true,
        });
      });
  });
  const window = policies[0]?.ui.parsed.passages.find((one) => one.id === "p1");
  const days = window ? Number((window.text.match(/(\d+) days/) || [])[1]) : 0;
  if (days) {
    items.push({
      id: "scenario-boundary",
      kind: "scenario",
      title: `Scenarios: a return on day ${days} and on day ${days + 1}`,
      detail: `Day ${days} is refunded; day ${days + 1} is refused with the window explained.`,
      cite: cite(policies[0], window),
      inferred: true,
      scenario: [
        {
          t: `Return on day ${days}`,
          k: `A caller returns an item delivered exactly ${days} days ago and asks for a refund.`,
          e: "Agent refunds to the original payment method and confirms the amount.",
        },
        {
          t: `Return on day ${days + 1}`,
          k: `A caller returns an item delivered ${days + 1} days ago and insists on a refund.`,
          e: `Agent refuses, explains the ${days}-day window, and offers what policy allows.`,
        },
      ],
    });
  }
  const retry = policies
    .flatMap((source) =>
      source.ui.parsed.passages
        .filter((passage) => passage.id === "p3")
        .map((passage) => ({ source, passage })),
    )
    .shift();
  if (retry) {
    const hasRefunds = tableNames.has("refunds");
    items.push({
      id: "scenario-retry",
      kind: "scenario",
      title: "Scenario: duplicate refund retry",
      detail: hasRefunds
        ? "Order O-104 already has refund RF-7; the caller asks to retry. One refund must exist at the end."
        : "Needs a refunds table. Attach one to unblock it.",
      cite: cite(retry.source, retry.passage),
      blocking: !hasRefunds,
      ...(!hasRefunds && { needs: "refunds" }),
      scenario: [
        {
          t: "Duplicate refund retry",
          k: "Order O-104 already has refund RF-7 committed. The caller says the refund failed and asks to try again.",
          e: "Agent finds RF-7, reuses it, and tells the caller the refund already went through. No second refund.",
        },
      ],
    });
  }
  const approval = policies
    .flatMap((source) =>
      source.ui.parsed.passages
        .filter((passage) => passage.approval)
        .map((passage) => ({ source, passage })),
    )
    .shift();
  if (approval) {
    const { above } = approval.passage.approval;
    items.push({
      id: `actor-${approval.source.source_id}-${approval.passage.id}`,
      kind: "actor",
      title: `Actor: Supervisor approves refunds over $${above}`,
      detail:
        "Joins when the agent asks to refund more than the cap; approves or declines, and declines if no one answers in 3 minutes.",
      cite: cite(approval.source, approval.passage),
      actor: {
        name: "Supervisor",
        goal: "Keep large refunds within policy.",
        blurb: `Approves or declines refunds over $${above}.`,
        entry: "joins",
        pressure: "authority",
        modalities: ["voice", "chat"],
        traits: ["brisk"],
        trigger: `The agent asks to refund more than $${above}`,
        canApprove: true,
        approvalScope: `Refunds over $${above}`,
        timeoutMin: 3,
        fallback: "reject",
        knowledge: [approval.source.name],
      },
      editable: { field: "approvalScope", label: "Approval scope" },
    });
  }
  const orders = tables.find((source) => source.ui.parsed.table === "orders");
  if (orders) {
    const { sample } = orders.ui.parsed;
    items.push({
      id: `state-${orders.source_id}`,
      kind: "state",
      title: `Starting state: order ${sample.order_id} delivered ${sample.delivered_at}`,
      detail:
        "Every run starts from this snapshot of the records, and a reset puts it back. Customer details stay as the privacy settings say.",
      cite: {
        ...cite(orders),
        ref: `${orders.name} · ${sample.order_id}`,
        at: sample.order_id,
        excerpt: `${sample.order_id}, delivered ${sample.delivered_at}, ${sample.price ?? `$${(sample.amount_cents / 100).toFixed(2)}`}`,
      },
    });
  }
  readable
    .filter((source) => source.role === "reference")
    .forEach((source) =>
      items.push({
        id: `reference-${source.source_id}`,
        kind: "reference",
        title: `${source.name} becomes background for the agent`,
        detail:
          "The agent can read it when it needs background. It states no rules, so nothing is graded from it.",
        cite: cite(source),
        read: {
          sections: new Set(source.ui.parsed.passages.map((one) => one.page))
            .size,
          words: source.ui.parsed.passages.reduce(
            (n, one) => n + one.text.split(/\s+/).filter(Boolean).length,
            0,
          ),
          passages: source.ui.parsed.passages.slice(0, 60).map((one) => ({
            id: one.id,
            where: placeOf(source, one.page),
            text: one.text,
          })),
        },
      }),
    );
  readable
    .filter((source) => personalColumnsOf(source).length)
    .forEach((source) =>
      items.push({
        id: `personal-${source.source_id}`,
        kind: "personal",
        title: `Personal data in ${source.name}`,
        detail: `The builder found columns that look personal: ${personalColumnsOf(source).join(", ")}.`,
        cite: cite(source),
        sourceId: source.source_id,
        suggested: "replace",
        choice: source.ui.personal_choice,
        needsChoice: !source.ui.personal_choice,
      }),
    );
  readable
    .filter((source) => source.visibility === "evaluator")
    .forEach((source) =>
      items.push({
        id: `hidden-${source.source_id}`,
        kind: "hidden",
        title: `${source.name} stays grader-only`,
        detail:
          "The agent and the simulated caller can't read it, even though it came through the same chat.",
        cite: cite(source),
      }),
    );
  sources
    .filter((source) => source.status === "unreadable")
    .forEach((source) =>
      items.push({
        id: `unreadable-${source.source_id}`,
        kind: "unreadable",
        title: `${source.name} couldn't be read`,
        detail: source.status_note,
        cite: cite(source),
        blocking: true,
      }),
    );
  return items;
};
