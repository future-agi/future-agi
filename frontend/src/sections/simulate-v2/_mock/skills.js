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
  if (saved) return { skills: saved.skills || [], draft: saved.draft || {} };
  return skillRuntimeOf(agent).canAdd
    ? { skills: SEEDED(), draft: {} }
    : { skills: [], draft: {} };
};

export const latestOf = (skill) => skill.versions[skill.versions.length - 1];

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
        s.id === meta.name ? { ...s, versions: [...s.versions, version] } : s,
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
