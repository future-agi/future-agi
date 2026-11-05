/*
  The exact source a version was read from: a commit for a repository
  ("main @ 1b9e201"), a digest for an upload ("sha256:3fa9…"). Recorded at
  connect; older records fall back to the same pointer every screen shows.
*/
import { sourceKindOf } from "../_mock/agentRegistry";
import { codeRefOf, isGitAgent } from "../_mock/codeRefs";

/* FNV-1a, stretched to `n` hex digits — a stand-in for the real hash. */
const hex = (s, n) => {
  let out = "";
  for (let i = 0; out.length < n; i += 1) {
    let h = 0x811c9dc5 ^ i;
    const str = String(s || "");
    for (let j = 0; j < str.length; j += 1)
      h = Math.imul(h ^ str.charCodeAt(j), 0x01000193);
    out += (h >>> 0).toString(16).padStart(8, "0");
  }
  return out.slice(0, n);
};

/** sha256:… of an upload's files, fixed by their names and sizes. */
export const digestOfFiles = (files = []) =>
  `sha256:${hex((files || []).map((f) => `${f.name}:${f.size}`).join("|") || "empty", 64)}`;

/** What to save on a first version, from the connect form's source: { commit } | { digest } | {}. */
export const identityAtConnect = (source) => {
  if (source?.kind === "upload") return { digest: digestOfFiles(source.files) };
  if (source?.kind === "repo" || source?.kind === "framework") {
    const ref = source.branch || source.ref || "main";
    return {
      commit: {
        sha: hex(`${source.value || source.name}:${ref}:connect`, 7),
        branch: ref,
      },
    };
  }
  return {};
};

/** { text, full } for a version of a code agent, else null. */
export const sourceIdentityOf = (agent, version) => {
  if (!agent || !version) return null;
  if (sourceKindOf(agent) === "upload") {
    const full =
      version.digest ||
      `sha256:${hex(`${agent.key || agent.name}:${version.label}:${version.bundle || ""}`, 64)}`;
    return { text: `${full.slice(0, 19)}…`, full };
  }
  if (isGitAgent(agent)) {
    const r = codeRefOf(agent, version);
    return r
      ? { text: `${r.branch} @ ${r.sha}`, full: `${r.branch} @ ${r.sha}` }
      : null;
  }
  return null;
};
