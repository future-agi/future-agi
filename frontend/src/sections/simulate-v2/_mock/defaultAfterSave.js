import { currentAgentVersion, isAgentPinned } from "./versions";
import { envVersionNameOf } from "./codeRefs";

/**
 * After an agent version is saved: which version each environment now
 * tests by default, and why when it isn't the new one. One row per
 * environment: { id, name, label, isNew, why }.
 */
export const defaultsAfterSave = (envs, byEnv, version) =>
  (envs || []).map((e) => {
    const es = byEnv?.[e.id];
    const nm = (l) => (l ? envVersionNameOf(es, l, e.id).main : "—");
    const cur = currentAgentVersion(es)?.label || null;
    const isNew = cur === version.label;
    const why = isNew
      ? "From the next run."
      : version.status === "draft"
        ? `${nm(version.label)} is a draft — it becomes the default once it's released.`
        : isAgentPinned(es)
          ? `Pinned to ${nm(cur)} here, so it stays. Make ${nm(version.label)} the default from this environment's agent line.`
          : `${nm(version.label)} hasn't reached this environment yet.`;
    return { id: e.id, name: e.name, label: nm(cur), isNew, why };
  });
