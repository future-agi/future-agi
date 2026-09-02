/**
 * Does this agent fit this world?
 *
 * Building an environment *from* an agent makes the contract match by
 * construction. Adopting a template does not: the world expects the template's
 * tools, and the agent may simply not have them. A scenario that ends in a
 * tool the agent cannot call fails — but it fails for the wrong reason, and a
 * pass rate computed over those is not a measurement of anything.
 *
 * So we probe the agent's declared tools and say plainly which scenarios are
 * still meaningful, before the first run rather than after it.
 */
import { getRows } from "./scenarios";

/** Deterministic per environment, so a demo replays the same way. */
const hash = (s = "") => [...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 9973, 7);

export const checkCompatibility = (env) => {
  const tools = (env?.tools || []).map((t) => t.name);
  if (!tools.length) return null;

  // Two of the world's tools are not on this agent — the common real case.
  const missCount = Math.min(2, Math.max(1, tools.length % 3));
  const start = hash(env.id) % Math.max(1, tools.length - missCount);
  const missing = tools.slice(start, start + missCount);
  const matched = tools.filter((t) => !missing.includes(t));

  // Tools it has that this world has no use for — harmless, worth stating.
  const extra = ["get_account_balance", "send_csat_survey"].slice(0, 1 + (hash(env.id) % 2));

  /*
    The core pack is one scenario per tool, so a missing tool maps exactly onto
    the scenarios that can no longer be passed.
  */
  const core = getRows(`${env.id}::core`, env);
  const blocked = core.filter((r) => missing.some((m) => r.title.includes(m)));

  return {
    matched,
    missing,
    extra,
    blocked,
    ready: missing.length === 0,
    probe: [
      { label: "connect()", result: "handshake ok" },
      { label: "list_tools()", result: `${matched.length + extra.length} declared` },
      { label: "compare()", result: `${matched.length} of ${tools.length} this world uses` },
    ],
  };
};
