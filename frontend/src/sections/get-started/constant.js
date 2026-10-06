const docLinks = {
  observeOverview: "https://docs.futureagi.com/docs/observe",
  datasetOverview: "https://docs.futureagi.com/docs/dataset",
  experimentOverview:
    "https://docs.futureagi.com/docs/dataset/features/experiments",
  evaluateOverview: "https://docs.futureagi.com/docs/evaluation",
};

const navigationLinks = {
  dataset: "/dashboard/develop",
  experiment: "/dashboard/develop",
  evaluate: "/dashboard/evaluations",
};

export const featureCardList = [
  {
    title: "Add dataset",
    description:
      "Quickly build and test your LLM agents with our comprehensive dataset management tool. Generate synthetic data, import existing datasets, and run sophisticated experiments—all in one platform.",
    image: "/assets/get_started/Add_dataset.png",
    imageDark: "/assets/get_started/Add_dataset_dark.png",
    buttonTitle: "Create dataset",
    learnMoreLink: docLinks.datasetOverview,
    actionLink: navigationLinks.dataset,
  },
  {
    title: "Experiment",
    description:
      "Experiment with Future AGI's comparison tool: evaluate multiple prompts or AI models side-by-side. Spot differences in reasoning, creativity, and accuracy in one interface. Make data-driven decisions to find the AI solution that truly meets your needs",
    image: "/assets/get_started/Experiment.png",
    imageDark: "/assets/get_started/Experiment_dark.png",
    buttonTitle: "Start experiment",
    learnMoreLink: docLinks.experimentOverview,
    actionLink: navigationLinks.experiment,
  },
  {
    title: "Evaluate",
    description:
      "Leverage Future AGI's research driven evals to automatically assess your GenAI applications without human involvement or ground truth data, ensuring your users always experience only the highest quality outputs.",
    image: "/assets/get_started/Evaluate.png",
    imageDark: "/assets/get_started/Evaluate_dark.png",
    buttonTitle: "Try evaluations",
    learnMoreLink: docLinks.evaluateOverview,
    actionLink: navigationLinks.evaluate,
  },
];

export const setupObserveVideoContent = {
  title: "What is observability and how to use?",
  description:
    "Monitor and analyze system performance in real-time. Identify bottlenecks, track anomalies, and ensure seamless operation.",
  srcThumbnail:
    "https://cdn.loom.com/sessions/thumbnails/c4440ec37395445f8f520a28fa79cdab-a22a142a928a289f-full-play.gif",
  buttonTitle: "Check Now",
  linkTitle: "View Doc",
  viewDocLink: docLinks.observeOverview,
  iframeVideo:
    "https://www.loom.com/embed/c4440ec37395445f8f520a28fa79cdab?sid=b381fd6d-c809-4825-9cba-1db5c5e640f7",
};

export const runFirstExperimentVideoContent = {
  title: "How to create experiment?",
  description:
    "Start testing models and explore different outcomes. Get insights quickly and refine your approach.",
  srcThumbnail:
    "https://cdn.loom.com/sessions/thumbnails/6670aff8264d4f1d86da26c77ba12f9c-88bfb4c5b0f3dd7d-full-play.gif",
  buttonTitle: "Check Now",
  linkTitle: "View Doc",
  viewDocLink: docLinks.experimentOverview,
  iframeVideo:
    "https://www.loom.com/embed/6670aff8264d4f1d86da26c77ba12f9c?sid=e84bd845-e3d4-4613-bbba-8b11187663f3",
};

export const createFirstEvalsLinks = {
  iframeVideoLinks:
    "https://www.loom.com/embed/e1b0bddb1eee47258200200e7402502d?sid=45f70a85-bf93-4b18-8677-a932d41ecec9",
  descriptions: [
    "Set up evaluations for testing different algorithms or models.",
    "Analyze results by comparing different evaluation outputs to identify the best approach.",
    "Save evaluations for easy access and future reference.",
    "Iterate and refine your models based on evaluation outcomes to improve performance.",
  ],
  links: [
    {
      link: "https://app.arcade.software/share/q2oR6KlsDatRQO6KouVG",
      title: "How to get started with RAG evaluation",
    },
    {
      link: "https://app.arcade.software/share/K7VPJyTtCce3d2By2eza",
      title: "How to get started with Image evaluation",
    },
  ],
};

export const createFirstDatasetLinks = {
  iframeVideoLinks:
    "https://www.loom.com/embed/060248e374de46c0b9f261d98a2caa37?sid=ae8bce80-ecd5-47d1-b381-a56a8f4f9e10",
  descriptions: [
    "Add data from multiple sources (CSV, Hugging face, synthetic data, SDK, etc)",
    "Clean and organize the data to ensure accuracy and consistency",
    "Prepare data for testing models, running analytics, or building reports",
    "Unlock powerful insights by experimenting with structured and unstructured data",
    "Easily manage and update your dataset as your project evolves",
  ],
  links: [
    {
      link: "https://app.arcade.software/share/Wcws7iE8g1h8kWXbWuHl",
      title: "How to add dynamic columns and run prompt",
    },
    {
      link: "https://app.arcade.software/share/o5INuCbOTaGE62llzjQe",
      title: "How to create synthetic dataset",
    },
    {
      link: "https://app.arcade.software/share/y4S9JB06Y0OQ48OQouJs",
      title: "How to run first experiment",
    },
    {
      link: "https://app.arcade.software/share/8ALx82DeRsQT9s5fcNeq",
      title: "How to annotate easily",
    },
    {
      link: "https://app.arcade.software/share/V1wYN0lI1Y07jlLjnWDF",
      title: "How to optimize prompt",
    },
  ],
};

export const getStartedPage = {
  contactUs: "https://futureagi.com/contact-us",
  getStartedVideo:
    "https://www.youtube.com/embed/6PLdnbMeC7k?si=IGY_DSHjkBrnMpZX",
};

export const menuesConstant = {
  statusColor: {
    complete: {
      iconColor: "green.700",
      titleColor: "text.primary",
      iconBackgroundColor: "green.o10",
      hoverIconBackgroundColor: "green.o10",
      borderColor: "green.500",
      hoverColor: "green.o10",
      backgroundColor: "#00A25105",
    },
    active: {
      iconColor: "blue.700",
      titleColor: "text.primary",
      iconBackgroundColor: "blue.o10",
      hoverIconBackgroundColor: "blue.o10",
      borderColor: "blue.500",
      hoverColor: "",
      backgroundColor: "",
    },
    incomplete: {
      iconColor: "text.primary",
      titleColor: "text.primary",
      iconBackgroundColor: `background.neutral`,
      hoverIconBackgroundColor: `background.neutral`,
      borderColor: `divider`,
      hoverColor: ``,
      backgroundColor: "",
    },
  },

  menusList: [
    {
      id: 0,
      icon: "/assets/icons/navbar/ic_lock.svg",
      title: "Add keys",
      status: "incomplete",
      label: "addKeys",
    },
    {
      id: 1,
      icon: "/assets/icons/navbar/hugeicons.svg",
      title: "Create first dataset",
      status: "incomplete",
      label: "createFirstDataset",
    },
    {
      id: 2,
      icon: "/assets/icons/action_buttons/ic_evaluation.svg",
      title: "Create your first evaluation",
      status: "incomplete",
      label: "createFirstEvaluation",
    },
    {
      id: 3,
      icon: "/assets/icons/navbar/ic_experiment.svg",
      title: "Run your first experiment",
      status: "incomplete",
      label: "RunFirstExperiment",
    },
    {
      id: 4,
      icon: "/assets/icons/navbar/ic_observe.svg",
      title: "Setup observability in application",
      status: "incomplete",
      label: "SetupObsabilityInApplication",
    },
    {
      id: 5,
      icon: "/assets/icons/navbar/ic_add_team.svg",
      title: "Invite team members",
      status: "incomplete",
      label: "inviteTeamMembers",
    },
  ],

  keySelector: {
    addKeys: "keys",
    createFirstDataset: "dataset",
    createFirstEvaluation: "evaluation",
    RunFirstExperiment: "experiment",
    SetupObsabilityInApplication: "observe",
    inviteTeamMembers: "invite",
  },
};
