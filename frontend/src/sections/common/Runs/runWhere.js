// What a run ran on, in one place (options-truth L2 R2): the model the run
// used (S5) and the sandbox it ran in. Pure; Run details, the source line,
// the provenance card and the leaderboard read these.

const SEP = " · ";
const join = (parts) => parts.filter((x) => x != null && x !== "").join(SEP);

const isHelper = (m) =>
  ["helper", "judge", "stt", "tts", "caller"].includes(m?.role);

/**
 * The model a run used (S5): the engine's stamp first (`config.engine.model`,
 * written at create), else harness model > a changed `model` param > prompt
 * model > the agent's first node model > the first non-helper declared model.
 * @param {object} run  canonical Run with `config`
 * @returns {string|null}
 */
export function runModelOf(run) {
  const c = run?.config ?? {};
  if (c.engine?.model) return c.engine.model;
  if (c.harness?.model) return c.harness.model;
  const schema = c.params?.schema ?? [];
  const declared = schema.find((p) => p?.name === "model");
  const value = c.params?.values?.model;
  if (value && (!declared || String(value) !== String(declared.default)))
    return value;
  if (c.model?.id) return c.model.id;
  const node = (c.agent?.nodes ?? []).find((n) => n?.model);
  if (node) return node.model;
  const first = (c.models ?? []).find((m) => m?.id && !isHelper(m));
  return first?.id ?? run?.target?.model ?? null;
}

const NETWORK_WORDS = { off: "network Off", on: "network On" };

/**
 * `Future AGI sandbox · acme/support-agents@main · agent.py:graph · network
 * Off · 1 vCPU` from a config's sandbox (`config.sandbox` for code runs,
 * `config.harness.sandbox` when it is an object). Null when the run did not
 * run in a sandbox or the config does not describe one.
 * @param {object} c  run config
 * @param {{fromExecution?: boolean}} [opts]  `fromExecution: false` reads
 *   only a sandbox the run was created with (`config.sandbox` /
 *   `config.harness.sandbox` objects), not a seeded run's `execution` block.
 */
export function sandboxText(c = {}, { fromExecution = true } = {}) {
  const sb =
    c.sandbox && typeof c.sandbox === "object"
      ? c.sandbox
      : c.harness?.sandbox && typeof c.harness.sandbox === "object"
        ? c.harness.sandbox
        : // A seeded code run that ran in the sandbox describes it in
          // `execution` (`{mode: "sandbox", image, network}`) and its entry
          // in `code.entry`.
          fromExecution && c.execution?.mode === "sandbox"
          ? {
              image: c.execution.image ?? null,
              network: c.execution.network ?? null,
              allowlist: c.execution.allowlist,
              entry: c.code?.entry ?? null,
              resources: c.execution.resources,
              cpu: c.execution.cpu,
            }
          : null;
  if (!sb) return null;
  const where =
    sb.source === "image"
      ? sb.image
      : sb.source === "zip"
        ? sb.zip?.name ?? "project.zip"
        : sb.repo
          ? `${sb.repo}${sb.branch ? `@${sb.branch}` : ""}`
          : sb.image ?? null;
  const net =
    sb.network === "allowlist"
      ? Array.isArray(sb.allowlist)
        ? `network allowlist (${sb.allowlist.length})`
        : "network allowlist"
      : sb.network
        ? NETWORK_WORDS[sb.network] ?? `network ${sb.network}`
        : null;
  const cpu = Number(sb.resources?.cpu ?? sb.cpu) || null;
  return join([
    "Future AGI sandbox",
    where,
    sb.entry ?? null,
    net,
    cpu ? `${cpu} vCPU` : null,
  ]);
}
