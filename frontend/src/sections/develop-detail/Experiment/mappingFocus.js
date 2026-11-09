// IT-R4 G1 P1: `Map customer_email first [Go to Data]` (Try on one row)
// asks the Data step to focus that variable's column picker once it mounts.
const pending = new Set();

/** Ask the Data step to focus `variable`'s picker. */
export function requestMappingFocus(variable) {
  if (variable) pending.add(variable);
}

/** True once for a requested variable (then forgotten). */
export function takeMappingFocus(variable) {
  if (!pending.has(variable)) return false;
  pending.delete(variable);
  return true;
}
