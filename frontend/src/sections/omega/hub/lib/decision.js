// The decision rules of SPEC §5.4, as the review form applies them before the
// server does: approve needs every flag acknowledged, every checklist item of
// the current rules ticked, a confirmed disclosure with no observed gap, and a
// maintainer other than the one who submitted the version.
//
// Flags are answered per rule: one Yes or No for each group of flags that share
// a checker rule. A Yes acknowledges every flag in its group, so the decision
// sends the same `acknowledgedFlags` list that ticking each flag sent.
import { disclosureGaps, disclosureShapeErrors } from "./disclosure";
import { allFindings } from "./report";
import { REASON_MAX, REASON_MIN } from "../constants";

// One fixed, plain hint per checker rule (services/omega-hub-checker: scan,
// licence, observed and similarity). The hint says what the rule saw and what
// the reviewer checks; the finding itself says where.
const RULE_TEXT = {
  "raw-network": {
    title: "Network module imports",
    hint: "Imports a networking module. Check that the Network part of the disclosure names every host and port it uses.",
  },
  "fetch-dynamic": {
    title: "fetch() calls with a computed URL",
    hint: "fetch() to a URL built at run time. Use the line to find where the URL comes from, and check that the Network part names that host.",
  },
  "child-process": {
    title: "Child processes",
    hint: "Starts another program. Check that the Shell part of the disclosure names each command and when it runs.",
  },
  "env-enumeration": {
    title: "Reads the whole environment",
    hint: "Reads every environment variable, not only the keys it names. Check that it needs this and that the Credentials part says what it reads.",
  },
  "writes-code": {
    title: "Writes code files",
    hint: "Writes a code or JSON file, or a path built at run time. Check what it writes and that the Files part says so; an entry must not change its own files.",
  },
  "home-access": {
    title: "Home directory paths",
    hint: "Reads or writes under the home directory. Check that the Files part of the disclosure names each path.",
  },
  "keeps-running": {
    title: "Keeps running",
    hint: "Starts work that can outlive the main flow. Check that the Keeps running part of the disclosure says when it stops.",
  },
  "proc-access": {
    title: "Paths under /proc",
    hint: "Reads another process's state under /proc. An entry rarely needs this: ask why unless the README says.",
  },
  "signals-processes": {
    title: "Signals other processes",
    hint: "Sends a signal to another process. An entry rarely needs this: ask why unless the README says.",
  },
  "remote-image": {
    title: "Remote images in the README",
    hint: "The README loads an image from another site, which can track readers. Check the host, or ask for the image to ship in the entry.",
  },
  chromium: {
    title: "Drives a browser",
    hint: "The manifest asks for Chromium. Check that the README and the disclosure say which sites it opens.",
  },
  "licence-mismatch": {
    title: "Licence file and manifest disagree",
    hint: "The licence file does not read like the manifest's licence. Check which one is right before you approve.",
  },
  "disclosure-gap": {
    title: "Disclosure gaps",
    hint: "The draft disclosure leaves a part empty although the checks observed something there. Write that part below, or ask the publisher to.",
  },
  "undeclared-copy": {
    title: "Undeclared copy",
    hint: "Code files match a listed entry. Approve only if remix_of names that entry and the difference is material.",
  },
};

// The checker names its worker-module rule after the Node module, so it is
// matched by its prefix.
const WORKER_TEXT = {
  title: "Workers",
  hint: "Starts a worker. Check that the Keeps running part of the disclosure says what the worker does and when it stops.",
};

const GENERIC_HINT = "Read each line and decide whether this entry needs it.";

// Flags whose finding is missing from the report still need an answer.
export const OTHER_FLAGS = "other-flags";

export function ruleText(rule) {
  if (RULE_TEXT[rule]) return RULE_TEXT[rule];
  if (String(rule).startsWith("worker-")) return WORKER_TEXT;
  if (rule === OTHER_FLAGS) return { title: "Other flags", hint: GENERIC_HINT };
  return { title: rule, hint: GENERIC_HINT };
}

export function findingPlace(finding) {
  if (!finding?.file) return "";
  return `${finding.file}${finding.line ? `:${finding.line}` : ""}`;
}

// The flags of a submission grouped by checker rule, in report order. Every id
// in `flags` lands in exactly one group.
export function flagGroups(flags, report) {
  const findings = allFindings(report).filter((f) => f.severity === "flag");
  const groups = new Map();
  for (const id of flags || []) {
    const index = findings.findIndex((f) => f.id === id);
    const finding = index >= 0 ? findings[index] : { id };
    const key = finding.rule || finding.code || OTHER_FLAGS;
    if (!groups.has(key))
      groups.set(key, { key, ...ruleText(key), flags: [], items: [] });
    const group = groups.get(key);
    group.flags.push(id);
    group.items.push({ finding, index: index >= 0 ? index : Infinity });
  }
  return [...groups.values()]
    .map((group) => {
      const items = group.items.sort((a, b) => a.index - b.index);
      return {
        key: group.key,
        title: group.title,
        hint: group.hint,
        flags: group.flags,
        findings: items.map((item) => item.finding),
        order: items[0].index,
      };
    })
    .sort((a, b) => a.order - b.order);
}

// The acknowledged list after a group's answer: Yes adds every flag in the
// group, anything else takes them out.
export function answerFlags(acknowledged, groupFlags, yes) {
  const rest = (acknowledged || []).filter((id) => !groupFlags.includes(id));
  return yes ? [...rest, ...groupFlags] : rest;
}

const plural = (count, word, many = `${word}s`) =>
  `${count} ${count === 1 ? word : many}`;

// The change request a No drafts: one line per group answered No.
export function changeRequestText(groups, answers) {
  return (groups || [])
    .filter((group) => answers?.[group.key] === "no")
    .map((group) => {
      const places = [
        ...new Set(group.findings.map(findingPlace).filter(Boolean)),
      ];
      const shown = places.slice(0, 6).join(", ");
      const more = places.length > 6 ? ` and ${places.length - 6} more` : "";
      return `${group.title}${shown ? ` (${shown}${more})` : ""}: say why the entry needs it, or remove it.`;
    })
    .join("\n")
    .slice(0, REASON_MAX);
}

export function isSubmitter(viewer, submittedBy) {
  if (!viewer || !submittedBy) return false;
  if (viewer.id && submittedBy.id) return viewer.id === submittedBy.id;
  return Boolean(viewer.email) && viewer.email === submittedBy.email;
}

export function approveBlockers({
  flags,
  acknowledged,
  checklistItems,
  checklist,
  disclosure,
  observed,
  groups,
  answers,
  selfApproval = false,
}) {
  const blockers = [];
  const unacked = (flags || []).filter(
    (id) => !(acknowledged || []).includes(id),
  );
  if (unacked.length && groups?.length) {
    const open = groups.filter((group) =>
      group.flags.some((id) => unacked.includes(id)),
    );
    const no = open.filter((group) => answers?.[group.key] === "no").length;
    const unanswered = open.length - no;
    const grouped = new Set(groups.flatMap((group) => group.flags));
    const loose = unacked.filter((id) => !grouped.has(id)).length;
    if (unanswered) blockers.push(`answer ${plural(unanswered, "flag group")}`);
    if (no) blockers.push(`change ${plural(no, "No answer")} to Yes`);
    if (loose) blockers.push(`acknowledge ${plural(loose, "flag")}`);
  } else if (unacked.length) {
    blockers.push(`acknowledge ${plural(unacked.length, "flag")}`);
  }
  const unticked = (checklistItems || []).filter(
    (item) => checklist?.[item.key] !== true,
  );
  if (!checklistItems?.length) blockers.push("load the current checklist");
  else if (unticked.length)
    blockers.push(`tick ${plural(unticked.length, "checklist item")}`);
  const gaps = disclosureGaps(disclosure, observed);
  if (gaps.length) blockers.push(`say what it does under ${gaps.join(", ")}`);
  const shape = Object.keys(disclosureShapeErrors(disclosure));
  if (shape.length && !gaps.length)
    blockers.push(`complete the disclosure (${shape.join(", ")})`);
  if (selfApproval)
    blockers.push(
      "another maintainer must approve it, because you submitted this version",
    );
  return blockers;
}

export function reasonProblem(reason) {
  const length = String(reason || "").trim().length;
  if (length < REASON_MIN)
    return `at least ${REASON_MIN} characters (${length} so far)`;
  if (length > REASON_MAX) return `at most ${REASON_MAX} characters`;
  return null;
}

const trimmed = (value) => String(value || "").trim();

export function decisionPayload({
  decision,
  rulesVersion,
  reason,
  checklistItems,
  acknowledged,
  disclosure,
  notes,
}) {
  if (decision === "approve") {
    return {
      decision,
      rulesVersion,
      checklist: Object.fromEntries(
        (checklistItems || []).map((item) => [item.key, true]),
      ),
      acknowledgedFlags: [...(acknowledged || [])].sort(),
      disclosure: Object.fromEntries(
        Object.entries(disclosure).map(([k, v]) => [k, trimmed(v)]),
      ),
    };
  }
  const payload = { decision, rulesVersion, reason: trimmed(reason) };
  if (decision === "request_changes" && notes?.length) payload.notes = notes;
  return payload;
}

// A moment as an absolute date and time in the viewer's zone, with the zone
// named: "11 Nov 2026, 08:42 UTC".
export function formatStamp(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
}
