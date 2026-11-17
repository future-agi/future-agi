/** One finding: `{ id, step, severity, rule, file, line, message, fix }`, with a stable id. */
import { createHash } from "node:crypto";

export function finding(step, severity, rule, file, message, { line = null, fix = "" } = {}) {
  const id = `scan-${createHash("sha256").update([step, rule, file, line ?? "", message].join("\u0000")).digest("hex").slice(0, 8)}`;
  return { id, step, severity, rule, file, line, message, fix };
}

export const block = (step, rule, file, message, extra) => finding(step, "block", rule, file, message, extra);
export const flag = (step, rule, file, message, extra) => finding(step, "flag", rule, file, message, extra);

/** A step's status from its findings. */
export function statusOf(findings) {
  if (findings.some((item) => item.severity === "block")) return "fail";
  if (findings.some((item) => item.severity === "flag")) return "flag";
  return "pass";
}
