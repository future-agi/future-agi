// Both transformed create submissions and raw edit form state use this wire shape.
export const webhookPayload = (data) => {
  if (data.webhook_url === undefined && data.webhook_condition === undefined) return {};
  const url = (data.webhook_url || "").trim();
  if (!url) return { webhook_url: "", webhook_condition: null };
  const condition = data.webhook_condition || {};
  let value = condition.value;
  if (condition.operator === "fail") value = null;
  else if (["gt", "lt"].includes(condition.operator)) {
    value = typeof value === "number" ? value
      : typeof value === "string" && value.trim() !== "" ? Number(value)
      : null;
  } else if (typeof value === "string") {
    // Equality accepts JSON numbers/booleans or literal text. Quoted JSON
    // strings let callers distinguish the string "0" from numeric zero.
    try { value = JSON.parse(value); } catch { /* Keep literal text. */ }
  }
  return {
    webhook_url: url,
    webhook_condition: { eval_config_id: condition.eval_config_id, operator: condition.operator, value },
  };
};

export const webhookErrors = (data) => {
  if (!data.webhook_url?.trim()) return {};
  const errors = {};
  try {
    const url = new URL(data.webhook_url);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.hash) throw new Error();
  } catch { errors.webhook_url = "Enter a public HTTP(S) webhook URL."; }
  const condition = data.webhook_condition;
  if (!condition?.eval_config_id || !data.evalsDetails?.includes(condition.eval_config_id)) {
    errors["webhook_condition.eval_config_id"] = "Choose an evaluation on this task.";
  }
  if (!["gt", "lt", "eq", "fail"].includes(condition?.operator)) {
    errors["webhook_condition.operator"] = "Choose a condition operator.";
  }
  const value = webhookPayload(data).webhook_condition?.value;
  if (["gt", "lt"].includes(condition?.operator) && (typeof value !== "number" || !Number.isFinite(value))) {
    errors["webhook_condition.value"] = "Enter a finite numeric threshold.";
  }
  if (condition?.operator === "eq" && (!["string", "boolean", "number"].includes(typeof value) || (typeof value === "number" && !Number.isFinite(value)))) {
    errors["webhook_condition.value"] = "Enter text, a boolean or a finite number.";
  }
  return errors;
};
