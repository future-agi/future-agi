import { hostRepoNameOf } from "../../_mock/integrations";
import { isSecretName } from "../../_mock/envValues";

/*
  Reading one repository for agents — done when it's picked (and again when
  the branch changes), never ahead of time for every repository an account
  can see. The prototype calls nothing: a read takes a moment, and two
  repositories stand in for what goes wrong on a first try — one whose
  clone fails, one too large to read in time. Retry reads it again.
*/
export const SCAN_MS = 1200;
/* How long a read may take before it's called a timeout. */
export const SCAN_TIMEOUT_MS = 2600;

const FAILS_FIRST = {
  "acme-labs/notebooks": "error",
  "acme-inc/infra": "timeout",
  "acme-inc/infra-terraform": "timeout",
};

/** "ok" | "error" | "timeout" — how reading `repoName` goes on `attempt` (0 = first). */
export const scanOutcomeOf = (repoName, attempt = 0) => (attempt === 0 && FAILS_FIRST[String(repoName || "").toLowerCase()]) || "ok";

/* ── environment values already given for a repository ─────────────────── */

const repoOfRecord = (a) => hostRepoNameOf(a?.definition?.repoUrl || a?.values?.repoUrl || "").toLowerCase();

/**
 * What other agents from the same repository were already given — so a
 * second agent from it asks only for what's missing. Keyed by repository,
 * read from the agents in Agents (where the values were saved):
 * { values: [{ name, secret, ref | value, from }], from: [agent names] }.
 */
export const envValuesFromRepo = (agents = [], repoName) => {
  const key = hostRepoNameOf(repoName || "").toLowerCase();
  if (!key) return { values: [], from: [] };
  const byName = new Map();
  const from = [];
  agents.filter((a) => repoOfRecord(a) === key).forEach((a) => {
    const list = a.values?.envValues || a.envValues || [];
    if (list.length) from.push(a.name);
    list.forEach((v) => { if (!byName.has(v.name)) byName.set(v.name, { ...v, from: a.name }); });
  });
  return { values: [...byName.values()], from };
};

/** Only the prefilled values the agent being connected reads (or every one, when nothing was detected). */
export const prefillFor = (remembered, names = []) => (names.length
  ? remembered.filter((v) => names.includes(v.name))
  : remembered);

/** Whether a name holds a secret — the row takes a reference, not a value. */
export const isSecretEnvName = isSecretName;
