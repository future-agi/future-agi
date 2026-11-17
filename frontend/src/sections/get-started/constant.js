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
    title: "Datasets",
    description:
      "Build test data, then run prompts, evals and experiments against it.",
    image: "/assets/get_started/Add_dataset.png",
    imageDark: "/assets/get_started/Add_dataset_dark.png",
    buttonTitle: "Go to Datasets",
    learnMoreLink: docLinks.datasetOverview,
    actionLink: navigationLinks.dataset,
  },
  {
    title: "Experiments",
    description: "Compare prompts and models side by side on a dataset.",
    image: "/assets/get_started/Experiment.png",
    imageDark: "/assets/get_started/Experiment_dark.png",
    buttonTitle: "Go to Datasets",
    learnMoreLink: docLinks.experimentOverview,
    actionLink: navigationLinks.experiment,
  },
  {
    title: "Evals",
    description:
      "Create, test and reuse evals that score responses for accuracy, safety, tone and more.",
    image: "/assets/get_started/Evaluate.png",
    imageDark: "/assets/get_started/Evaluate_dark.png",
    buttonTitle: "Go to Evals",
    learnMoreLink: docLinks.evaluateOverview,
    actionLink: navigationLinks.evaluate,
  },
];

export const setupObserveVideoContent = {
  title: "What tracing is and how to set it up",
  description: "",
  srcThumbnail: "/assets/icons/action_buttons/ic_filled_play_button.svg",
  buttonTitle: "Watch video",
  linkTitle: "View docs",
  viewDocLink: docLinks.observeOverview,
  iframeVideo:
    "https://www.loom.com/embed/c4440ec37395445f8f520a28fa79cdab?sid=b381fd6d-c809-4825-9cba-1db5c5e640f7",
};

export const runFirstExperimentVideoContent = {
  title: "How to run an experiment",
  description: "",
  srcThumbnail: "/assets/icons/action_buttons/ic_filled_play_button.svg",
  buttonTitle: "Watch video",
  linkTitle: "View docs",
  viewDocLink: docLinks.experimentOverview,
  iframeVideo:
    "https://www.loom.com/embed/6670aff8264d4f1d86da26c77ba12f9c?sid=e84bd845-e3d4-4613-bbba-8b11187663f3",
};

export const createFirstEvalsLinks = {
  iframeVideoLinks:
    "https://www.loom.com/embed/e1b0bddb1eee47258200200e7402502d?sid=45f70a85-bf93-4b18-8677-a932d41ecec9",
  descriptions: [
    "Set up an evaluation for a prompt or model.",
    "Compare evaluation results to choose an approach.",
    "Save evaluations to reuse them.",
  ],
  links: [
    {
      link: "https://app.arcade.software/share/q2oR6KlsDatRQO6KouVG",
      title: "Evaluate a RAG app",
    },
    {
      link: "https://app.arcade.software/share/K7VPJyTtCce3d2By2eza",
      title: "Evaluate images",
    },
  ],
};

export const createFirstDatasetLinks = {
  iframeVideoLinks:
    "https://www.loom.com/embed/060248e374de46c0b9f261d98a2caa37?sid=ae8bce80-ecd5-47d1-b381-a56a8f4f9e10",
  descriptions: [
    "Add data from a CSV file, Hugging Face, the SDK or synthetic generation.",
    "Add columns that run a prompt or an evaluation on every row.",
    "Run experiments on a dataset to compare prompts and models.",
  ],
  links: [
    {
      link: "https://app.arcade.software/share/Wcws7iE8g1h8kWXbWuHl",
      title: "Add a column that runs a prompt",
    },
    {
      link: "https://app.arcade.software/share/o5INuCbOTaGE62llzjQe",
      title: "Create a synthetic dataset",
    },
    {
      link: "https://app.arcade.software/share/y4S9JB06Y0OQ48OQouJs",
      title: "Run an experiment",
    },
    {
      link: "https://app.arcade.software/share/8ALx82DeRsQT9s5fcNeq",
      title: "Annotate rows",
    },
    {
      link: "https://app.arcade.software/share/V1wYN0lI1Y07jlLjnWDF",
      title: "Optimize a prompt",
    },
  ],
};

export const getStartedPage = {
  contactUs: "https://futureagi.com/contact-us",
  getStartedVideo:
    "https://www.youtube.com/embed/6PLdnbMeC7k?si=IGY_DSHjkBrnMpZX",
};

export const menuesConstant = {
  menusList: [
    {
      id: 0,
      icon: "/assets/icons/navbar/ic_lock.svg",
      title: "Add provider keys",
      status: "incomplete",
      label: "addKeys",
    },
    {
      id: 1,
      icon: "/assets/icons/navbar/hugeicons.svg",
      title: "Create your first dataset",
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
      title: "Send your first trace",
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

// The steps listed for every reader, in this order. The step list, the
// summary and the step opened on arrival all read the steps from here.
export const listedSteps = (steps) => steps;

// The listed steps that count toward this reader's progress. The invite step
// counts only for a reader who can invite (countsInvite, from
// useInviteAccess); for anyone else it is listed, and shows as done, but is
// not counted. The step list's count, the summary and the step opened on
// arrival all read this one list.
export const countedSteps = (steps, countsInvite) =>
  steps.filter((item) => countsInvite || item.label !== "inviteTeamMembers");
