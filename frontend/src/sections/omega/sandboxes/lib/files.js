/**
 * Pure helpers for the Files tab: paths, names, which files preview as
 * images, saving a downloaded file, and the words for a refused file action.
 * Nothing here talks to the server.
 */

/** Raster images the viewer shows inline (never SVG: it can carry script). */
const PREVIEW_TYPES = new Set(["png", "jpg", "jpeg", "gif", "webp"]);

/** Images larger than this are offered for download, not drawn inline. */
export const PREVIEW_MAX_BYTES = 20 * 1024 * 1024;

export const joinPath = (dir, name) => (dir ? `${dir}/${name}` : name);

export function baseName(path) {
  const parts = String(path || "").split("/");
  return parts[parts.length - 1] || "";
}

export function parentOf(path) {
  const parts = String(path || "").split("/");
  parts.pop();
  return parts.join("/");
}

export function isPreviewableImage(path) {
  const name = baseName(path);
  const dot = name.lastIndexOf(".");
  return dot > 0 && PREVIEW_TYPES.has(name.slice(dot + 1).toLowerCase());
}

// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/;

/**
 * Why a single file or folder name cannot be used, or null when it can. A
 * name is one path segment: no slash, not "." or "..", no control characters.
 */
export function nameProblem(raw) {
  const name = String(raw ?? "").trim();
  if (!name) return "Enter a name.";
  if (name.includes("/")) return "A name cannot contain /.";
  if (name === "." || name === "..") return "Choose another name.";
  if (CONTROL.test(name)) return "A name cannot contain control characters.";
  if (name.length > 255) return "Use at most 255 characters.";
  return null;
}

/**
 * A new file's name or path as typed, trimmed, with a run of slashes read as
 * one (`src//notes.txt` is `src/notes.txt`), as production read it.
 */
export function newFilePath(raw) {
  return String(raw ?? "")
    .trim()
    .replace(/\/{2,}/g, "/");
}

/**
 * Why a new file's name or path cannot be used, or null when it can. One name
 * is checked as nameProblem. A path such as `src/notes.txt` names a file in a
 * folder below the open one, so each part must be a name of its own.
 */
export function newFilePathProblem(raw) {
  const path = newFilePath(raw);
  if (!path.includes("/")) return nameProblem(path);
  // Before the parts are trimmed, so a tab at a part's edge is caught too.
  if (CONTROL.test(path)) return "A name cannot contain control characters.";
  if (path.startsWith("/"))
    return "Start the path in this folder, without a leading /.";
  for (const part of path.split("/")) {
    if (!part) return "Each part of the path needs a name.";
    if (part === "." || part === "..")
      return "A path cannot use . or .. as a part.";
    const problem = nameProblem(part);
    if (problem) return problem;
  }
  return null;
}

/** Offer a downloaded blob to the browser as a file named `name`. */
export function saveBlob(blob, name, doc = globalThis.document) {
  const url = URL.createObjectURL(blob);
  try {
    const link = doc.createElement("a");
    link.href = url;
    link.download = name || "download";
    link.rel = "noopener";
    link.style.display = "none";
    doc.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    // Give the browser a moment to start the download before the URL goes.
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }
}

const CODE_COPY = {
  too_large: "This file is larger than this sandbox accepts in one upload.",
  request_body_too_large:
    "This file is larger than Future AGI sends in one upload.",
  disk_quota_exceeded:
    "The sandbox's disk is full. Delete something and try again.",
};

/** A 413 from a hop that sends no code of ours (a proxy or ingress body limit). */
const TOO_LARGE_ANY_HOP =
  "This file is larger than this deployment accepts in one upload.";

/** The words for a refused file action: Future AGI copy for known limits, else the error's own. */
export function fileErrorMessage(error) {
  if (!error) return "";
  return (
    CODE_COPY[error.code] ||
    (error.status === 413 ? TOO_LARGE_ANY_HOP : null) ||
    error.message ||
    "That did not work. Try again."
  );
}
