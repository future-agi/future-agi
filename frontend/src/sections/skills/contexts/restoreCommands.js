const quote = (value) => `'${String(value).replaceAll("'", "'\"'\"'")}'`;

// The commands an operator runs to restore the checked-in lock exported from the
// last reviewed commit. Native discovery is not qualified, so installation is an
// explicit preview and never delivery evidence.
export function restoreCommands({ name, version, lockPath }) {
  return [
    `python -m fi.context restore --allow-preview --lock ${quote(lockPath)} --project . --plan ${quote("restore-plan.json")}`,
    `python -m fi.context inspect --plan ${quote("restore-plan.json")} --diff`,
    `python -m fi.context apply --plan ${quote("restore-plan.json")} --quiescent`,
    "python -m fi.context verify --project .",
    `# Expect ${name} v${version}. Local files outside the managed paths are preserved.`,
  ].join("\n");
}
