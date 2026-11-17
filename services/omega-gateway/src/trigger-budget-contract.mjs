// Signed-ingress limits. Keep equal to the pinned route manifest and daemon contract.
export const TRIGGER_BUDGETS = Object.freeze({
  sourceIpPerMinute: 60,
  triggerPerMinute: 20,
  workspacePerMinute: 120,
  verifiedConcurrencyPerTrigger: 4,
  verificationConcurrencyPerProcess: 32,
  runBudgetScope: "process",
});

export function assertTriggerBudgetContract(route) {
  if (route.credential !== "trigger-signature") {
    if (route.rate === "trigger-ingress" || route.budgets !== undefined)
      throw new Error("Trigger budgets require signature ingress.");
    return;
  }
  const budgets = route.budgets;
  if (
    route.rate !== "trigger-ingress" ||
    !budgets ||
    typeof budgets !== "object" ||
    Array.isArray(budgets) ||
    Object.keys(budgets).length !== Object.keys(TRIGGER_BUDGETS).length ||
    Object.entries(TRIGGER_BUDGETS).some(
      ([key, value]) => budgets[key] !== value,
    )
  )
    throw new Error(
      "Signed triggers require the enforced trigger-ingress budgets.",
    );
}
