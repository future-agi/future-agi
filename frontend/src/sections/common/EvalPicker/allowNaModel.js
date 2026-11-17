// The N/A switch is implemented only by prototype mocks. The real eval
// update contract rejects allow_na; hiding the control must also stop its
// payload and any previously stored prototype value from reaching that API.
import { isPrototypeMode } from "src/utils/prototype";

export function stripLaunchAllowNa(config = {}) {
  const supported = { ...config };
  if (!isPrototypeMode()) delete supported.allow_na;
  return supported;
}

export const ALLOW_NA_HINT =
  "When the row gives the judge nothing to score, the judge answers N/A with a reason. N/A rows are left out of means and pass rates.";

/**
 * Whether an eval can offer the switch: one of the workspace's own judges
 * (an LLM or agent judge the template detail marks `supports_na`; a new
 * one being created is), never a code eval, a system template or a
 * decision model. Where the scorer would not honour it, it is not shown.
 */
export const allowNaApplies = ({
  evalType,
  isSystemEval,
  decisionModel,
  supportsNa = true,
}) =>
  isPrototypeMode() &&
  (evalType === "llm" || evalType === "agent") &&
  !isSystemEval &&
  !decisionModel &&
  Boolean(supportsNa);

/**
 * W1-3: the version an eval card is pinned to, from the editor's version
 * select: "latest" or "v4"; none for the default version. The real backend
 * stores "latest" as the newest saved version when the experiment is saved
 * (a later version does not move the pin; the reopened card reads "V4 ·
 * pinned"); only the prototype mock resolves it when a run starts. `versionPinLabel` is the card's chip ("V4 · pinned",
 * "Latest · V4").
 * @param {string|null} selected  the select's value: "", "latest" or a version id
 * @param {Array<{id: string, version_number: number}>} versions  newest first
 */
export function versionPinOf(selected, versions = []) {
  if (!selected) return {};
  if (selected === "latest")
    return {
      versionPin: "latest",
      versionPinLabel: `Latest · V${versions[0]?.version_number ?? ""}`,
    };
  const v = versions.find((x) => x.id === selected);
  if (!v) return {};
  return {
    versionPin: `v${v.version_number}`,
    versionPinLabel: `V${v.version_number} · pinned`,
  };
}
