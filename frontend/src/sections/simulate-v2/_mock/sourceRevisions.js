/*
  Which source revisions a trial used. A run pins an environment version, and
  that version pinned each source at one revision — a Knowledge base file at
  its rev, an uploaded file at its v. Reading it back from the environment
  version means a later refresh never rewrites what an old trial used.
*/
import { versionNumber } from "./versions";

/* The label a source's revision goes by: rev 7 for the Knowledge base, v2 for an upload. */
const revisionLabel = (source, entry) =>
  source.kb ? `rev ${entry.rev ?? source.kb.revision}` : `v${entry.v}`;

/**
 * The sources an environment version held, each at the revision it pinned:
 * [{ id, name, label }]. Sources that joined later, and archived ones that
 * were never in it, are left out.
 */
export const sourcesAtEnvVersion = (envState, label) => {
  const at = versionNumber(label);
  return (envState?.sources || [])
    .filter((s) => s.inEnv)
    .flatMap((s) => {
      const entries = s.versions || [];
      /* A source's entries carry the version they joined; the oldest falls
         back to the version the source itself was saved in. */
      const stamped = entries.map((e, i) => ({
        ...e,
        n: versionNumber(
          e.inVersion || (i === 0 ? s.inVersion : null) || s.inVersion,
        ),
      }));
      const used = stamped.filter((e) => e.n <= at).pop();
      return used
        ? [{ id: s.id, name: s.name, label: revisionLabel(s, used) }]
        : [];
    });
};

/** "orders-q3-sample.csv rev 7 · Refund policy 2026.pdf rev 3", or null with none. */
export const sourceRevisionsLine = (envState, label) => {
  const used = sourcesAtEnvVersion(envState, label);
  return used.length
    ? used.map((u) => `${u.name} ${u.label}`).join(" · ")
    : null;
};
