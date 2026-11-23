/*
  An epoch's change as files: how many it touches, the patch as text (each
  change under its file, the lines it adds and removes) and that patch as a
  download. The Changes tab, the Ship drawer and the ⋯ menu all read these,
  so the count and the patch never differ between them.
*/
import { changeFileFor } from "../_mock/optimize";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The distinct files the changes touch (a change with no file counts by its kind). */
export const changedFilesOf = (env, proposals = []) => [
  ...new Set(proposals.map((p) => changeFileFor(env, p)?.path || p.kind)),
];

/** "4 changes · 3 files". */
export const changeCountLine = (env, proposals = []) =>
  `${plural(proposals.length, "change")} · ${plural(changedFilesOf(env, proposals).length, "file")}`;

/** The changes as one patch: each under its file, then its lines. */
export const patchTextOf = (env, proposals = []) =>
  proposals
    .map((p) =>
      [
        `--- ${changeFileFor(env, p)?.path || p.kind}`,
        `# ${p.kind}: ${p.title}`,
        ...(p.diff || []).map(
          (d) =>
            `${d.type === "add" ? "+" : d.type === "remove" ? "-" : " "} ${d.text}`,
        ),
      ].join("\n"),
    )
    .join("\n\n");

/** Saves the patch as `<name>.patch`. */
export const downloadPatch = (name, text) => {
  if (typeof document === "undefined") return;
  const blob = new Blob([`${text}\n`], { type: "text/x-diff" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${String(name || "epoch")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")}.patch`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
