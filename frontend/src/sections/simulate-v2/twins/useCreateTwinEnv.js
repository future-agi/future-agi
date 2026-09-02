import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { useSimStore } from "../store";
import {
  twinById, seedScenariosForClone, resolveSeedPromptToJson,
} from "../_mock/twins";

/**
 * Creates a twin-backed environment and navigates to its workspace.
 *
 * Shared between the "Create a twin-backed environment" multi-service
 * flow (NewTwinEnvironment) and the one-click flow on the Twins
 * browse page (TwinsBrowse). Both paths must produce the same shape
 * of env — same `twinBacking`, same starter scenarios, same eval
 * preset — so users don't get different behaviours depending on
 * which door they came through.
 *
 * The consumer typically opens `TwinProvisioningModal` first, then
 * calls this on the modal's `onDone` so the animation doesn't race
 * with navigation.
 */
export function useCreateTwinEnv() {
  const navigate = useNavigate();
  const { dispatch } = useSimStore();

  return (services, {
    name = "",
    seedPrompt = "",
    ttlMinutes = null,
    agent = null,
  } = {}) => {
    if (!services?.length) return null;

    const envId = `env-twin-${Date.now().toString(36)}`;
    const resolvedSeed = resolveSeedPromptToJson(services, seedPrompt.trim());
    const derivedName = name.trim() || defaultEnvName(services);
    /* Full seed pack: starter-per-service + cross-service combos,
       each pre-stamped with a persona so the Personas tab is populated
       from the moment the env lands. */
    const starterScenarios = seedScenariosForClone(services);
    const provisionedAt = new Date().toISOString();
    const twinBacking = {
      services,
      seedPrompt: seedPrompt.trim(),
      seed: resolvedSeed,
      endpoints: Object.fromEntries(services.map((sId) => [
        sId,
        `https://${sId}.sandbox.futureagi.com/e/${envId.slice(-6)}`,
      ])),
      activity: Object.fromEntries(services.map((sId) => [sId, { requests: 0, failures: 0 }])),
      provisionedAt,
      /*
        `ttlMinutes: null` = permanent env (default). A number = short-
        lived env; the workspace header shows a countdown and the
        surface treats the env as ephemeral. `expiresAt` is pre-computed
        so the UI doesn't have to keep adding to a Date on every render.
      */
      ttlMinutes: ttlMinutes || null,
      expiresAt: ttlMinutes
        ? new Date(Date.parse(provisionedAt) + ttlMinutes * 60_000).toISOString()
        : null,
      /*
        status: "provisioning" while the inline TwinProvisioningView
        is animating; the view flips it to "ready" once every phase
        lands, which unlocks the real review layout.
      */
      status: "provisioning",
    };

    const env = {
      id: envId,
      agentType: "twin_backed",
      name: derivedName,
      surface: primarySurface(services),
      domain: "twin",
      tagline: `${services.length} twinned service${services.length === 1 ? "" : "s"}`,
      description: seedPrompt.trim() || defaultDescription(services),
      difficulty: "Advanced",
      popularity: 1,
      builtFrom: { kind: "twin", services },
      seed: { tables: [] },
      tools: [],
      rules: [],
      evalPreset: ["task_success", "twin_end_state_match", "twin_no_extra_writes"],
      buildStatus: "ready",
      buildProgress: { done: 3, total: 3 },
      starterScenarios,
    };

    dispatch({ type: "adoptEnvironment", env, now: new Date().toISOString() });
    dispatch({
      type: "patchEnvState",
      envId,
      patch: {
        twinBacking,
        scenarios: starterScenarios,
        scenarioSource: "twin_starter",
        /*
          Agent connection captured in the composer — persisted onto
          envState so the workspace's Agents tab shows the connected
          agent from the moment the user lands.
        */
        agent: agent ? {
          typeId: "twin_backed",
          via: "endpoint",
          connectedAt: new Date().toISOString(),
          values: {
            sdkEndpoint: agent.sdkEndpoint,
            ...(agent.authToken ? { authToken: agent.authToken } : {}),
          },
        } : undefined,
      },
    });
    /*
      Land directly at the review layout (chat left + workspace tabs
      right). Agent is already connected from the composer, so no
      intermediate Connect page — one form to set everything up.
    */
    navigate(paths.dashboard.simulate.environmentTwinReview(envId));
    return envId;
  };
}

/* ── helpers (kept here so both consumers get identical defaults) ── */

export function defaultEnvName(services) {
  if (services.length === 1) {
    const t = twinById(services[0]);
    return t ? `${t.name} sandbox` : "Twin sandbox";
  }
  return `${services.length}-service sandbox`;
}

export function defaultDescription(services) {
  const names = services.map((s) => twinById(s)?.name).filter(Boolean).join(", ");
  return `Clone-backed environment. Agent operates across ${names || "the selected services"}.`;
}

export function primarySurface(services) {
  if (services.some((s) => ["slack", "gmail", "discord"].includes(s))) return "chat";
  if (services.some((s) => ["github", "linear"].includes(s))) return "coding";
  return "chat";
}
