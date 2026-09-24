import { isMeasured } from "../_mock/failures";

/**
 * Tool-call analytics data source.
 *
 * Per Monika's feedback ("tool level will be important for us … we
 * need tool level analytics also, how many tools were called and
 * how many failed and how many passed"), tool call outcomes route
 * to a *different* team (infra) than prompt failures. Making them
 * a first-class data source lets the analytics tab surface them
 * as their own section and lets custom widgets slice by tool.
 *
 * Row shape:
 *   {
 *     __derived: true,
 *     toolName: "check_order_status",
 *     toolStatus: "success" | "failed" | "error" | "timeout" | "not_called",
 *     durationMs: number,
 *     taskId: string,
 *     useCase: string,
 *     persona: object,
 *   }
 */

const KNOWN_TOOLS = [
  "check_return_eligibility",
  "issue_refund",
  "lookup_order",
  "shipment_status",
  "escalate_to_human",
  "generate_return_label",
  "cancel_order",
  "update_shipping_address",
];

/**
 * Tool-call rows for a set of tasks.
 *
 * Read from each task's own call log when it has one: every call the agent
 * made (status from the call itself — a tool that errored is the tool's
 * failure, an infra matter), plus every tool the scenario required that the
 * agent never called ("not_called" — the agent skipped it, a prompt matter).
 * A task's verdict is never turned into a tool failure.
 *
 * Unmeasured tasks (environment, connection, simulator or grader broke) are
 * left out: whatever tool traffic they have says nothing about the tools.
 *
 * Tasks without a call log fall back to one inferred call from the steps.
 */
export function deriveToolCalls(tasks) {
  const out = [];
  (tasks || []).forEach((task) => {
    if (!isMeasured(task)) return;
    const base = { __derived: true, taskId: task.id, useCase: task.useCase, persona: task.persona };
    const log = task.callLog;
    if (log && Array.isArray(log.calls)) {
      log.calls.forEach((call) => {
        out.push({
          ...base,
          toolName: call.name,
          toolStatus: call.status === "ok" || call.status === "success" ? "success"
            : call.status === "timeout" ? "timeout" : "failed",
          durationMs: Number(call.ms) || null,
        });
      });
      (log.missing || []).forEach((name) => {
        out.push({ ...base, toolName: name, toolStatus: "not_called", durationMs: null });
      });
      return;
    }
    const steps = task.steps || [];
    const toolSteps = steps.filter((step) => step?.kind === "tool" || step?.tool || step?.toolName);
    toolSteps.forEach((step) => {
      out.push({
        ...base,
        toolName: step.toolName || step.tool || step.label || pickPrimaryTool(task.useCase),
        toolStatus: toolStatusOf(step),
        durationMs: step.durationMs || null,
      });
    });
  });
  return out;
}

/** Whether a tool-call row is the tool breaking (as opposed to the agent skipping it). */
export const isToolFailure = (row) => row.toolStatus === "failed" || row.toolStatus === "error" || row.toolStatus === "timeout";

function pickPrimaryTool(useCase) {
  if (!useCase) return KNOWN_TOOLS[0];
  const s = useCase.toLowerCase();
  if (/refund/.test(s))    return "issue_refund";
  if (/return/.test(s))    return "check_return_eligibility";
  if (/ship/.test(s))      return "shipment_status";
  if (/label/.test(s))     return "generate_return_label";
  if (/escalat/.test(s))   return "escalate_to_human";
  if (/cancel/.test(s))    return "cancel_order";
  if (/address/.test(s))   return "update_shipping_address";
  if (/order|lookup/.test(s)) return "lookup_order";
  /* Otherwise pick deterministically from the use case string so
     the same use case always maps to the same tool. */
  const h = Array.from(s).reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 0);
  return KNOWN_TOOLS[h % KNOWN_TOOLS.length];
}

function toolStatusOf(step) {
  if (step?.error || step?.status === "error") return "error";
  if (step?.status === "failed") return "failed";
  if (step?.status === "timeout") return "timeout";
  return "success";
}
