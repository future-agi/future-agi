/*
  Review changes (roadmap builder.md Week 1): every unsaved world edit in one
  place, grouped by what it changes, with where it came from — and each one
  accepted or rejected before the version is saved.

  An edit is recorded by envChangePatch as { kind, detail, at } and applied
  in place. The store stamps it as it lands (stampEdit): `before` keeps the
  fields that patch replaced, as they were, and `source` says who made it.
  Undo puts those fields back — only when no later edit changed the same
  ones, since that would undo the later edit too.
*/

/* Bookkeeping keys a patch carries that are not the edit itself. */
const BOOKKEEPING = new Set(["unsavedEnvChanges", "draftBase", "changeSource"]);
const ABSENT = "__absent__";

export const EDIT_AREAS = [
  {
    id: "rules",
    label: "Rules",
    kinds: ["rules", "contract"],
    tab: "contract",
  },
  {
    id: "state",
    label: "Starting state and records",
    kinds: ["seed"],
    tab: "world",
  },
  {
    id: "tools",
    label: "Tools",
    kinds: ["toolbehaviour", "tools-added", "tools-removed", "network"],
    tab: "world",
  },
  { id: "actors", label: "Actors", kinds: ["actors"], tab: "actors" },
  {
    id: "scenarios",
    label: "Scenarios",
    kinds: ["scenarios"],
    tab: "scenarios",
  },
];
export const areaOfEdit = (c) =>
  EDIT_AREAS.find((a) => a.kinds.includes(c.kind)) || {
    id: "other",
    label: "Other",
    tab: "settings",
  };

export const sourceLabelOf = (c) => {
  if (!c.source) return null;
  if (c.source === "chat") return "From the builder chat";
  if (c.source.startsWith("file:")) return `From ${c.source.slice(5)}`;
  return "By hand";
};

/** Where an edit came from, as somewhere to go: the chat message, the file, or its tab. */
export const sourceLinkOf = (c, envState) => {
  if (c.source === "chat") return { kind: "chat", label: "Open in chat" };
  if (c.source?.startsWith("file:")) {
    const name = c.source.slice(5);
    const file = (envState?.sources || []).find((x) => x.name === name);
    /* The place in the file — "p.1", "O-104" — when the change knows it. */
    const where = c.cite?.ref?.startsWith(`${name} · `)
      ? c.cite.ref.slice(name.length + 3)
      : null;
    return file
      ? {
          kind: "file",
          label: `Open ${name}`,
          sourceId: file.id,
          at: c.cite?.at || null,
          where,
        }
      : { kind: "tab", label: "Open" };
  }
  return { kind: "tab", label: "Open" };
};

/* A record's name in a diff line: what a person would call it. */
const nameOf = (v) =>
  v == null || v === ""
    ? "none"
    : typeof v === "boolean"
      ? v
        ? "yes"
        : "no"
      : typeof v !== "object"
        ? String(v)
        : v.name || v.title || v.to || v.text || v.label || v.id || "an item";
const keyOf = (v) =>
  v && typeof v === "object" ? v.id || v.name || JSON.stringify(v) : String(v);

/* What each part of the world is called in the product. */
const FIELD_LABEL = {
  ruleChanges: "Rules",
  dataImports: "Tables",
  scenarios: "Scenarios",
  actors: "Cast",
  customActors: "Actors",
  actorEdits: "Actors",
  toolEnv: "Tools",
  startingState: "Starting state",
  sources: "Sources",
  dataMode: "World data",
  useCaseChanges: "Use cases",
};
/* Stored values, as the screens say them. */
const VALUE_LABEL = {
  dataMode: {
    synthetic: "generated",
    "private-sample": "your records (a private sample)",
  },
  tier: {
    stub: "Fixed",
    replay: "Recorded",
    sim: "Simulated",
    stateful: "Simulated",
    live: "Live",
  },
};
/* What an unset field means — no data mode is generated data. */
const DEFAULT_VALUE = { dataMode: "synthetic" };
/* A record's fields, as the screens name them. Internal ones are left out. */
const FIELD_NAME = {
  title: "title",
  name: "name",
  task: "task",
  situation: "situation",
  expected: "passes when",
  outcome: "passes when",
  persona: "caller",
  tier: "answers with",
  approval: "approval",
  faults: "faults",
  goal: "goal",
  blurb: "role",
  entry: "joins",
  canApprove: "can approve",
  approvalScope: "approves",
  rows: "rows",
  role: "role",
  visibility: "who reads it",
  status: "state",
};
const valueOf = (field, raw) => {
  const v = raw ?? DEFAULT_VALUE[field];
  return (
    VALUE_LABEL[field]?.[v] ??
    (v && typeof v === "object" && !Array.isArray(v)
      ? nameOf(v)
      : Array.isArray(v)
        ? `${v.length} item${v.length === 1 ? "" : "s"}`
        : nameOf(v))
  );
};

/* The fields that differ between two versions of one record, readably. */
const fieldChangesOf = (a, b) =>
  [...new Set([...Object.keys(a || {}), ...Object.keys(b || {})])]
    .filter((f) => FIELD_NAME[f] && !same(a?.[f], b?.[f]))
    .map((f) =>
      a?.[f] === undefined
        ? `${FIELD_NAME[f]}: ${valueOf(f, b?.[f])}`
        : `${FIELD_NAME[f]}: ${valueOf(f, a?.[f])} → ${valueOf(f, b?.[f])}`,
    );

/* A rule's words just before an entry: its last earlier change, else the base rule. */
const ruleTextBefore = (id, earlier, baseRules = []) => {
  const last = [...earlier].reverse().find((r) => r.id === id && r.to);
  if (last) return last.to;
  const n = Number(String(id).replace(/^R/, ""));
  return baseRules[n - 1] || null;
};

/**
 * What an edit changed, as before → after lines a person can read:
 * [{ field, sign: "+" | "−" | "~", text, before? }]. `before` is the old
 * wording when a rule's words changed — shown struck through above the new.
 * Compared with the field as it stands now, so a later edit to the same field
 * shows in it too. `opts.nameOfId(field, id)` names ids (an actor's id → name).
 * `opts.baseOf(field, key)` gives what an entry never set before held (a
 * tool's default tier).
 */
export const diffOf = (c, envState, opts = {}) => {
  const out = [];
  const nameId = (field, v) =>
    (typeof v === "string" && opts.nameOfId?.(field, v)) || nameOf(v);
  Object.entries(c?.before || {}).forEach(([k, was]) => {
    const field = FIELD_LABEL[k];
    if (!field) return;
    const now = envState?.[k];
    /* Never set before: it had its default (the environment's starting cast). */
    const old = was === ABSENT ? opts.baseOf?.(k) ?? undefined : was;

    /* Rules: each entry says what happened to one rule. */
    if (k === "ruleChanges") {
      const earlier = old || [];
      (now || []).slice(earlier.length).forEach((r) => {
        const prev = ruleTextBefore(r.id, earlier, opts.baseRules);
        if (r.kind === "added")
          out.push({ field, sign: "+", text: `${r.id}: ${r.to}` });
        else if (r.kind === "removed")
          out.push({ field, sign: "−", text: `${r.id}: ${prev || "removed"}` });
        else if (!r.removal)
          out.push({
            field,
            sign: "~",
            text: `${r.id}: ${r.to}`,
            before: prev ? `${r.id}: ${prev}` : null,
          });
      });
      return;
    }
    if (was?.__sub) {
      Object.entries(was.__sub).forEach(([sk, o]) => {
        /* Never set before: it had its default (a tool's starting tier). */
        const prev = o === ABSENT ? opts.baseOf?.(k, sk) : o;
        const cur = now?.[sk];
        const who = nameId(k, sk);
        if (isMap(prev) || isMap(cur)) {
          const lines = fieldChangesOf(prev, cur);
          (lines.length ? lines : ["changed"]).forEach((l) =>
            out.push({ field, sign: "~", text: `${who} · ${l}` }),
          );
        } else
          out.push({
            field,
            sign: "~",
            text: `${who}: ${valueOf(sk, prev)} → ${valueOf(sk, cur)}`,
          });
      });
      return;
    }
    if (Array.isArray(old) || Array.isArray(now)) {
      const a = old || [];
      const b = now || [];
      const ak = new Map(a.map((x) => [keyOf(x), x]));
      const bk = new Map(b.map((x) => [keyOf(x), x]));
      bk.forEach((x, key) => {
        if (!ak.has(key)) out.push({ field, sign: "+", text: nameId(k, x) });
        else if (!same(ak.get(key), x)) {
          /* Changed in place: say which of its fields moved. */
          const lines = fieldChangesOf(ak.get(key), x);
          out.push({
            field,
            sign: "~",
            text: `${nameId(k, x)}${lines.length ? ` — ${lines.join("; ")}` : ""}`,
          });
        }
      });
      ak.forEach((x, key) => {
        if (!bk.has(key)) out.push({ field, sign: "−", text: nameId(k, x) });
      });
      return;
    }
    /* The starting state: the facts every run starts from. */
    if (k === "startingState") {
      const facts = (now?.facts || []).map((f) => nameOf(f?.text || f));
      out.push({
        field,
        sign: old ? "~" : "+",
        text: facts.length ? facts.join("; ") : "cleared",
      });
      return;
    }
    /* A keyed map set for the first time (or cleared): one line per entry. */
    if ((isMap(now) && !isMap(old)) || (isMap(old) && !isMap(now))) {
      const was = isMap(old) ? old : {};
      const to = isMap(now) ? now : {};
      const keys = [...new Set([...Object.keys(was), ...Object.keys(to)])];
      const from = Object.fromEntries(
        keys.map((key) => [key, was[key] ?? opts.baseOf?.(k, key)]),
      );
      keys
        .filter((key) => !same(from[key], to[key]))
        .forEach((key) => {
          const who = nameId(k, key);
          const lines =
            isMap(from[key]) || isMap(to[key])
              ? fieldChangesOf(from[key], to[key])
              : [`${valueOf(key, from[key])} → ${valueOf(key, to[key])}`];
          lines.forEach((l) =>
            out.push({
              field,
              sign: from[key] ? "~" : "+",
              text: `${who} · ${l}`,
            }),
          );
        });
      return;
    }
    if (!same(old, now))
      out.push({
        field,
        sign: "~",
        text:
          isMap(old) || isMap(now)
            ? fieldChangesOf(old, now).join("; ") ||
              `${nameOf(old)} → ${nameOf(now)}`
            : `${valueOf(k, old)} → ${valueOf(k, now)}`,
      });
  });
  return out;
};

const isMap = (v) => v && typeof v === "object" && !Array.isArray(v);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/*
  What one field held before. A keyed map (tool settings, actor edits) keeps
  only the entries this edit changed, so edits to different tools stay
  separate: { __sub: { [entry]: old value } }.
*/
const beforeOf = (prevValue, nextValue) => {
  if (prevValue === undefined) return ABSENT;
  if (!isMap(prevValue) || !isMap(nextValue)) return prevValue;
  const keys = new Set([...Object.keys(prevValue), ...Object.keys(nextValue)]);
  const changed = [...keys].filter((k) => !same(prevValue[k], nextValue[k]));
  return {
    __sub: Object.fromEntries(
      changed.map((k) => [
        k,
        prevValue[k] === undefined ? ABSENT : prevValue[k],
      ]),
    ),
  };
};

/* The parts an edit touched: "field" or "field.entry". */
const partsOf = (before) =>
  Object.entries(before || {}).flatMap(([k, v]) =>
    v?.__sub ? Object.keys(v.__sub).map((sk) => `${k}.${sk}`) : [k],
  );
const overlaps = (a, b) =>
  a.some((x) =>
    b.some((y) => x === y || x === y.split(".")[0] || y === x.split(".")[0]),
  );

/** What `fields` replace in `prev`, kept the way an edit's `before` is. */
export const beforeOfFields = (prev, fields) =>
  Object.fromEntries(
    Object.keys(fields)
      .filter((k) => !BOOKKEEPING.has(k))
      .map((k) => [k, beforeOf(prev?.[k], fields[k])]),
  );

/**
 * Called by the store on every patch: when it adds an edit, remember what
 * the fields it replaces held, and who made it. Returns the patch to apply.
 */
export const stampEdit = (prev, patch) => {
  if (!patch || typeof patch !== "object") return patch;
  const { changeSource, ...rest } = patch;
  const before = prev?.unsavedEnvChanges || [];
  const after = rest.unsavedEnvChanges;
  if (!Array.isArray(after) || after.length !== before.length + 1) return rest;
  const last = after[after.length - 1];
  if (last.before) return rest;
  const keys = Object.keys(rest).filter((k) => !BOOKKEEPING.has(k));
  const snapshot = Object.fromEntries(
    keys.map((k) => [k, beforeOf(prev?.[k], rest[k])]),
  );
  return {
    ...rest,
    unsavedEnvChanges: [
      ...after.slice(0, -1),
      { ...last, before: snapshot, source: changeSource || "hand" },
    ],
  };
};

/** Whether edit `i` can be undone by itself, and why not. */
export const undoableOf = (list, i) => {
  const c = list[i];
  if (!c?.before)
    return {
      ok: false,
      why: "Made before single edits could be undone. Discard all puts it back.",
    };
  const mine = partsOf(c.before);
  const later = list
    .slice(i + 1)
    .find((l) => overlaps(partsOf(l.before), mine));
  if (later)
    return {
      ok: false,
      why: `“${later.detail}” changed the same part later. Undo that one first.`,
    };
  return { ok: true };
};

/** Put back what edit `i` replaced and drop it from the list. */
export const undoEditPatch = (envState, i) => {
  const list = envState?.unsavedEnvChanges || [];
  const c = list[i];
  if (!undoableOf(list, i).ok) return null;
  const restored = Object.fromEntries(
    Object.entries(c.before).map(([k, v]) => {
      if (v === ABSENT) return [k, undefined];
      if (!v?.__sub) return [k, v];
      const cur = { ...(envState?.[k] || {}) };
      Object.entries(v.__sub).forEach(([sk, old]) => {
        if (old === ABSENT) delete cur[sk];
        else cur[sk] = old;
      });
      return [k, cur];
    }),
  );
  const rest = [...list.slice(0, i), ...list.slice(i + 1)];
  return {
    ...restored,
    unsavedEnvChanges: rest,
    ...(!rest.length && { draftBase: undefined }),
  };
};

/*
  Accept or reject (data.md §3, TH-8931): every edit waits for a decision —
  undecided, "accepted" or "rejected". Rejecting carries out at once — the
  world goes back to what the edit replaced, so every count, run and save
  already leaves it out — and keeps what the edit set (`after`), so Restore
  puts it back until the version is saved. Edits to the same part go in
  order: a later one is rejected before an earlier one, and an earlier one
  restored before a later one.
*/

/* Write a kept snapshot ({ field: value | ABSENT | { __sub } }) back over the state. */
const applySnapshot = (envState, snap) =>
  Object.fromEntries(
    Object.entries(snap || {}).map(([k, v]) => {
      if (v === ABSENT) return [k, undefined];
      if (!v?.__sub) return [k, v];
      const cur = { ...(envState?.[k] || {}) };
      Object.entries(v.__sub).forEach(([sk, val]) => {
        if (val === ABSENT) delete cur[sk];
        else cur[sk] = val;
      });
      return [k, cur];
    }),
  );

/* What the parts in `before` hold now, kept the same way — what Restore writes back. */
const currentOf = (envState, before) =>
  Object.fromEntries(
    Object.entries(before || {}).map(([k, v]) => {
      if (!v?.__sub)
        return [k, envState?.[k] === undefined ? ABSENT : envState[k]];
      const cur = envState?.[k] || {};
      return [
        k,
        {
          __sub: Object.fromEntries(
            Object.keys(v.__sub).map((sk) => [
              sk,
              cur[sk] === undefined ? ABSENT : cur[sk],
            ]),
          ),
        },
      ];
    }),
  );

const laterOverlapping = (list, i) => {
  const mine = partsOf(list[i]?.before);
  return list.slice(i + 1).filter((l) => overlaps(partsOf(l.before), mine));
};

/** Edit `i`'s decision: null (undecided), "accepted" or "rejected". */
export const decisionOf = (list, i) => list[i]?.decision || null;

/** Whether edit `i` can be rejected now, and why not. */
export const rejectableOf = (list, i) => {
  if (!list[i]?.before)
    return {
      ok: false,
      why: "Made before single edits could be taken out. Discard all puts it back.",
    };
  const later = laterOverlapping(list, i).find(
    (l) => l.decision !== "rejected",
  );
  return later
    ? {
        ok: false,
        why: `“${later.detail}” changed the same part later. Reject that one first.`,
      }
    : { ok: true };
};

/** Whether rejected edit `i` can be restored now, and why not. */
export const restorableOf = (list, i) => {
  const mine = partsOf(list[i]?.before);
  const earlier = list
    .slice(0, i)
    .find(
      (l) => l.decision === "rejected" && overlaps(partsOf(l.before), mine),
    );
  if (earlier)
    return {
      ok: false,
      why: `“${earlier.detail}” changed the same part earlier and is rejected. Restore that one first.`,
    };
  const later = laterOverlapping(list, i).find(
    (l) => l.decision !== "rejected",
  );
  return later
    ? {
        ok: false,
        why: `“${later.detail}” changed the same part since. Reject that one first.`,
      }
    : { ok: true };
};

/** Mark edit `i` accepted, or back to undecided (null). */
export const decidePatch = (envState, i, decision) => ({
  unsavedEnvChanges: (envState?.unsavedEnvChanges || []).map((c, k) =>
    k === i ? { ...c, decision: decision || undefined } : c,
  ),
});

/** Reject edit `i`: put back what it replaced, keep what it set for Restore. */
export const rejectEditPatch = (envState, i) => {
  const list = envState?.unsavedEnvChanges || [];
  const c = list[i];
  if (!rejectableOf(list, i).ok) return null;
  return {
    ...applySnapshot(envState, c.before),
    unsavedEnvChanges: list.map((x, k) =>
      k === i
        ? { ...x, decision: "rejected", after: currentOf(envState, x.before) }
        : x,
    ),
  };
};

/** Restore rejected edit `i`: what it set comes back, undecided again. */
export const restoreEditPatch = (envState, i) => {
  const list = envState?.unsavedEnvChanges || [];
  const c = list[i];
  if (c?.decision !== "rejected" || !restorableOf(list, i).ok) return null;
  return {
    ...applySnapshot(envState, c.after),
    unsavedEnvChanges: list.map((x, k) => {
      if (k !== i) return x;
      const { decision: _d, after: _a, ...rest } = x;
      return rest;
    }),
  };
};

/** The state with rejected edit `c` shown as it was — for reading its diff. */
export const withEditShown = (envState, c) =>
  c?.decision === "rejected"
    ? { ...envState, ...applySnapshot(envState, c.after) }
    : envState;

/** Accept every edit not yet decided. */
export const acceptRestPatch = (envState) => {
  const list = envState?.unsavedEnvChanges || [];
  return {
    unsavedEnvChanges: list.map((c, k) =>
      decisionOf(list, k) ? c : { ...c, decision: "accepted" },
    ),
  };
};

export const reviewCountsOf = (list = []) => {
  const ds = list.map((_, k) => decisionOf(list, k));
  const accepted = ds.filter((d) => d === "accepted").length;
  const rejected = ds.filter((d) => d === "rejected").length;
  return {
    total: list.length,
    accepted,
    rejected,
    undecided: list.length - accepted - rejected,
  };
};
