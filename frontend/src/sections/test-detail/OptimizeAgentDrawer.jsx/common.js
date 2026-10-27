export const getStatusColor = (status, theme) => {
  switch (status) {
    case "completed":
      return {
        bg: "#00A2511A",
        icon: "#00A251",
      };
    case "running":
      return {
        bg: "#348AEF1A",
        icon: "#348AEF",
      };
    case "failed":
      return {
        bg: theme?.palette?.red?.["o10"] ?? "#D92D201A",
        icon: theme?.palette?.red?.["700"] ?? "#D92D20",
      };
    default:
      return {
        bg: "#1A1A1A1A",
        icon: "text.secondary",
      };
  }
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
