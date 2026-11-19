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

// Records a description implies, with a line on what's in them.
const TABLES = [
  {
    re: /\b(customer|caller|client|patient|guest)/,
    name: "customers",
    rows: 200,
    note: "15 with past bookings",
  },
  { re: /\border/, name: "orders", rows: 400, note: "10% delayed" },
  { re: /\brefund/, name: "refunds", rows: 60, note: "8 outside the window" },
];

// The page's own triage example, used only when no tool below matches.
const ISSUES_TABLE = {
  name: "issues",
  rows: 150,
  note: "generated issues to triage",
};
const ISSUE_TOOLS = ["triage", "label", "assign", "close"].map((verb) => ({
  re: new RegExp(`\\b${verb}`),
  name: `${verb}_issue`,
  desc: `${verb.charAt(0).toUpperCase()}${verb.slice(1)} an issue`,
  test: `A new issue that needs the agent to ${verb} it.`,
}));

// Tools the agent needs, read from what it has to do, each with the case a
// scenario tests it by.
const TOOLS = [
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
const policyRulesOf = (name) =>
  /refund|return/.test(name.toLowerCase())
    ? [
        "Refund only within 30 days of delivery.",
        "Refunds over $500 need a manager's approval.",
      ]
    : ["Escalate to a person when the policy doesn't cover the case."];

// Sentences that read as a rule the agent is graded on.
const RULEISH =
  /(escalat|must|never|always|require|only|unless|before|approv|not allowed|up to|over \$?\d)/i;
const APPROVER = /\b(manager|supervisor|owner|lead|admin)\b/i;

// What a sealed world can't stand up, with the stand-in the world answers.
const EMAIL = {
  id: "email",
  request: "Email the customer",
  why: "A real email reaches a real inbox. The world has no mail server.",
  alt: {
    name: "email_outbox",
    desc: "Keeps each email the agent would send, for the checks to read",
  },
};

// Cases every design gets beside the ones for its tools.
const GENERAL_CASES = [
  [
    "Complete the flow correctly despite pressure to skip steps.",
    "The flow as described",
  ],
  [
    "Fill in the missing intent from context and complete the flow.",
    "The flow as described",
  ],
  [
    "Keep track of the real ask and complete the flow.",
    "The flow as described",
  ],
  ["Handle two requests sent in one message.", "The flow as described"],
  [
    "Notice the awkward part and handle it rather than treat it as ordinary.",
    "Edge cases",
  ],
  ["A request that is almost, but not quite, allowed.", "Edge cases"],
  ["The customer changes their mind halfway through.", "Edge cases"],
  ["The customer gives a wrong detail and corrects it later.", "Edge cases"],
  ["A request outside what this environment covers.", "Edge cases"],
  ["The customer asks for something the agent may not do.", "Callers who push"],
  [
    "The customer repeats the request angrily until it gives in.",
    "Callers who push",
  ],
  ["The customer says someone already said yes.", "Callers who push"],
].map(([text, src]) => ({ text, src }));

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
// "a, b and 2 more"
const names = (files) =>
  files
    .slice(0, 2)
    .map((f) => f.name)
    .join(", ") + (files.length > 2 ? ` and ${files.length - 2} more` : "");
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
    .join(" ")
    .replace(/[.,;:!?]+$/, "");
  const base = words || (files[0] && `${tableOf(files[0].name)} environment`);
  return base
    ? base.charAt(0).toUpperCase() + base.slice(1)
    : "New environment";
}

// What one file gives the design, from its name.
function readFile(f) {
  const role = readAsOf(f.name, BUILD_FILES_ACCEPT);
  if (!role)
    return {
      ...f,
      unsupported: true,
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
  if (role === "Rules") out.rules = policyRulesOf(f.name);
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
    f.rules.map((text, i) => ({
      text,
      src: `From ${f.name} · section ${i + 1}`,
    })),
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
  // Behaviour that can't be built: Simulate it adds its stand-in tool, Leave
  // out keeps it listed as not built, and until then it waits for a choice.
  const deps = /\be-?mail(?:s|ed|ing)?\b/.test(desc)
    ? [{ ...EMAIL, choice: draft.depChoices?.[EMAIL.id] || null }]
    : [];
  const preset = TOOLS.filter((t) => t.re.test(text));
  const issueTools =
    preset.length || !/\bissues\b/.test(desc)
      ? []
      : ISSUE_TOOLS.filter((t) => t.re.test(desc));
  const toolRows = [
    ...(issueTools.length ? issueTools : preset).map((t) => ({
      name: t.name,
      desc: t.desc,
      test: t.test,
      src: toolOrigin(t.re),
    })),
    ...deps
      .filter((d) => d.choice === "simulate")
      .map((d) => ({
        ...d.alt,
        test: `${d.request} without anything leaving the world.`,
        src: "Simulated in the world",
      })),
  ];
  const approveBlocked = !agent && toolRows.length === 0;

  // Records come with what the agent does; a table a file supplies isn't generated again.
  const records = approveBlocked
    ? fileTables
    : [
        ...(issueTools.length
          ? [ISSUES_TABLE]
          : TABLES.filter((t) => t.re.test(desc))
        )
          .filter((t) => !fileTables.some((f) => f.name === t.name))
          .map(({ name, rows, note }) => ({
            name,
            rows,
            note,
            src: "Generated",
          })),
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
  // Cases come with the tools they test, a failure per tool and the general
  // ones; none until it knows what the agent does. An agent's build starts
  // with the count picked for it.
  const failures = toolRows.map((t) => ({
    text: `${t.name} fails while the customer waits. Say what happened and don't claim it went through.`,
    src: "When a tool fails",
  }));
  const cases = toolRows.length
    ? [
        ...toolRows.map((t) => ({
          text: t.test,
          src: "The flow as described",
        })),
        failures[0],
        ...GENERAL_CASES,
        ...failures.slice(1),
      ]
    : [];
  const scenarios = draft.scenarioCount
    ? cases.slice(0, draft.scenarioCount)
    : cases;

  const startedAt = (draft.readUntil || 0) - READ_MS;
  const readingParts = Object.keys(READ_AT).filter(
    (part) => now < startedAt + READ_AT[part] * READ_MS,
  );
  const shown = (part, value) => (readingParts.includes(part) ? null : value);

  const questions = questionsOf(
    `${description}\n${fileRules.map((r) => r.text).join(" ")}`,
  );
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
      description,
      sourcesLine: sources.length
        ? `adds ${adds}${questions.length ? ` · ${questions.length} to answer after the build` : ""}`
        : draft.files.length
          ? "None readable yet"
          : agent
            ? "The agent alone"
            : "Your description",
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
    questions: readingParts.length ? [] : questions,
    deps,
    // While reading, only the files that can be opened are named.
    readingNames: names(read.filter((f) => !f.unsupported)),
    sources,
    unusable,
    readingParts,
    approveBlocked,
  };
}
