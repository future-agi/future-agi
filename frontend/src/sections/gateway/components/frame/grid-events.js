// AG Grid reports a row click even when the click landed on a button inside
// the row (React's stopPropagation does not reach its native listener), so a
// row's own controls would also open the row's drawer.
export function fromControl(event) {
  const target = event?.event?.target;
  return Boolean(
    target?.closest?.('button, a, [role="menu"], [role="menuitem"], input'),
  );
}

// Enter or Space on a focused cell opens its row, as a click would; on a
// control inside the cell (a button, a link) the key is the control's.
export function opensRow(event) {
  const key = event?.event?.key;
  return (key === "Enter" || key === " ") && !fromControl(event);
}
