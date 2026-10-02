const ignoredDirectories = new Set([
  ".git",
  ".next",
  ".venv",
  "__pycache__",
  "build",
  "dist",
  "node_modules",
  "venv",
]);

const safeEnvironmentTemplates = new Set([
  ".env.example",
  ".env.sample",
  ".env.template",
]);

// The runner parses the multipart under Django's DATA_UPLOAD_MAX_NUMBER_FILES
// and DATA_UPLOAD_MAX_NUMBER_FIELDS limits, both 1000. Each file sends one file
// part plus one `paths` field, so an over-large folder is rejected server-side
// before anything is stored. Reject it here first, with a clear message, so the
// picker never fires a doomed upload.
export const MAX_UPLOAD_FILES = 1000;
export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;

export function prepareSourceFolder(selectedFiles) {
  const selected = Array.from(selectedFiles || []);
  const files = selected.filter((file) => {
    const parts = (file.webkitRelativePath || file.name).split("/");
    const leaf = parts.at(-1).toLowerCase();
    const environmentFile =
      (leaf === ".env" || leaf.startsWith(".env.")) &&
      !safeEnvironmentTemplates.has(leaf);
    return (
      !environmentFile &&
      leaf !== ".ds_store" &&
      !parts.some((part) => ignoredDirectories.has(part))
    );
  });
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  if (!files.length)
    throw new Error("The selected folder contains no uploadable source files.");
  if (files.length > MAX_UPLOAD_FILES)
    throw new Error(
      `This folder has ${files.length} files; uploads support at most ${MAX_UPLOAD_FILES}. Trim it to your agent's own source, or upload a smaller subfolder.`,
    );
  if (totalBytes > MAX_UPLOAD_BYTES)
    throw new Error("Source uploads support at most 200 MiB.");
  const originalPaths = files.map(
    (file) => file.webkitRelativePath || file.name,
  );
  const root = originalPaths[0]?.split("/")[0] || "uploaded-agent";
  const stripRoot = originalPaths.every((path) => path.startsWith(`${root}/`));
  return {
    files,
    paths: originalPaths.map((path) =>
      stripRoot ? path.slice(root.length + 1) : path,
    ),
    name: root,
    excludedCount: selected.length - files.length,
    totalBytes,
  };
}
