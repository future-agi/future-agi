import {
  BUILD_FILES_ACCEPT,
  extOf,
  readAsOf,
} from "../buildDrawer/forms/shared/buildFiles";

// The design proposed before a build, worked out from the draft alone. Stub:
// no design API exists yet, so files are judged by their name (the draft keeps
// no contents) and the description by keywords, as the prototype does.

export const READ_MS = 3600;
// When each part of the design shows while the files are read, as a share of
// READ_MS, so the wait is visible part by part.
export const READ_AT = {
  records: 0.2,
  tools: 0.45,
  rules: 0.7,
  people: 0.82,
  scenarios: 0.97,
};

// Records a description implies; the first match per table wins.
const TABLES = [
  {
    re: /\b(book(ing|ings|s)?|appointment|reservation)/,
    name: "bookings",
    rows: 120,
  },
  { re: /\b(repair|service|treatment|class)/, name: "services", rows: 24 },
  { re: /\b(slot|availab|calendar|schedule)/, name: "slots", rows: 320 },
  {
    re: /\b(customer|caller|client|patient|guest)/,
    name: "customers",
    rows: 200,
  },
  { re: /\border/, name: "orders", rows: 400 },
  { re: /\brefund/, name: "refunds", rows: 60 },
  { re: /\b(payment|card|charge|invoice)/, name: "payments", rows: 300 },
  { re: /\bdiscount/, name: "discounts", rows: 40 },
  { re: /\bticket/, name: "tickets", rows: 150 },
];

// Tools the agent needs, read from what it has to do, each with the case a
// scenario tests it by.
const TOOLS = [
  {
    re: /\b(check|look|find).{0,20}(slot|availab|open)|availability/,
    name: "check_availability",
    desc: "Open slots for a service on a day",
    test: "A customer asks for the next open slot and books it.",
  },
  {
    re: /\bbook(s|ing)?\b(?!ings? (are|is))/,
    name: "create_booking",
    desc: "Book a slot for a customer",
    test: "The customer wants a day that is already fully booked.",
  },
  {
    re: /\bcancel/,
    name: "cancel_booking",
    desc: "Cancel a booking and free its slot",
    test: "A customer cancels an existing booking.",
  },
  {
    re: /\bdiscount/,
    name: "apply_discount",
    desc: "Apply a discount to a booking",
    test: "A customer asks for a small discount within the limit.",
  },
  {
    re: /\b(approv|sign.?off)/,
    name: "request_approval",
    desc: "Ask the approver and wait for a yes or no",
    test: "A customer pushes for more than the agent may give on its own.",
  },
  {
    re: /\brefund/,
    name: "issue_refund",
    desc: "Refund up to what was paid",
    test: "A customer asks for a refund on a recent order.",
  },
  {
    re: /\b(look ?up|status|track).{0,20}order|order status/,
    name: "lookup_order",
    desc: "Fetch an order by ID or email",
    test: "A customer asks where their order is.",
  },
  {
    re: /\b(escalat|hand ?off|transfer to|human)/,
    name: "escalate_to_human",
    desc: "Hand the case to a person",
    test: "A case the agent shouldn't handle alone.",
  },
];

// What a policy file stands for, by its name, until files are read for real.
const POLICY_RULES = [
  {
    re: /refund|return/,
    rules: [
      "Refund only within 30 days of delivery.",
      "Refunds over $500 need a manager's approval.",
    ],
  },
  {
    re: /cancel/,
    rules: ["Cancel free of charge up to 24 hours before the booking."],
  },
  { re: /discount|pric/, rules: ["Discounts over 10% need approval."] },
  {
    re: /sla|escalat/,
    rules: ["Escalate to a person when a case is open longer than a day."],
  },
];
const POLICY_FALLBACK = [
  "Escalate to a person when the policy doesn't cover the case.",
];

// Sentences that read as a rule the agent is graded on.
const RULEISH =
  /(escalat|must|never|always|require|only|unless|before|approv|not allowed|up to|over \$?\d)/i;
const APPROVER = /\b(manager|supervisor|owner|lead|admin)\b/i;

// Actions an agent takes on the thing it works with, outside the domains above.
const ACTIONS = [
  "triage",
  "label",
  "tag",
  "assign",
  "close",
  "reopen",
  "merge",
  "reply",
  "respond",
  "comment",
  "escalate",
  "route",
  "notify",
  "schedule",
  "approve",
  "reject",
  "review",
  "summarize",
  "update",
  "create",
  "search",
  "flag",
  "prioritize",
  "archive",
];

/** The records and tools a description names outside those domains, or null. */
function genericDesignOf(desc) {
  // Lines about changing the code (a contributing guide) say nothing about
  // what the agent does: "tests must pass before review" isn't a review tool.
  const t = desc
    .split(/(?<=[.!?\n])\s*/)
    .filter(
      (x) =>
        !/\b(pull requests?|commits?|lint(er|ing)?|unit tests?|test suite|ci|branch(es)?|pre-commit|contributors?|maintainers?)\b/.test(
          x,
        ),
    )
    .join(" ");
  const verbs = ACTIONS.filter((v) =>
    new RegExp(`\\b${v}(s|es|d|ed|ing)?\\b`).test(t),
  );
  const m =
    t.match(
      new RegExp(
        `\\b(?:${ACTIONS.join("|")})(?:s|es)?\\s+(?:new |incoming |open |the |each |every )?([a-z]+s)\\b`,
      ),
    ) || t.match(/\b(?:new|incoming|open)\s+([a-z]+s)\b/);
  if (!verbs.length || !m) return null;
  const one = m[1].replace(/s$/, "");
  return {
    // Always proposed: it's the thing the description acts on.
    table: { re: /./, name: m[1], rows: 150 },
    tools: verbs.slice(0, 6).map((v) => ({
      re: new RegExp(`\\b${v}`),
      name: `${v}_${one}`,
      desc: `${v.charAt(0).toUpperCase()}${v.slice(1)} ${/^[aeiou]/.test(one) ? "an" : "a"} ${one}`,
      test: `A new ${one} that needs the agent to ${v} it.`,
    })),
  };
}

export const PERSONAL_DATA_LABELS = {
  replace: "Replace with stand-ins",
  keep: "Keep as is",
  none: "There's none",
};

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const tableOf = (name) =>
  name
    .replace(/\.[^.]+$/, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");

/** A draft's name when none was typed: from the description, else a file. */
export function draftNameOf(description, files = []) {
  const words = String(description || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 5)
    .join(" ");
  const base = words || (files[0] && `${tableOf(files[0].name)} environment`);
  return base
    ? base.charAt(0).toUpperCase() + base.slice(1)
    : "New environment";
}

/**
 * Add files to a design. A new name joins the others; the same name with
 * other contents replaces the earlier copy in place; an identical copy (the
 * same row id: name, size and modified time) is skipped. The note lists the
 * names in each group.
 */
export function mergeDesignFiles(existing, added) {
  const files = [...existing];
  const note = { added: [], replaced: [], skipped: [] };
  added.forEach((f) => {
    const at = files.findIndex((x) => x.name === f.name);
    if (at < 0) {
      files.push(f);
      note.added.push(f.name);
    } else if (files[at].id === f.id) {
      note.skipped.push(f.name);
    } else {
      files[at] = f;
      note.replaced.push(f.name);
    }
  });
  return { files, note };
}

// What one file gives the design, from its name.
function readFile(f) {
  const role = readAsOf(f.name, BUILD_FILES_ACCEPT);
  if (!role)
    return {
      ...f,
      note: `Can't read .${extOf(f.name)} files. Use PDF, Excel, CSV, JSON or text.`,
    };
  if (/scan/i.test(f.name))
    return {
      ...f,
      note: "Scanned pages with no text. Upload a text PDF or describe the policy instead.",
    };
  const out = { ...f, role, tables: [], rules: [] };
  if (role === "Records")
    // shortcut: rows estimated from the size, until the file is parsed.
    out.tables = [
      {
        name: tableOf(f.name),
        rows: Math.max(1, Math.round((f.size || 0) / 80)),
      },
    ];
  if (role === "Rules")
    out.rules =
      POLICY_RULES.find((p) => p.re.test(f.name.toLowerCase()))?.rules ||
      POLICY_FALLBACK;
  return out;
}

/** The builder's open questions, one at a time: who approves, if unsaid. */
function questionsOf(text) {
  const m = text.match(
    /([^.!?\n]*?)\s+(?:needs?|requires?|must (?:get|have))\s+(?:an?\s+)?approval/i,
  );
  if (!m || APPROVER.test(text)) return [];
  const what = m[1].trim().replace(/^(and|but|so)\s+/i, "");
  const subject = what.charAt(0).toLowerCase() + what.slice(1);
  return [
    {
      id: "q-approver",
      prompt: `Who approves ${subject}?`,
      why: "It needs approval, but nothing says whose. I won't guess a policy, so this decides the rule and who takes part.",
      options: [
        {
          label: "A supervisor",
          description:
            "Adds a simulated supervisor who says yes or no when asked.",
        },
        {
          label: "No one, never allowed",
          description: `The rule becomes: never allow ${subject}.`,
        },
      ],
    },
  ];
}

/**
 * The design for a draft at time `now`: the brief, each part of the proposal
 * (null until its read time), the files used and the ones that couldn't be
 * read, the parts still being read, and whether Approve is blocked because
 * nothing says what the agent does.
 */
export function designSummary(draft, now) {
  const { description = "", agent = null } = draft;
  const read = draft.files.map(readFile);
  const sources = read.filter((f) => f.role);
  const unusable = read
    .filter((f) => !f.role)
    .map(({ id, name, note }) => ({ id, name, note }));

  const fileTables = sources.flatMap((f) =>
    f.tables.map((t) => ({ ...t, src: `From ${f.name}` })),
  );
  const fileRules = sources.flatMap((f) =>
    f.rules.map((text) => ({ text, src: `From ${f.name}` })),
  );
  const desc = description.toLowerCase();
  const text = [
    desc,
    ...fileTables.map((t) => `${t.name} records`),
    ...fileRules.map((r) => r.text.toLowerCase()),
  ].join(". ");

  const toolOrigin = (re) =>
    agent
      ? "From the agent's code"
      : re.test(desc)
        ? "From your description"
        : "From your files";
  const preset = TOOLS.filter((t) => t.re.test(text));
  const generic = preset.length ? null : genericDesignOf(desc);
  const toolRows = (generic?.tools || preset).map((t) => ({
    name: t.name,
    desc: t.desc,
    test: t.test,
    src: toolOrigin(t.re),
  }));
  const approveBlocked = !agent && toolRows.length === 0;

  // Records come with what the agent does; a table a file supplies isn't generated again.
  const records = approveBlocked
    ? fileTables
    : [
        ...(generic ? [generic.table] : TABLES)
          .filter(
            (t) =>
              t.re.test(desc) && !fileTables.some((f) => f.name === t.name),
          )
          .map(({ name, rows }) => ({ name, rows, src: "Generated" })),
        ...fileTables,
      ];
  const descRules = description
    .split(/(?<=[.!?\n])\s+/)
    .map((s) => s.trim())
    .filter((s) => s.split(/\s+/).length >= 3 && RULEISH.test(s))
    .map((s) => ({
      text: s,
      src: agent ? "From what to test" : "From your description",
    }));
  const rules = [...descRules, ...fileRules];
  const people = [
    { name: "Caller", note: "the person the agent serves" },
    ...(APPROVER.test(text)
      ? [
          {
            name: text.match(APPROVER)[1].replace(/^./, (c) => c.toUpperCase()),
            src: "Proposed for the scenarios",
          },
        ]
      : []),
  ];
  // Cases come with the tools they test; none until it knows what the agent does.
  const scenarios = toolRows.length
    ? [
        ...toolRows.map((t) => ({
          text: t.test,
          src: "The flow as described",
        })),
        {
          text: `${toolRows[0].name} times out while the customer waits.`,
          src: "When a tool fails",
        },
      ]
    : [];

  const startedAt = (draft.readUntil || 0) - READ_MS;
  const readingParts = Object.keys(READ_AT).filter(
    (part) => now < startedAt + READ_AT[part] * READ_MS,
  );
  const shown = (part, value) => (readingParts.includes(part) ? null : value);

  const ruleCount = fileRules.length;
  const recordCount = fileTables.reduce((n, t) => n + t.rows, 0);
  const adds =
    [
      ruleCount && plural(ruleCount, "rule"),
      recordCount && `${recordCount.toLocaleString()} seeded records`,
    ]
      .filter(Boolean)
      .join(" and ") || "background for the agent";

  return {
    brief: {
      name: draft.name,
      description,
      sourcesLine: sources.length
        ? `adds ${adds}`
        : draft.files.length
          ? "None readable"
          : agent
            ? "The agent alone"
            : "Your description",
      personalData: draft.files.length
        ? PERSONAL_DATA_LABELS[draft.personalChoice] || null
        : null,
      channel: /\b(calls?|calling|phone|voice|dial)\b/i.test(description)
        ? "Voice"
        : "Chat",
      agent: agent
        ? [agent.name, agent.version].filter(Boolean).join(" · ")
        : null,
      scenarioCount: draft.scenarioCount || null,
    },
    records: shown("records", records),
    tools: shown("tools", toolRows),
    rules: shown("rules", rules),
    people: shown("people", people),
    scenarios: shown("scenarios", scenarios),
    questions: readingParts.length
      ? []
      : questionsOf(
          `${description}\n${fileRules.map((r) => r.text).join(" ")}`,
        ),
    sources,
    unusable,
    readingParts,
    approveBlocked,
  };
}
