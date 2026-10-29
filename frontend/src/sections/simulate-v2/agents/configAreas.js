/*
  The twelve configuration areas on the Constraints tab (PRD "Agents,
  defined"), mapped onto the change surfaces the improvement engine reads
  from `limits.allowed`:

    instructions, opening          → prompt
    tools                          → tools
    model, speech in/out, turn-taking, knowledge, variables → settings
    skills                         → skills
    memory                         → memory
    structure                      → structure
    analysis, account              → never

  Code and tool code stay their own switches (code agents only). An area's
  own choice is kept in `limits.areas` ({ [id]: bool }); without one it
  follows its default — default areas on while their surface is allowed,
  optional and off-by-default areas off (limits saved before areas existed
  read an allowed surface as every area on it), Analysis and Account never.
*/
import { CONFIG_AREAS } from "../_mock/rsiModel";
import { agentChannelsOf } from "../_mock/agentLifecycle";

export const AREA_SURFACE = {
  instructions: "prompt",
  opening: "prompt",
  tools: "tools",
  model: "settings",
  speechIn: "settings",
  speechOut: "settings",
  turnTaking: "settings",
  knowledge: "settings",
  variables: "settings",
  skills: "skills",
  memory: "memory",
  structure: "structure",
  analysis: null,
  account: null,
};

export const ALLOW_LABEL = { default: "On by default", optional: "Optional", off: "Off by default", never: "Never" };

/** The areas that apply to this agent — voice-only ones hidden for a chat-only agent. */
export const configAreasFor = (agent) => {
  const voice = agentChannelsOf(agent).includes("voice");
  return CONFIG_AREAS.filter((a) => voice || a.applies !== "voice");
};

/** Whether each area is on, from the stored choices and `allowed`. */
export const areaStatesOf = (limits, can = {}) => {
  const allowed = new Set(limits?.allowed || []);
  const own = limits?.areas || {};
  /* Saved before areas existed: an allowed surface meant every area on it. */
  const legacy = !limits?.areas;
  return Object.fromEntries(CONFIG_AREAS.map((a) => {
    const surface = AREA_SURFACE[a.id];
    const available = a.allow !== "never" && (!surface || can[surface]?.ok !== false);
    if (!available) return [a.id, false];
    if (typeof own[a.id] === "boolean") return [a.id, own[a.id] && allowed.has(surface)];
    if (a.allow === "off") return [a.id, false];
    if (a.allow === "optional") return [a.id, legacy && allowed.has(surface)];
    return [a.id, allowed.has(surface)];
  }));
};

/**
 * Turn one area on or off. Returns the limits patch: the area's own choice,
 * and `allowed` recomputed so a surface is allowed while any area that maps
 * onto it is on. Code and tool code are left as they are.
 */
export const areaPatch = (limits, id, on, can = {}) => {
  const states = { ...areaStatesOf(limits, can), [id]: on };
  /* Every area's choice is written down, so the next read doesn't guess. */
  const areas = Object.fromEntries(Object.entries(states).filter(([k]) => AREA_SURFACE[k]));
  const fromAreas = new Set(Object.entries(states).filter(([, v]) => v).map(([k]) => AREA_SURFACE[k]).filter(Boolean));
  const mapped = new Set(Object.values(AREA_SURFACE).filter(Boolean));
  const kept = (limits?.allowed || []).filter((s) => !mapped.has(s));
  return { areas, allowed: [...new Set([...kept, ...fromAreas])] };
};
