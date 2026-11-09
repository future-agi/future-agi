// A single, registration-order-independent owner for Cmd/Ctrl+K.
// Only the focused dataset/results grid or its already-open palette takes
// the DSX shortcut; every other target belongs to the global Falcon launcher.
export function dsxOwnsCommandShortcut(event) {
  const target = event?.target;
  return Boolean(target?.closest?.(
    '#fa-data-grid-shell, [data-results-grid], [data-dsx-command-palette]',
  ));
}
