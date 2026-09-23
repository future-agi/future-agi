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
 *     toolStatus: "success" | "failed" | "error" | "timeout",
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
 * Derive tool-call rows from task steps. In production the runtime
 * would emit these directly; here we approximate from the steps
 * array + status so every panel has data to render.
 */
export function deriveToolCalls(tasks) {
  const out = [];
  (tasks || []).forEach((task) => {
    const steps = task.steps || [];
    /* Fallback: infer a synthetic tool call per task if steps don't
       carry tool metadata. The task's useCase + status usually maps
       to one primary tool. */
    const primaryTool = pickPrimaryTool(task.useCase);
    let synthesised = false;
    steps.forEach((step, i) => {
      if (step?.kind === "tool" || step?.tool || step?.toolName) {
        out.push({
          __derived: true,
          taskId: task.id,
          useCase: task.useCase,
          persona: task.persona,
          toolName: step.toolName || step.tool || step.label || primaryTool,
          toolStatus: toolStatusOf(step, task, i, steps.length),
          durationMs: step.durationMs || Math.round((task.durationMs || 0) / Math.max(1, steps.length)),
        });
        synthesised = true;
      }
    });
    if (!synthesised && primaryTool) {
      out.push({
        __derived: true,
        taskId: task.id,
        useCase: task.useCase,
        persona: task.persona,
        toolName: primaryTool,
        toolStatus: task.status === "passed" ? "success" : (task.status === "error" ? "error" : "failed"),
        durationMs: Math.round((task.durationMs || 0) * 0.4),
      });
    }
  });
  return out;
}

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

function toolStatusOf(step, task, i, total) {
  if (step?.error || step?.status === "error") return "error";
  if (step?.status === "failed") return "failed";
  if (step?.status === "success" || step?.status === "passed") return "success";
  /* Heuristic when the step doesn't carry an explicit status: if the
     task overall errored on the last tool step, that step is the
     one that broke; earlier steps succeeded. */
  if (task.status === "error" && i === total - 1) return "error";
  if (task.status !== "passed" && i === total - 1) return "failed";
  return "success";
}
