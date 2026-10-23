/*
  A new version of an agent connected by folder or zip upload.

  An upload has no source to re-read, so it has no Sync: the person uploads
  the new folder or .zip, we read it the way we read the first one, show
  what changed since the last upload (the same shape a Sync shows), and it
  is saved as the next version — vN, with "Upload <date>" as where it came
  from. It's the person's own change, never an outside one.
*/
import { agentSourceOf, changesSince, syncedVersion } from "./agentRegistry";

/** "Oct 24" — the date an upload is named by. */
export const uploadDateOf = (at) => {
  const d = at ? new Date(at) : new Date();
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
};

/** "Upload Oct 24" — a version's source note. */
export const uploadNoteOf = (v) => `Upload ${uploadDateOf(v?.createdAt || v?.connectedAt)}`.trim();

/** "agent-v4.zip" or "support-agent/" for what was picked; "" for nothing. */
export const bundleNameOf = (files) => {
  const list = [...(files || [])];
  if (!list.length) return "";
  /* A folder comes in as its files, each with the folder in its path. */
  const folder = list[0].webkitRelativePath ? list[0].webkitRelativePath.split("/")[0] : null;
  if (folder) return `${folder}/`;
  return list.map((f) => f.name).join(" + ");
};

/**
 * What reading the new upload finds, against the newest version:
 *   { ok, upToDate: false, label, basedOn, changes, source, bundle }
 * Deterministic per agent and version, like a sync, so the demo repeats.
 */
export const uploadCheck = ({ agent, versions, env, bundle }) => {
  const source = agentSourceOf(agent);
  return { ok: true, upToDate: false, ...changesSince({ agent, versions, env, source }), bundle };
};

/** The version record an upload saves: vN, from "Upload <date>". */
export const uploadedVersion = ({ result, baseTools, now = new Date().toISOString() }) => {
  const base = syncedVersion({ result, baseTools, now });
  const what = result.changes.map((c) => c.title.split(" · ")[0].toLowerCase()).join(", ");
  return {
    ...base,
    origin: "upload",
    outsideChange: false,
    bundle: result.bundle,
    note: `${uploadNoteOf({ createdAt: now })} — ${result.bundle}${what ? `: ${what}` : ""}.`,
  };
};
