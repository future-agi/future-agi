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
  if (files.length > 5000 || totalBytes > 200 * 1024 * 1024)
    throw new Error("Source uploads support at most 5000 files and 200 MiB.");
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
