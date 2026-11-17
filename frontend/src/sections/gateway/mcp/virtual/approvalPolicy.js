export function approvalForm(policy) {
  return {
    require: policy?.require || "none",
    tools: (policy?.tools || []).join("\n"),
    validity: policy?.validity || "once",
    ttl: String(policy?.ttl_seconds ?? 300),
  };
}

export function approvalPatch(form) {
  if (!["none", "listed", "destructive"].includes(form.require)) {
    throw new Error("Choose when approval is required.");
  }
  if (form.require === "none") return { require: "none" };
  if (!["once", "ttl"].includes(form.validity)) {
    throw new Error("Choose once-only or reusable approval validity.");
  }
  if (
    !/^\d+$/.test(form.ttl) ||
    Number(form.ttl) < 1 ||
    Number(form.ttl) > 86400
  ) {
    throw new Error("Expiry must be a whole number from 1 to 86400 seconds.");
  }
  const tools = [
    ...new Set(
      form.tools
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ];
  if (
    form.require === "listed" &&
    (!tools.length || tools.some((s) => !s.includes("/")))
  ) {
    throw new Error("Add at least one server/tool pattern, one per line.");
  }
  return {
    require: form.require,
    tools,
    validity: form.validity,
    ttl_seconds: Number(form.ttl),
  };
}
