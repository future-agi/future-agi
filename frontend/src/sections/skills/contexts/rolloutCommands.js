// The displayed plan and the drawer's copy action must use identical commands.
export const planCommand = (version) => `python -m fi.context plan --name "$CONTEXT_NAME" --version ${Number.isInteger(version) ? version : '"$VERSION"'} --client codex --project . --out context-plan.json --allow-preview`;
