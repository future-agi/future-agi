/*
  Skills as testable capabilities (roadmap skills.md): the skills an agent
  loads, each a versioned folder — SKILL.md (name + the description that
  decides when it's used), scripts, references, assets. Importing reads and
  checks a folder; it is never proof the skill helps.

  Saved per agent in workspaceSettings.agentSkills[agentKey]:
    { skills: [{ id, state: "on"|"off"|"library", versions: [...] }],
      draft: { [id]: "on"|"off" } }   — a skill-set change not saved yet
  An agent with nothing saved shows the skills its code already has.
*/
import { sourceKindOf } from "./agentRegistry";
import { currentUser } from "./scenarioProvenance";

const CODE_KINDS = new Set(["repo", "upload", "framework"]);
/* Runtimes that report each skill they load (a skill.load span). */
const OBSERVED_FRAMEWORKS = new Set(["claude-agent-sdk"]);

const daysAgo = (d) => new Date(Date.now() - d * 864e5).toISOString();

/** Whether this agent can take a skill, and whether loading is observable. */
export const skillRuntimeOf = (agent) => {
  const kind = sourceKindOf(agent);
  if (!CODE_KINDS.has(kind))
    return {
      canAdd: false,
      why: "Adding a skill needs the agent's code. Connect its repository or upload it to add one.",
    };
  const fw = agent?.values?.framework;
  if (kind === "framework" && !OBSERVED_FRAMEWORKS.has(fw))
    return {
      canAdd: true,
      observed: false,
      mechanism: "Added to the instructions by the SDK adapter",
      line: "This runtime doesn't report when a skill loads, so activation will show as Unknown. Task results are still observed.",
    };
  return {
    canAdd: true,
    observed: true,
    mechanism:
      kind === "framework"
        ? "Skills folder, loaded by the Claude Agent SDK"
        : "Skills folder in the repository (skills/)",
    line: "The runtime reports each skill it loads, so activation is observed.",
  };
};

const md = (name, description, body, extra = "") =>
  `---\nname: ${name}\ndescription: ${description}\n${extra}---\n\n${body}\n`;

const seedVersion = (n, days, files, note) => ({
  v: n,
  at: daysAgo(days),
  by: "Repository",
  source: {
    kind: "repo",
    path: `skills/${files[0].path.split("/")[0]}`,
    rev: ["3c19ab0", "8e02f4d", "b71c5e9"][n - 1],
  },
  files: files.map((f) => ({
    ...f,
    path: f.path.split("/").slice(1).join("/"),
  })),
  note,
});

/* The skills the agent's code already loads — read from its skills/ folder. */
const SEEDED = () => [
  {
    id: "escalate-to-human",
    state: "on",
    versions: [
      seedVersion(
        1,
        60,
        [
          {
            path: "escalate-to-human/SKILL.md",
            text: md(
              "escalate-to-human",
              "Use when the caller asks for a person.",
              "Offer a transfer and say how long the wait is.",
            ),
          },
        ],
        "First version",
      ),
      seedVersion(
        2,
        34,
        [
          {
            path: "escalate-to-human/SKILL.md",
            text: md(
              "escalate-to-human",
              "Use when the caller asks for a person, or the agent can't finish the task safely.",
              "Offer a transfer and say how long the wait is.\nSummarise the case for the person taking over.",
            ),
          },
        ],
        "Also used when the agent can't finish safely",
      ),
      seedVersion(
        3,
        9,
        [
          {
            path: "escalate-to-human/SKILL.md",
            text: md(
              "escalate-to-human",
              "Use when the caller asks for a person, or the agent can't finish the task safely.",
              "Offer a transfer and say how long the wait is.\nSummarise the case for the person taking over.\nNever transfer before identity is verified.",
              "allowed-tools: transfer_call\n",
            ),
          },
          {
            path: "escalate-to-human/references/handoff-template.md",
            text: "# Handoff summary\n\n- Who is calling\n- What they asked for\n- What was already tried\n",
          },
        ],
        "Verify identity before a transfer; handoff template",
      ),
    ],
  },
  {
    id: "payment-failures",
    state: "on",
    versions: [
      seedVersion(
        1,
        21,
        [
          {
            path: "payment-failures/SKILL.md",
            text: md(
              "payment-failures",
              "Use when a card payment fails, is declined or needs a retry.",
              "Read the decline code before saying anything.\nOffer another card once; never retry the same card more than twice.",
              "allowed-tools: get_payment_status\n",
            ),
          },
          {
            path: "payment-failures/assets/decline-codes.csv",
            text: "code,meaning,retry\n05,Do not honour,no\n51,Insufficient funds,no\n91,Issuer unavailable,yes\n",
          },
        ],
        "First version",
      ),
    ],
  },
  {
    id: "holiday-greetings",
    state: "off",
    versions: [
      seedVersion(
        1,
        320,
        [
          {
            path: "holiday-greetings/SKILL.md",
            text: md(
              "holiday-greetings",
              "Use in December to add a seasonal greeting.",
              "Add one short seasonal greeting at the start.",
            ),
          },
        ],
        "First version",
      ),
      seedVersion(
        2,
        300,
        [
          {
            path: "holiday-greetings/SKILL.md",
            text: md(
              "holiday-greetings",
              "Use in the last two weeks of December to add a seasonal greeting.",
              "Add one short seasonal greeting at the start.\nSkip it when the caller is upset.",
            ),
          },
        ],
        "Only the last two weeks; skip when upset",
      ),
    ],
  },
];

/** The agent's skills and draft — saved, or what its code already has. */
export const skillSetOf = (agent, settings) => {
  const saved = settings?.agentSkills?.[agent?.key];
  if (saved)
    return {
      skills: saved.skills || [],
      draft: saved.draft || {},
      tests: saved.tests || [],
    };
  return skillRuntimeOf(agent).canAdd
    ? { skills: SEEDED(), draft: {}, tests: [] }
    : { skills: [], draft: {}, tests: [] };
};

export const latestOf = (skill) => skill.versions[skill.versions.length - 1];
/** The version a loaded skill runs — older than the latest after an import. */
export const loadedVOf = (skill) => skill.loadedV ?? latestOf(skill).v;

/** A skill's state with the draft applied. */
export const draftStateOf = (skill, draft) => draft?.[skill.id] || skill.state;

export const draftChangesOf = (set) =>
  Object.entries(set.draft || {})
    .map(([id, to]) => ({ skill: set.skills.find((s) => s.id === id), to }))
    .filter((c) => c.skill && c.skill.state !== c.to);

/* ── Reading a folder ──────────────────────────────────────────────────── */

const FOLDERS = { scripts: "script", references: "reference", assets: "asset" };
const TEXT_EXT = /\.(md|txt|py|js|ts|sh|json|ya?ml|csv|toml)$/i;
const LANG = { py: "Python", js: "JavaScript", ts: "TypeScript", sh: "Shell" };

export const fileKindOf = (path) =>
  path === "SKILL.md" ? "skill" : FOLDERS[path.split("/")[0]] || "other";

/** name / description / allowed-tools from SKILL.md's frontmatter. */
export const frontmatterOf = (text) => {
  const m = /^---\n([\s\S]*?)\n---/.exec(text || "");
  if (!m) return null;
  return Object.fromEntries(
    m[1]
      .split("\n")
      .map((l) => /^([\w-]+):\s*(.*)$/.exec(l))
      .filter(Boolean)
      .map((x) => [x[1], x[2].trim()]),
  );
};

const metaErrorsOf = (fm) => {
  if (!fm)
    return [
      "SKILL.md has no frontmatter (the --- block with name and description).",
    ];
  const out = [];
  if (!fm.name) out.push("name is missing.");
  else if (!/^[a-z0-9-]+$/.test(fm.name))
    out.push(
      `name "${fm.name}" must be lowercase letters, numbers and hyphens.`,
    );
  if (!fm.description)
    out.push("description is missing — it decides when the skill is used.");
  return out;
};

/* Example folders in the agent's repository. */
const REPO_FOLDERS = {
  "skills/refund-retry": [
    {
      path: "SKILL.md",
      text: md(
        "refund-retry",
        "Use when a refund fails with a retryable processor error (timeout, 91, 96). Not for new refunds or cancellations.",
        "1. Call get_refund_status and read the processor code.\n2. If the code is retryable, run scripts/retry_refund.py once.\n3. Tell the caller the new expected date. Never promise a date the processor didn't give.\n4. If the retry fails, hand off with references/refund-policy.md section 4.",
        "allowed-tools: get_refund_status, issue_refund\n",
      ),
    },
    {
      path: "scripts/retry_refund.py",
      text: 'import os\n\n# Retries one refund through the processor. No other network access.\nAPI_KEY = os.environ["PAYMENTS_API_KEY"]\n\ndef retry(order_id, refund_id):\n    status = get_refund_status(order_id)\n    if status.code not in ("timeout", "91", "96"):\n        return status\n    return issue_refund(order_id, status.amount, caller_confirmed=True)\n',
    },
    {
      path: "references/refund-policy.md",
      text: "# Refund policy\n\n## 4. Failed refunds\nAfter one failed retry, hand off to a person with the processor code.\n",
    },
    {
      path: "assets/retry-codes.csv",
      text: "code,retryable\ntimeout,yes\n91,yes\n96,yes\n05,no\n",
    },
  ],
  "skills/booking-cancel": [
    {
      path: "README.md",
      text: md(
        "booking-cancel",
        "Use when the caller wants to cancel a booking.",
        "Read the fare rules, quote the fee, cancel only after the caller agrees.",
        "allowed-tools: fare_rules, cancel_booking\n",
      ),
    },
    {
      path: "scripts/fee.py",
      text: "def cancellation_fee(fare, days_out):\n    return 0 if days_out > 14 else fare.fee\n",
    },
  ],
  "skills/lounge-access": [
    {
      path: "SKILL.md",
      text: "---\nname: Lounge Access\n---\n\nCheck the ticket class and status before offering a lounge pass.\n",
    },
  ],
  "skills/refund-cheatsheet": [
    {
      path: "SKILL.md",
      text: md(
        "refund-cheatsheet",
        "Use for any refund question.",
        "## Cases and what to answer\n- Never issue a refund outside the return window without a supervisor. Refuse, then offer a supervisor.\n- Never read back more than the last four digits of a card. Read only the last four.",
        "allowed-tools: issue_refund\n",
      ),
    },
  ],
  "skills/fare-calculator": [
    {
      path: "SKILL.md",
      text: md(
        "fare-calculator",
        "Use when the caller asks what a change would cost.",
        "Run bin/farecalc with the old and new itinerary.",
        "allowed-tools: fare_rules\n",
      ),
    },
    { path: "bin/farecalc", binary: true, size: "4.1 MB" },
  ],
};
export const REPO_EXAMPLES = Object.keys(REPO_FOLDERS);

/**
 * Read a skill folder. `files` comes from an upload; otherwise the repo
 * path is looked up. `repairs` carries the fixes the user chose:
 * { readme: true } uses README.md as SKILL.md, { skillMd: text } replaces
 * its text, { drop: [paths] } leaves files out.
 * → { ok: true, files, meta } or { ok: false, error: { code, title, lines } }
 */
export const readSkillFolder = ({ path, files: uploaded }, repairs = {}) => {
  let files = uploaded || REPO_FOLDERS[(path || "").replace(/\/+$/, "")];
  if (!files)
    return {
      ok: false,
      error: {
        code: "not_found",
        title: "No folder at that path",
        lines: [`Nothing at ${path || "(empty)"} in this revision.`],
      },
    };
  files = files.filter((f) => !(repairs.drop || []).includes(f.path));
  if (repairs.readme)
    files = files.map((f) =>
      f.path === "README.md" ? { ...f, path: "SKILL.md" } : f,
    );
  if (repairs.skillMd != null)
    files = files.map((f) =>
      f.path === "SKILL.md" ? { ...f, text: repairs.skillMd } : f,
    );
  const skillMd = files.find((f) => f.path === "SKILL.md");
  if (!skillMd)
    return {
      ok: false,
      error: {
        code: "missing_skill_md",
        title: "No SKILL.md in this folder",
        lines: [
          "A skill folder needs a SKILL.md with its name and description.",
          files.some((f) => f.path === "README.md")
            ? "README.md has a skill frontmatter — it may be the SKILL.md under another name."
            : "Found: " + files.map((f) => f.path).join(", "),
        ],
        canUseReadme: files.some((f) => f.path === "README.md"),
      },
    };
  const binaries = files.filter((f) => f.binary);
  if (binaries.length)
    return {
      ok: false,
      error: {
        code: "unsupported",
        title: "Unsupported resource",
        lines: binaries.map(
          (b) =>
            `${b.path} is a compiled binary (${b.size}). Skills can run scripts we can read, not binaries.`,
        ),
        drop: binaries.map((b) => b.path),
      },
    };
  const fm = frontmatterOf(skillMd.text);
  const errs = metaErrorsOf(fm);
  if (errs.length)
    return {
      ok: false,
      error: {
        code: "invalid_metadata",
        title: "Invalid metadata in SKILL.md",
        lines: errs,
        skillMd: skillMd.text,
      },
    };
  return { ok: true, files, meta: metaOf(fm, files) };
};

/** What the folder declares and touches — read from its files, nothing run. */
const metaOf = (fm, files) => {
  const all = files.map((f) => f.text || "").join("\n");
  const credentials = [
    ...new Set(
      [
        ...all.matchAll(
          /environ\[\s*"([A-Z0-9_]+)"\s*\]|\$\{?([A-Z][A-Z0-9_]{3,})\}?/g,
        ),
      ].map((m) => m[1] || m[2]),
    ),
  ];
  return {
    name: fm.name,
    description: fm.description,
    tools: (fm["allowed-tools"] || "")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean),
    scripts: files
      .filter((f) => fileKindOf(f.path) === "script")
      .map((f) => ({
        path: f.path,
        lang: LANG[f.path.split(".").pop()] || "Script",
        network: /requests|fetch|http/.test(f.text || ""),
      })),
    reads: files
      .filter((f) => ["reference", "asset"].includes(fileKindOf(f.path)))
      .map((f) => f.path),
    credentials,
  };
};

/** Read a real folder picked in the browser (webkitdirectory). */
export const readUploadedFiles = async (fileList) => {
  const list = Array.from(fileList || []);
  const root = list[0]?.webkitRelativePath?.split("/")[0] || "";
  const files = await Promise.all(
    list
      .filter((f) => !/(^|\/)\./.test(f.webkitRelativePath))
      .map(async (f) => {
        const path =
          f.webkitRelativePath.split("/").slice(1).join("/") || f.name;
        if (!TEXT_EXT.test(path) && path !== "SKILL.md")
          return {
            path,
            binary: f.size > 0,
            size: `${Math.max(1, Math.round(f.size / 1024))} KB`,
          };
        return {
          path,
          text: f.size > 200000 ? "(too large to preview)" : await f.text(),
        };
      }),
  );
  return { name: root, files };
};

/** Add an imported folder: a new skill, or the next version of one with the same name. */
export const importSkill = (set, { meta, files, source }) => {
  const existing = set.skills.find((s) => s.id === meta.name);
  const version = {
    v: existing ? latestOf(existing).v + 1 : 1,
    at: new Date().toISOString(),
    by: currentUser().name,
    source,
    files,
    note: existing ? "Imported a new revision" : "Imported",
  };
  const skills = existing
    ? set.skills.map((s) =>
        s.id === meta.name
          ? /* A loaded skill keeps loading the version it had until a saved change. */
            { ...s, loadedV: loadedVOf(s), versions: [...s.versions, version] }
          : s,
      )
    : [...set.skills, { id: meta.name, state: "library", versions: [version] }];
  return {
    ...set,
    skills,
    imported: { id: meta.name, v: version.v, isNewVersion: !!existing },
  };
};

/** Files that changed between two versions of a skill. */
export const versionChangesOf = (prev, next) => {
  const a = new Map((prev?.files || []).map((f) => [f.path, f.text]));
  const b = new Map((next?.files || []).map((f) => [f.path, f.text]));
  const out = [];
  b.forEach((t, p) => {
    if (!a.has(p)) out.push({ path: p, change: "added" });
    else if (a.get(p) !== t) out.push({ path: p, change: "changed" });
  });
  a.forEach((_, p) => {
    if (!b.has(p)) out.push({ path: p, change: "removed" });
  });
  return out;
};

const slug = (name) =>
  String(name || "agent")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
export const skillImportCli = (agent, path) =>
  `agent-learn skill import ${path || "skills/<name>"} --agent ${slug(agent?.name)}`;

/* ── Testing a skill (skills.md "Choose tests", "Run and compare") ────── */

const STOP = new Set(
  "use when the a an or and to of in for is it its not new with any be can can't cant that this on caller agent asks task".split(
    " ",
  ),
);
const wordsOf = (text) =>
  new Set(
    String(text || "")
      .toLowerCase()
      .replace(/[^a-z0-9 ]+/g, " ")
      .split(/\s+/)
      .filter((w) => w.length > 3 && !STOP.has(w))
      .map((w) => w.replace(/(ing|ed|es|s)$/, "")),
  );
export const skillWordsOf = (skill) => {
  const md = latestOf(skill).files.find((f) => f.path === "SKILL.md")?.text;
  const fm = frontmatterOf(md) || {};
  return wordsOf(`${skill.id.replace(/-/g, " ")} ${fm.description || ""}`);
};
const hits = (words, sc) => {
  const hay = wordsOf(
    `${sc.title} ${sc.summary || ""} ${sc.task || ""} ${sc.situation || ""}`,
  );
  return [...words].filter((w) => hay.has(w));
};

export const CASE_GROUPS = [
  {
    id: "positive",
    label: "Should use it",
    line: "The skill should load and change what the agent does.",
  },
  {
    id: "negative",
    label: "Shouldn't use it",
    line: "The skill should stay unused. Loading here is a wrong activation.",
  },
  {
    id: "interaction",
    label: "Next to other skills",
    line: "Another loaded skill also fits — checks for overlap, conflicting steps and duplicate tool calls.",
  },
];

/**
 * Suggested cases from the environment's scenarios: ones that mention what
 * the skill is for (positive), ones that also fit another loaded skill
 * (interaction), and a few unrelated ones it must leave alone (negative).
 * Each says why it was picked.
 */
export const suggestCasesOf = (skill, others, scenarios) => {
  const mine = skillWordsOf(skill);
  const theirs = others.map((o) => ({ id: o.id, words: skillWordsOf(o) }));
  const out = [];
  const negatives = [];
  scenarios
    .filter((sc) => !sc.archived && !sc.retired && !sc.proposed)
    .forEach((sc) => {
      const m = hits(mine, sc);
      const other = theirs
        .map((o) => ({ id: o.id, h: hits(o.words, sc) }))
        .find((o) => o.h.length);
      if (m.length && other)
        out.push({
          scenarioId: sc.id,
          title: sc.title,
          detail: [sc.summary, sc.persona?.name].filter(Boolean).join(" · "),
          group: "interaction",
          why: `Mentions ${m.slice(0, 2).join(", ")}, and ${other.id} also fits (${other.h[0]})`,
        });
      else if (m.length)
        out.push({
          scenarioId: sc.id,
          title: sc.title,
          detail: [sc.summary, sc.persona?.name].filter(Boolean).join(" · "),
          group: "positive",
          why: `Mentions ${m.slice(0, 3).join(", ")}`,
        });
      else
        negatives.push({
          scenarioId: sc.id,
          title: sc.title,
          detail: [sc.summary, sc.persona?.name].filter(Boolean).join(" · "),
          group: "negative",
          why: other
            ? `Fits ${other.id}, not this skill`
            : "Nothing in it matches the skill's description",
        });
    });
  /* Unrelated cases that fit another skill first — the likeliest wrong loads. */
  negatives.sort((a, b) => b.why.startsWith("Fits") - a.why.startsWith("Fits"));
  return [...out, ...negatives.map((n, i) => ({ ...n, picked: i < 3 }))].map(
    (c) => ({ picked: true, ...c }),
  );
};

/**
 * What runs side by side. Without the skill, the agent's current version,
 * and the candidate with the skill version under test. When the current
 * version doesn't load the skill, it is the no-skill baseline.
 */
export const testConfigsOf = (skill, set, agent) => {
  const loaded = set.skills.filter((s) => s.state === "on");
  const cur = loaded.find((s) => s.id === skill.id);
  const ver = (s) => `${s.id} v${loadedVOf(s)}`;
  const base = loaded.filter((s) => s.id !== skill.id);
  const at = (s) => ({ id: s.id, v: loadedVOf(s) });
  const candidate = [...base.map(at), { id: skill.id, v: latestOf(skill).v }];
  const configs = [];
  if (cur) {
    const curV = loadedVOf(cur);
    configs.push({
      id: "none",
      label: `Without ${skill.id}`,
      detail: base.map(ver).join(", ") || "No skills",
      skills: base.map(at),
    });
    if (curV !== latestOf(skill).v)
      configs.push({
        id: "current",
        label: `${agent.production || agent.latest} as it is`,
        detail: `${skill.id} v${curV}`,
        skills: [...base.map(at), { id: skill.id, v: curV }],
      });
  } else {
    configs.push({
      id: "current",
      label: `${agent.production || agent.latest} as it is`,
      detail: `Doesn't load ${skill.id}, so it's the no-skill baseline · ${base.map(ver).join(", ") || "no skills"}`,
      skills: base.map(at),
    });
  }
  /* Already loaded at its latest: what's tested is the version as it is. */
  const same = cur && loadedVOf(cur) === latestOf(skill).v;
  configs.push({
    id: "candidate",
    label: same
      ? `${agent.production || agent.latest} as it is`
      : `With ${skill.id} v${latestOf(skill).v}`,
    detail: same
      ? `Loads ${skill.id} v${latestOf(skill).v}`
      : "Draft skill set, not a saved version",
    skills: candidate,
    candidate: true,
    asIs: same,
  });
  return configs;
};

export const TEST_SECONDS = 14;
export const testStatusOf = (test, now = Date.now()) => {
  if (test.cancelledAt) return "cancelled";
  return now - new Date(test.startedAt) >= TEST_SECONDS * 1000
    ? "done"
    : "running";
};

export const startTestPatch = (set, test) => ({
  ...set,
  tests: [
    ...(set.tests || []),
    {
      ...test,
      id: `ST-${101 + (set.tests || []).length}`,
      startedAt: new Date().toISOString(),
      by: currentUser().name,
    },
  ],
});

export const skillTestCli = ({ skill, agent, env, cases, repeats }) => {
  const ids = (g) =>
    cases
      .filter((c) => c.picked && c.group === g)
      .map((c) => c.scenarioId)
      .join(",");
  return [
    `agent-learn skill test ${skill?.id || "<skill>"} --agent ${slug(agent?.name)}`,
    `--environment ${slug(env?.name)}`,
    ids("positive") && `--positive ${ids("positive")}`,
    ids("negative") && `--negative ${ids("negative")}`,
    ids("interaction") && `--interaction ${ids("interaction")}`,
    `--repeats ${repeats} --compare no-skill,current,candidate`,
  ]
    .filter(Boolean)
    .join(" ");
};

/* ── Results (skills.md "What results reveal") ──────────────────────── */

const rand = (str) => {
  let h = 2166136261;
  for (let i = 0; i < str.length; i += 1)
    h = Math.imul(h ^ str.charCodeAt(i), 16777619);
  return (h >>> 0) / 2 ** 32;
};

/**
 * A skill that carries the test's answers isn't an improvement: any tested
 * case whose title or expected outcome appears in the skill's files.
 */
export const contaminationOf = (skill, v, cases, scenarios = []) => {
  const files = skill?.versions.find((x) => x.v === v)?.files || [];
  const out = [];
  cases.forEach((c) => {
    const sc = scenarios.find((x) => x.id === c.scenarioId) || {};
    const needles = [c.title, sc.expected && sc.expected.slice(0, 60)].filter(
      (n) => n && n.length > 24,
    );
    const file = files.find((f) =>
      needles.some((n) =>
        (f.text || "").toLowerCase().includes(n.toLowerCase()),
      ),
    );
    if (file) out.push({ title: c.title, file: file.path });
  });
  return out.filter((x, i, a) => a.findIndex((y) => y.title === x.title) === i);
};

/** The config results are measured against: no skill, else the current version. */
export const baselineOf = (test) =>
  test.configs.find((c) => c.id === "none") ||
  test.configs.find((c) => !c.candidate);

/**
 * Per case, per configuration: passes out of the repeats, whether the skill
 * loaded (only when the runtime reports it), and what went wrong. Seeded by
 * the test so it reads the same every time.
 */
export const testResultsOf = (test, skill) => {
  const reps = test.repeats;
  const fm =
    frontmatterOf(
      (
        skill?.versions.find((x) => x.v === test.v) ||
        (skill && latestOf(skill))
      )?.files.find((f) => f.path === "SKILL.md")?.text,
    ) || {};
  const tool = (fm["allowed-tools"] || "").split(",")[0].trim();
  /* A description that says what it's not for ("Not for card questions")
     keeps the skill off those cases. */
  const excluded = wordsOf(
    (fm.description || "").toLowerCase().split("not for")[1] || "",
  );
  /* The likeliest wrong load: an unrelated case another skill fits. */
  const wrongCase =
    test.cases.find(
      (c) => c.group === "negative" && /^Fits /.test(c.why || ""),
    ) || test.cases.find((c) => c.group === "negative");
  const clashCase = test.cases.find((c) => c.group === "interaction");
  const perRun =
    test.estimate?.high && test.runs ? test.estimate.high / test.runs : 3.6;
  const configs = test.configs.map((cfg) => {
    const loads = cfg.skills.find((x) => x.id === test.skillId);
    const fresh = loads && loads.v === test.v;
    const rows = test.cases.map((c) => {
      const r = rand(`${test.id}|${cfg.id}|${c.scenarioId}`);
      let p;
      let loaded = 0;
      let note = null;
      if (c.group === "positive") {
        p = !loads ? 0.25 + r * 0.3 : fresh ? 0.86 + r * 0.14 : 0.55 + r * 0.2;
        loaded = loads ? (r < 0.2 ? reps - 1 : reps) : 0;
        if (!loads && p < 0.5) note = "Gave up after the first processor error";
      } else if (c.group === "negative") {
        p = 0.9 + r * 0.1;
        const ruledOut = [...wordsOf(`${c.title} ${c.detail || ""}`)].some(
          (w) => excluded.has(w),
        );
        if (
          loads &&
          fresh &&
          c.scenarioId === wrongCase?.scenarioId &&
          !ruledOut
        ) {
          loaded = Math.max(1, Math.round(reps * 0.67));
          p = (reps - loaded) / reps;
          note = test.observed
            ? `Loaded here${tool ? ` and called ${tool}` : ""}, which this task doesn't need`
            : `Called ${tool || "a refund tool"}, which this task doesn't need`;
        }
      } else {
        p = loads ? 0.7 + r * 0.2 : 0.65 + r * 0.2;
        loaded = loads ? reps : 0;
        if (loads && fresh && c.scenarioId === clashCase?.scenarioId) {
          p = Math.max(0, (reps - 1) / reps);
          note = "Both skills gave steps; one tool call ran twice";
        }
      }
      const passes = Math.min(reps, Math.max(0, Math.round(p * reps)));
      return {
        case: c,
        passes,
        loaded: test.observed && loads ? loaded : null,
        note: passes < reps ? note : null,
      };
    });
    const cost = perRun * (loads ? (fresh ? 1.12 : 1.08) : 1);
    const latency = 2.1 + (loads ? (fresh ? 0.4 : 0.3) : 0);
    return { cfg, rows, cost, latency };
  });
  return configs;
};

/** Totals per group for one configuration's rows. */
const tally = (rows, group) => {
  const g = rows.filter((x) => x.case.group === group);
  return {
    passes: g.reduce((a, x) => a + x.passes, 0),
    n: g.length,
    loaded: g.some((x) => x.loaded != null)
      ? g.reduce((a, x) => a + (x.loaded || 0), 0)
      : null,
  };
};

/**
 * The test read as a decision: success where it should help, wrong loads
 * and regressions where it shouldn't, cost and latency — kept apart, so a
 * gain with a regression reads as a tradeoff, not a win.
 */
export const testSummaryOf = (test, skill, scenarios) => {
  const results = testResultsOf(test, skill);
  const base =
    results.find((r) => r.cfg.id === baselineOf(test)?.id) || results[0];
  const cand =
    results.find((r) => r.cfg.candidate) || results[results.length - 1];
  const reps = test.repeats;
  const groups = ["positive", "negative", "interaction"].map((g) => {
    const b = tally(base.rows, g);
    const c = tally(cand.rows, g);
    return {
      id: g,
      n: b.n,
      runs: b.n * reps,
      base: b.passes,
      cand: c.passes,
      loaded: c.loaded,
    };
  });
  const [pos, neg, inter] = groups;
  const regressions = cand.rows.filter((r) => {
    const b = base.rows.find((x) => x.case.scenarioId === r.case.scenarioId);
    return r.passes < b.passes && r.case.group !== "positive";
  });
  const contaminated = contaminationOf(skill, test.v, test.cases, scenarios);
  /* Where it should help: its own cases, or the shared ones when it has none. */
  const help = pos.n ? pos : inter;
  const gain = help.cand - help.base;
  const verdict = contaminated.length
    ? "rejected"
    : gain > 0 && regressions.length
      ? "tradeoff"
      : gain > 0
        ? "helps"
        : "no_gain";
  return {
    results,
    base,
    cand,
    groups,
    help,
    pos,
    neg,
    inter,
    regressions,
    contaminated,
    verdict,
    reps,
  };
};

export const VERDICTS = {
  helps: { label: "Helps, no side effects found", tone: "passed" },
  tradeoff: { label: "Helps, with a regression", tone: "needs_input" },
  no_gain: { label: "No improvement", tone: "cancelled" },
  rejected: { label: "Rejected: carries the test's answers", tone: "failed" },
};

/** What the skill's finished tests showed — for its detail page. */
export const skillEvidenceOf = (skill, tests, runtime, scenariosOf) => {
  const mine = (tests || []).filter(
    (t) => t.skillId === skill.id && testStatusOf(t) === "done",
  );
  const cases = {
    positive: new Set(),
    negative: new Set(),
    interaction: new Set(),
  };
  const failures = [];
  mine.forEach((t) => {
    t.cases.forEach((c) => cases[c.group]?.add(c.scenarioId));
    const sum = testSummaryOf(
      t,
      skill,
      scenariosOf ? scenariosOf(t.envId) : [],
    );
    sum.regressions.forEach((r) =>
      failures.push({ test: t.id, v: t.v, title: r.case.title, note: r.note }),
    );
  });
  return {
    tests: mine,
    runtime: mine.length
      ? `${runtime.mechanism} · activation ${mine.some((t) => t.observed) ? "observed" : "unknown"} · ${mine.map((t) => t.id).join(", ")}`
      : null,
    coverage: Object.fromEntries(
      Object.entries(cases).map(([k, v]) => [k, v.size]),
    ),
    failures,
  };
};
