/*
  What was checked when an agent version was saved — the connection checks
  a version passes before an environment tests it: the source can be read,
  the platform takes the key, the assistant is there, the test credentials
  resolve.

  Only the checks that apply to this kind of agent are listed. One that
  doesn't apply isn't "skipped" — it isn't there. A version recorded by hand,
  or from before versioning, was never checked, and says so rather than
  reading as passed.

  A version may carry its own `checks` (recorded when it was read); otherwise
  they're worked out from how the agent connects.
*/

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

/**
 * `{ checks: [{ id, label, ok, detail }], at }`, or `{ unchecked: "why" }`.
 * `source` is agentSourceOf(agent).
 */
export const versionChecksOf = (agent, source, version) => {
  if (!version) return null;
  if (Array.isArray(version.checks)) return { checks: version.checks, at: version.checkedAt || version.createdAt || null };
  if (version.origin === "pre-versioning") return { unchecked: "Not checked" };
  if (version.origin === "manual") return { unchecked: "Not checked — recorded by hand" };

  const v = agent?.values || {};
  const kind = source?.kind;
  const checks = [];
  const add = (id, label, detail = null, ok = true) => checks.push({ id, label, ok, detail });

  if (kind === "platform") {
    add("key", `Key accepted by ${source.label}`, v.apiKeyRef || null);
    if (v.agentId) add("agent", `Agent found on ${source.label}`, v.agentId);
    else add("agent", `Agent found on ${source.label}`, "No agent ID — reconnect to read it again", false);
    if (v.phoneNumber) add("phone", "Phone number answers", v.phoneNumber);
  } else if (kind === "repo" || kind === "framework") {
    add("source", "Code read", version.commit?.sha || source.where || null);
    if (kind === "framework") add("adapter", "Adapter answered");
  } else if (kind === "upload") {
    add("source", "Bundle unpacked", source.where || null);
  } else if (kind === "endpoint") {
    add("endpoint", "Endpoint answered", v.endpoint || null);
  } else if (kind === "mcp") {
    add("server", "Server answered");
  } else if (kind === "traces") {
    add("traces", "Traces read", v.fromTraces?.traces ? `${v.fromTraces.traces} from ${v.fromTraces.project}` : v.fromTraces?.project || null);
  } else if (kind === "builtin" || kind === "prompt") {
    add("prompt", "Prompt and model resolve");
  }

  const secrets = (v.envValues || []).filter((e) => e.secret);
  if (secrets.length) add("credentials", "Test credentials resolve", plural(secrets.length, "reference"));
  const files = v.credentialFiles || [];
  if (files.length) add("files", "Credential file mounts", files.map((f) => f.name).join(", "));

  return { checks, at: version.createdAt || version.connectedAt || null };
};

/** "3 checks passed" · "1 of 3 checks failed" · the unchecked reason. */
export const versionChecksLine = (result) => {
  if (!result) return "";
  if (result.unchecked) return result.unchecked;
  const failed = result.checks.filter((c) => !c.ok).length;
  return failed
    ? `${failed} of ${plural(result.checks.length, "check")} failed`
    : `${plural(result.checks.length, "check")} passed`;
};
