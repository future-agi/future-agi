import { isNumericPass } from "src/sections/projects/LLMTracing/evalCellModel";

const isPassFail = (ot) => {
  const t = String(ot || "").toLowerCase();
  return t === "pass/fail" || t === "pass_fail" || t === "boolean";
};

const isChoice = (ot) => {
  const t = String(ot || "").toLowerCase();
  return t === "choices" || t === "choice";
};

// Flatten this span's own evals. The root carries scope:"trace" (every span),
// so filter spans[] to the entry's own id. Errored/choice rows have pass=null.
export function spanOwnEvalRows(entry) {
  const spanId = entry?.observation_span?.id;
  const rows = [];
  for (const task of entry?.eval_scores?.eval_tasks || []) {
    for (const ev of task.evals || []) {
      const pf = isPassFail(ev.output_type);
      const choice = isChoice(ev.output_type);
      for (const s of ev.spans || []) {
        if (spanId && s.span_id !== spanId) continue;
        let pass = null;
        let score = null;
        let result = null;
        let label = "—";
        if (s.error) {
          label = "Err";
        } else if (choice) {
          label = Array.isArray(s.value)
            ? s.value.join(", ")
            : String(s.value ?? "—");
        } else if (pf) {
          result = s.value === "pass" ? true : s.value === "fail" ? false : null;
          pass = result;
          score = result === true ? 100 : result === false ? 0 : null;
          label = result === true ? "Pass" : result === false ? "Fail" : "—";
        } else if (typeof s.value === "number") {
          score = s.value;
          pass = isNumericPass(s.value);
          label = `${s.value}%`;
        }
        rows.push({
          evalName: ev.eval_name,
          evalConfigId: ev.eval_config_id,
          outputType: ev.output_type,
          taskName: task.eval_task_name,
          spanId: s.span_id,
          pass,
          score,
          result,
          error: !!s.error,
          label,
        });
      }
    }
  }
  return rows;
}

export function collectSubtreeEvals(entry) {
  let pass = 0;
  let fail = 0;
  let total = 0;
  for (const r of spanOwnEvalRows(entry)) {
    // Only scorable rows count toward the X/Y badge; choice/errored rows
    // have no pass/fail.
    if (r.pass === true) {
      pass += 1;
      total += 1;
    } else if (r.pass === false) {
      fail += 1;
      total += 1;
    }
  }
  for (const child of entry?.children || []) {
    const c = collectSubtreeEvals(child);
    pass += c.pass;
    fail += c.fail;
    total += c.total;
  }
  return { pass, fail, total };
}
