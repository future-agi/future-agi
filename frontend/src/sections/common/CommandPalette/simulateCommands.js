// ⌘K items that only exist in prototype mode (notify BUILD-BRIEF §3.1, §4.0):
//  - Settings: "View as Viewer" / "View as Member" / "View as Editor" (the
//    two roles the tab isn't using), "Reloads the page as that role".
//  - L7b absent-sweep: no Simulate group. Its flags only changed the Round 5
//    stage (r5), which is absent with its CI and notification paths (Q12).
//  - Demo stage: every stage by its label (store.js PROTO_STAGE_LABELS), the
//    current one marked; choosing one reloads the page at that stage.
// Components never import src/_mock/prototype (utils/prototype.js), so the
// role key is repeated here (the same value as protoRole.js;
// simulateCommands.test.js keeps them equal) and the stages come from
// window.__PROTOTYPE__ at run time.
export const PROTO_ROLE_KEY = "fa.proto.role";

export const ROLE_LABELS = Object.freeze({
  viewer: "Viewer",
  member: "Member",
  editor: "Editor",
});

const storage = () => {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};
const read = (key) => {
  try {
    return storage()?.getItem(key) ?? null;
  } catch {
    return null;
  }
};
const write = (key, value) => {
  try {
    if (value == null) storage()?.removeItem(key);
    else storage()?.setItem(key, value);
  } catch {
    /* storage blocked: the URL param still carries it */
  }
};

/** The tab's prototype role: `viewer`, `member` or `editor`. */
export const protoRole = () => {
  const v = read(PROTO_ROLE_KEY);
  return v && Object.hasOwn(ROLE_LABELS, v) ? v : "editor";
};

/**
 * Reload this page with URL params changed (null removes one), the way
 * `?proto_stage` / `?proto_role` are read on a full load.
 */
export function reloadWith(params) {
  const url = new URL(window.location.href);
  Object.entries(params).forEach(([key, value]) => {
    if (value == null) url.searchParams.delete(key);
    else url.searchParams.set(key, value);
  });
  window.location.assign(url.toString());
}

/** What the prototype items read: role and stages of this tab. */
export function prototypeState() {
  const proto = typeof window !== "undefined" ? window.__PROTOTYPE__ : null;
  let stages = [];
  let stage = null;
  try {
    stages = proto?.stages?.() ?? [];
    stage = proto?.stage?.() ?? null;
  } catch {
    /* the mock isn't ready: no stage items */
  }
  return { role: protoRole(), stages, stage };
}

function roleCommands(role, reload) {
  return Object.keys(ROLE_LABELS)
    .filter((r) => r !== role)
    .map((next) => ({
      id: `proto.role.${next}`,
      group: "Settings",
      label: `View as ${ROLE_LABELS[next]}`,
      keywords: [
        "role",
        "viewer",
        "member",
        "editor",
        "permission",
        "prototype",
      ],
      icon: "mdi:account-switch-outline",
      hint: "Reloads the page as that role",
      run: () => {
        write(PROTO_ROLE_KEY, next);
        reload({ proto_role: next });
      },
    }));
}

/**
 * L7c g1 (cmdk:demo-stage-restart): drop one stage's W4 tables (comments,
 * assignments, access grants, share and review links, audit, ...), the
 * localStorage keys `fa.proto.w4.<stage>.*` (w4Persist.js), which otherwise
 * survive a reload on purpose. Returns the keys removed.
 */
export function clearStageTables(stage, ls) {
  if (!stage) return [];
  const prefix = `fa.proto.w4.${stage}.`;
  let store = ls;
  try {
    store = store ?? window.localStorage;
  } catch {
    return [];
  }
  if (!store) return [];
  const keys = [];
  try {
    for (let i = 0; i < store.length; i += 1) {
      const k = store.key(i);
      if (k && k.startsWith(prefix)) keys.push(k);
    }
    keys.forEach((k) => store.removeItem(k));
  } catch {
    /* storage blocked: nothing persisted */
  }
  return keys;
}

/**
 * NB-00 "choose it to start it again": before a stage pick reloads, the mock
 * drops the session changes that reload would otherwise replay (W3 C's
 * copy and move journal) and, L7c g1, the stage's W4 tables, so the stage
 * loads as seeded.
 */
function startStageAgain(stage) {
  try {
    window.__PROTOTYPE__?.startStageAgain?.();
  } catch {
    /* the mock isn't ready: the reload still picks the stage */
  }
  clearStageTables(stage);
}

function stageCommands(stages, stage, reload) {
  return stages.map(({ id, label }) => ({
    id: `proto.stage.${id}`,
    group: "Demo stage",
    label,
    keywords: ["stage", "demo", "round", id],
    icon: id === stage ? "mdi:check-circle-outline" : "mdi:map-marker-path",
    hint:
      id === stage
        ? "Current stage · choose it to start it again"
        : "Reloads the page at this stage",
    run: () => {
      startStageAgain(id);
      reload({ proto_stage: id });
    },
  }));
}

/**
 * The prototype-only ⌘K items for a tab's state (pure apart from `run`).
 * @param {{role: string, stages: {id: string, label: string}[],
 *   stage: string|null}} state
 * @param {(params: object) => void} [reload]
 */
export function prototypeCommands(state, reload = reloadWith) {
  return [
    ...roleCommands(state.role, reload),
    ...stageCommands(state.stages ?? [], state.stage, reload),
  ];
}
