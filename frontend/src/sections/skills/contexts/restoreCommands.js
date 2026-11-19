const quote = (value) => `'${String(value).replaceAll("'", "'\"'\"'")}'`;
const COMMIT = /^([0-9a-f]{40}|[0-9a-f]{64})$/;

// One command restores the lock of the last reviewed commit: it reads that lock
// from git, plans, applies and verifies in one quiescent step, refusing any
// local collision and preserving files outside the managed paths. Native
// discovery is not qualified, so installation is an explicit preview and never
// delivery evidence.
export function restoreCommands({ name, version, commit }) {
  const ref = COMMIT.test(commit ?? "") ? commit : quote("<full id of the last reviewed commit>");
  return [
    `python -m fi.context restore --allow-preview --lock-from-git ${ref} --project . --apply --quiescent`,
    `# Expect ${name} v${version}. Local files outside the managed paths are preserved.`,
  ].join("\n");
}
