/*
  Two people editing one environment (roadmap "How the product fits
  together": "If another member saves first, the next save shows both
  changes. The user can keep the other version, resolve overlapping fields
  or save a separate copy. Nothing is overwritten silently.").

  The prototype has one person, so one environment carries a teammate's
  save made while you were editing: "Pickup dispatch environment" (and the
  seeded clinic line), once, until you resolve it. The teammate changed one tool's
  behaviour — the tool you edited too, when you did, so the overlap shows.
*/
import { toolSettingOf, tierOf } from "./toolEnv";
import {
  pendingEnvChanges,
  saveEnvChangesPatch,
  discardEnvChangesPatch,
} from "./versions";

const TEAMMATE = "Priya Shah";

/** The teammate's save waiting on this environment, or null. */
export const teammateSaveOf = (env, envState) => {
  if (!/pickup dispatch|clinic appointment line/i.test(env?.name || ""))
    return null;
  if (envState?.teammateSaveResolved) return null;
  const mine = pendingEnvChanges(envState);
  if (!mine.length) return null;
  const tools = env?.tools || [];
  if (!tools.length) return null;
  /* The tool you touched, when one of your edits names it. */
  const touched = tools.find((t) =>
    mine.some((c) => String(c.detail || "").includes(t.name)),
  );
  const tool = touched || tools[0];
  const base = envState?.draftBase || {};
  const savedTier = toolSettingOf(tool, { toolEnv: base.toolEnv }).tier;
  const tier = savedTier === "replay" ? "stub" : "replay";
  const at = new Date(Date.now() - 12 * 60 * 1000).toISOString();
  return {
    by: TEAMMATE,
    at,
    tool: tool.name,
    tier,
    detail: `${tool.name} set to ${tierOf(tier).word}`,
    overlap: touched
      ? mine.find((c) => String(c.detail || "").includes(tool.name))
      : null,
  };
};

/* Their one change, on the world as it was saved. */
const theirToolEnv = (save, baseToolEnv = {}) => ({
  ...baseToolEnv,
  [save.tool]: {
    ...(baseToolEnv[save.tool] || {}),
    tier: save.tier,
    safeTarget: null,
    confirmed: true,
  },
});
const theirChange = (save) => ({
  kind: "toolbehaviour",
  detail: `${save.detail} (${save.by})`,
  at: save.at,
});

/** Keep theirs: your unsaved edits go; their save becomes the next version. */
export const keepTheirsPatch = (env, envState, save) => {
  const restore = discardEnvChangesPatch(env, envState) || {};
  const baseState = { ...envState, ...restore };
  const toolEnv = theirToolEnv(save, baseState.toolEnv || {});
  return {
    ...restore,
    toolEnv,
    ...saveEnvChangesPatch(env, {
      ...baseState,
      toolEnv,
      unsavedEnvChanges: [theirChange(save)],
    }),
    teammateSaveResolved: true,
  };
};

/**
 * Save both: their save is the next version, yours the one after it. Where
 * you both changed the same tool, `pick` says whose stays ("mine" | "theirs").
 */
export const saveBothPatch = (env, envState, save, pick = "mine") => {
  const restore = discardEnvChangesPatch(env, envState) || {};
  const baseState = { ...envState, ...restore };
  const theirs = theirToolEnv(save, baseState.toolEnv || {});
  const first = saveEnvChangesPatch(env, {
    ...baseState,
    toolEnv: theirs,
    unsavedEnvChanges: [theirChange(save)],
  });
  /* Your tool edits on top of theirs — the overlapping one as picked. */
  const mineToolEnv = envState?.toolEnv || {};
  const baseToolEnv = baseState.toolEnv || {};
  const changedByMe = Object.keys(mineToolEnv).filter(
    (k) => JSON.stringify(mineToolEnv[k]) !== JSON.stringify(baseToolEnv[k]),
  );
  const merged = { ...theirs };
  changedByMe.forEach((k) => {
    if (k === save.tool && pick === "theirs") return;
    merged[k] = mineToolEnv[k];
  });
  const mine = pendingEnvChanges(envState).filter(
    (c) => !(pick === "theirs" && c === save.overlap),
  );
  const second = mine.length
    ? saveEnvChangesPatch(env, {
        ...envState,
        ...first,
        toolEnv: merged,
        unsavedEnvChanges: mine,
      })
    : first;
  return { toolEnv: merged, ...second, teammateSaveResolved: true };
};
