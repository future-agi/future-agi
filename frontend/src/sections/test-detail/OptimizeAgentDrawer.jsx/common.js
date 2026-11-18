import { statusColors } from "src/utils/runStatus";

export const getStatusColor = (status, theme) => {
  const roleFor = {
    completed: "success",
    running: "info",
    failed: "danger",
  }[status];
  if (!theme) return { bg: undefined, icon: "text.secondary" };
  const c = statusColors(theme, roleFor || "neutral");
  return { bg: c.bg, icon: roleFor ? c.fg : "text.secondary" };
};

export const getDocsLinkBasedOnOptimizer = (optimiserName) => {
  const OPTIMIZER_DOCS_MAP = {
    gepa: "https://docs.futureagi.com/docs/optimization/optimizers/gepa",
    metaprompt:
      "https://docs.futureagi.com/docs/optimization/optimizers/meta-prompt",
    protegi: "https://docs.futureagi.com/docs/optimization/optimizers/protegi",
    random_search:
      "https://docs.futureagi.com/docs/optimization/optimizers/random-search",
    bayesian:
      "https://docs.futureagi.com/docs/optimization/optimizers/bayesian-search",
    promptwizard:
      "https://docs.futureagi.com/docs/optimization/optimizers/promptwizard",
  };

  return (
    OPTIMIZER_DOCS_MAP[optimiserName] ??
    "https://docs.futureagi.com/docs/optimization"
  );
};
