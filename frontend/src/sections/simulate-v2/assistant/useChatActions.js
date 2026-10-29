import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { useOptionalSimStore, emptyEnvState } from "../store";
import { getEnvironment } from "../_mock/environments";
import { getEval } from "../_mock/evals";
import { FAULT_PRESETS, tierOf, toolEnvPatch, allToolsFaultPatch, liveFieldsOf } from "../_mock/toolEnv";
import { withPinnedWorld } from "../_mock/toolFit";
import { credentialSetPatch, envVarRefPatch } from "../_mock/envConfig";
import { credentialRefPatch } from "../_mock/secretRefs";
import { openGapsInbox } from "../_mock/gapsInboxBus";
import { useAppliedEvals } from "../workspace/evals/useAppliedEvals";
import {
  parseChatIntent, toolsAnswer, failureAnswer, missingAnswer, traceAnswer,
  tierPlan, faultPlan, secretTarget, envVarsAnswer, finishedRuns, failingOf,
} from "../_mock/chatActions";

/*
  The builder chat, acting — the part that needs the store and the router.

  Only inside an environment's workspace: the same console also narrates a
  build, a template review and a twin provisioning, where there is no
  environment to change yet. A message that isn't one of these intents is
  left to the caller's own reply, so everything the chat did before still
  happens.

  Every change goes through the patch helpers the panels use and lands in
  the same envState, so a tier set here is the tier the World tab shows.
*/

const NOT_A_WORKSPACE = new Set(["new", "browse", "templates", "use"]);
const NOT_A_PANEL = new Set(["twin-connect", "twin-review", "compare", "runs"]);
const REPLY_MS = 650;

const envIdFrom = (pathname) => {
  const m = String(pathname || "").match(/\/simulate\/environments\/([^/]+)(?:\/([^/]+))?\/?$/);
  if (!m || NOT_A_WORKSPACE.has(m[1]) || NOT_A_PANEL.has(m[2])) return null;
  return decodeURIComponent(m[1]);
};

/* Take back the pending-change entry an edit added — and only that one. */
const withoutChange = (list, detail) => {
  const all = list || [];
  const i = all.map((c) => c.detail).lastIndexOf(detail);
  return i < 0 ? all : [...all.slice(0, i), ...all.slice(i + 1)];
};

let seq = 0;
const nextId = (p) => { seq += 1; return `${p}-${Date.now()}-${seq}`; };

export default function useChatActions() {
  const store = useOptionalSimStore();
  const location = useLocation();
  const navigate = useNavigate();
  const envId = envIdFrom(location.pathname);

  const state = store?.state;
  const env = envId ? getEnvironment(envId) || state?.myEnvironments?.find((e) => e.id === envId) : null;
  const adopted = !!env && !!state?.myEnvironments?.some((e) => e.id === envId);
  const envState = (envId && state?.byEnv?.[envId]) || emptyEnvState();
  const dispatch = store?.dispatch;
  /* Edits made here are marked as the chat's in Review changes. */
  const patch = useCallback((p) => { if (dispatch && envId) dispatch({ type: "patchEnvState", envId, patch: { ...p, changeSource: "chat" } }); }, [dispatch, envId]);
  const locked = !!envState.seededFromTemplate && !env?.templateId;
  const active = !!store && adopted;

  /* Callbacks run after a reply delay or a click — always against the
     newest state, never the render that scheduled them. */
  const latest = useRef({ env, envState });
  latest.current = { env, envState };
  const evals = useAppliedEvals(envState.evals ? envState : { ...envState, evals: [] }, patch);
  const addEvalRef = useRef(evals.add);
  addEvalRef.current = evals.add;

  const [turns, setTurns] = useState([]);
  const [running, setRunning] = useState(false);
  /* A different environment is a different conversation. */
  useEffect(() => { setTurns([]); setRunning(false); }, [envId]);

  const push = (turn) => setTurns((prev) => [...prev, turn]);
  const update = useCallback((id, change) => setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, card: { ...t.card, ...change } } : t))), []);

  /* ── the changes ───────────────────────────────────────────────────── */

  const applyTier = useCallback((tool, tier, { safeTarget, toolRecord } = {}) => {
    const { envState: es } = latest.current;
    const before = es.toolEnv?.[tool];
    const detail = `${tool} set to ${tierOf(tier).short}`;
    /* Confirming Live in the chat is the permission, given for this tool; an
       MCP tool's server is its target. */
    const fields = tier === "live"
      ? { ...liveFieldsOf(toolRecord), ...(safeTarget && { safeTarget }) }
      : { tier, safeTarget: null };
    patch(toolEnvPatch(es, tool, fields, detail));
    return { tool, before, detail };
  }, [patch]);

  const confirmTier = useCallback((tool, tier) => {
    /* Already at this tier: only marked as the team's choice — no world edit. */
    const { envState: es } = latest.current;
    const all = es.toolEnv || {};
    const before = all[tool];
    patch({ toolEnv: { ...all, [tool]: { ...(before || {}), tier, confirmed: true } } });
    return { tool, before, detail: null };
  }, [patch]);

  const applyFaults = useCallback((tool, preset) => {
    const { env: e, envState: es } = latest.current;
    if (!tool) {
      const beforeAll = es.toolEnv;
      const p = allToolsFaultPatch(withPinnedWorld(e, es), es, preset);
      patch(p);
      return { all: true, beforeAll, detail: `${FAULT_PRESETS[preset]?.label || "Clean"} faults on every tool` };
    }
    const before = es.toolEnv?.[tool];
    const detail = `${tool} faults set to ${FAULT_PRESETS[preset]?.label || "Clean"}`;
    patch(toolEnvPatch(es, tool, { faults: FAULT_PRESETS[preset]?.faults || null }, detail));
    return { tool, before, detail };
  }, [patch]);

  const undoToolChange = useCallback((undo) => {
    const { envState: es } = latest.current;
    const all = { ...(es.toolEnv || {}) };
    if (undo.all) {
      patch({ toolEnv: undo.beforeAll || {}, unsavedEnvChanges: withoutChange(es.unsavedEnvChanges, undo.detail) });
      return;
    }
    if (undo.before === undefined) delete all[undo.tool];
    else all[undo.tool] = undo.before;
    patch({ toolEnv: all, ...(undo.detail && { unsavedEnvChanges: withoutChange(es.unsavedEnvChanges, undo.detail) }) });
  }, [patch]);

  const addEval = useCallback((id) => {
    const item = getEval(id);
    if (!item) return null;
    addEvalRef.current([item]);
    return { evalId: id, detail: `Added ${item.name}` };
  }, []);

  const undoEval = useCallback((undo) => {
    const { envState: es } = latest.current;
    patch({
      evals: (es.evals || []).filter((e) => (typeof e === "string" ? e : e.id) !== undo.evalId),
      ...(es.evalChanges && { evalChanges: withoutChange(es.evalChanges, undo.detail) }),
    });
  }, [patch]);

  const setSecretRef = useCallback((target, ref) => {
    const { envState: es } = latest.current;
    /* A moved reference is the environment's next (credentials-only) version. */
    if (target.type === "env") patch(credentialSetPatch(es, envVarRefPatch(es, target.id, ref)));
    else patch(credentialSetPatch(es, credentialRefPatch(es, target.id, ref, target.provider)));
  }, [patch]);

  /* ── links ─────────────────────────────────────────────────────────── */

  const go = useMemo(() => ({
    tab: (step, search = "") => navigate(`${paths.dashboard.simulate.environmentStep(envId, step)}${search}`),
    run: (runId, scenarioId) => navigate(`${paths.dashboard.simulate.simulationRun(envId, runId)}${scenarioId ? `?task=${encodeURIComponent(scenarioId)}` : ""}`),
    scenario: (scenarioId) => navigate(`${paths.dashboard.simulate.environmentStep(envId, "scenarios")}?scenario=${encodeURIComponent(scenarioId)}`),
    gaps: (gapId) => openGapsInbox(gapId || null),
  }), [navigate, envId]);

  /* ── answering ─────────────────────────────────────────────────────── */

  const cardFor = (intent) => {
    const { env: e, envState: es } = latest.current;
    const lockedNote = { type: "note", tone: "muted", text: "This is a catalogue template, so it's read-only. Fork it from the header, then ask again." };
    switch (intent.kind) {
      case "listTools":
        return { type: "tools", ...toolsAnswer(e, es) };
      case "whyFailed":
        return { type: "failure", ...failureAnswer(e, es, intent.query) };
      case "showTrace":
        return { type: "trace", ...traceAnswer(e, es, intent.query) };
      case "missing":
        return { type: "missing", ...missingAnswer(e, es) };
      case "envVars":
        return { type: "envVars", ...envVarsAnswer(es, intent.usedBy) };
      case "setTier": {
        if (locked) return lockedNote;
        const plan = tierPlan(e, es, intent.tool, intent.tier);
        if (!plan) return { type: "note", text: `There's no ${intent.tool} in this world.` };
        if (plan.same) {
          confirmTier(plan.tool, plan.tier);
          return { type: "change", title: `${plan.tool} stays ${plan.to}`, line: "It was already set to that — marked as your choice. No world edit, so no new version.", undo: null, review: "world", applied: true };
        }
        /* Live is the real tool: never set without a confirmation, and never
           where the rule says no — a tool that writes, or one the Gateway blocks. */
        if (plan.tier === "live" && !plan.live?.allowed && !plan.live?.needsAllow) {
          return { type: "note", text: plan.live?.reason || `${plan.tool} can't run Live.` };
        }
        if (plan.tier === "live") return { type: "liveConfirm", plan, status: "asking" };
        const undo = applyTier(plan.tool, plan.tier);
        return { type: "change", title: `${plan.tool}: ${plan.from} → ${plan.to}`, line: `${tierOf(plan.tier).note} Recorded as a world edit — saved as the next environment version.`, undo, undoKind: "tool", review: "world", applied: true };
      }
      case "setFaults": {
        if (locked) return lockedNote;
        if (intent.all) {
          const undo = applyFaults(null, intent.preset);
          return { type: "change", title: `${FAULT_PRESETS[intent.preset]?.label || "Clean"} faults on every tool`, line: "One world edit for all of them — saved as the next environment version.", undo, undoKind: "tool", review: "world", applied: true };
        }
        const plan = faultPlan(e, es, intent.tool, intent.preset);
        if (!plan) return { type: "note", text: `There's no ${intent.tool} in this world.` };
        if (plan.same) return { type: "note", text: `${plan.tool} already runs with ${plan.label.toLowerCase()} faults (${plan.line}). Nothing changed.` };
        const undo = applyFaults(plan.tool, plan.preset);
        return { type: "change", title: `${plan.tool}: ${plan.label} faults`, line: `${plan.line}. Injected on every call to it; recorded as a world edit.`, undo, undoKind: "tool", review: "world", applied: true };
      }
      case "addEval": {
        if (locked) return lockedNote;
        const item = getEval(intent.evalId);
        const has = (es.evals || []).some((x) => (typeof x === "string" ? x : x.id) === intent.evalId);
        if (has) return { type: "note", text: `${item.name} is already scoring every scenario here.` };
        const undo = addEval(intent.evalId);
        return { type: "change", title: `Added ${item.name}`, line: `${item.blurb || item.description || ""} Scores every scenario from the next run — saved as the next environment version.`.trim(), undo, undoKind: "eval", review: "evals", applied: true };
      }
      case "setSecret": {
        if (locked) return lockedNote;
        const target = secretTarget(e, es, intent.provider);
        if (!target) return { type: "note", text: "This environment has no secrets to point anywhere yet. Add one on Settings → Environment variables." };
        return { type: "secret", target, status: "editing" };
      }
      default:
        return null;
    }
  };

  /**
   * Handle a message if it is one of the chat's actions. Returns false to
   * let the caller reply as before. `anchor` is how many of the caller's
   * turns precede this one, so the two conversations interleave in order.
   */
  const handle = (text, anchor = 0) => {
    if (!active) return false;
    const intent = parseChatIntent(text, latest.current);
    if (!intent) return false;
    const replyId = nextId("ca");
    push({ id: nextId("cu"), role: "user", text, anchor });
    setRunning(true);
    setTimeout(() => {
      setRunning(false);
      let card;
      try {
        card = cardFor(intent);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[chat] action failed", err);
        card = { type: "note", text: "I couldn't work that out from this environment's data." };
      }
      push({ id: replyId, role: "assistant", anchor, card: card || { type: "note", text: "Nothing to do there." } });
    }, REPLY_MS);
    return true;
  };

  /* One grounded suggestion: the latest run's worst scenario, by its title —
     never its slug — and only one that is a scenario of this environment. */
  const suggestion = useMemo(() => {
    if (!active) return null;
    try {
      const run = finishedRuns(envState)[0];
      const rows = new Map((envState.scenarios || []).filter((r) => !r.retired).map((r) => [r.id, r]));
      const worst = run ? failingOf(env, envState, run).find((t) => rows.has(t.id)) : null;
      const row = worst ? rows.get(worst.id) : null;
      const label = row && (row.title || row.useCase || worst.title);
      return label ? `Why did "${label}" fail?` : null;
    } catch {
      return null;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, env, envState.runs, envState.scenarios]);

  const actions = {
    go,
    update,
    confirmLive: (turnId, plan, safeTarget) => {
      const undo = applyTier(plan.tool, "live", { safeTarget, toolRecord: plan.toolRecord });
      update(turnId, { status: "applied", undo, safeTarget });
    },
    declineLive: (turnId) => update(turnId, { status: "declined" }),
    undo: (turnId, card) => {
      if (card.undoKind === "eval") undoEval(card.undo);
      else undoToolChange(card.undo);
      update(turnId, { undone: true });
    },
    setSecret: (turnId, target, ref) => {
      setSecretRef(target, ref);
      update(turnId, { status: "saved", ref, checkAt: new Date().toISOString() });
    },
    cancelSecret: (turnId) => update(turnId, { status: "cancelled" }),
  };

  return { active, handle, turns, running, actions, suggestion, envState, envId };
}
