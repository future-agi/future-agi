export const LIST_ITEMS = [
  {
    title: "Define Scenario Details",
    description:
      "Set the scenario name, description, agent definition, and number of scenarios you want to generate.",
  },
  {
    title: "Generate the Workflow",
    description:
      "Use the workflow builder, import a dataset, or upload scripts to create the conversation logic for your agent to follow.",
  },
  {
    title: "Add Personas and Inputs",
    description:
      "Select personas or set default persona behavior, and add custom columns to generate scenario variations.",
  },
];

export const SCENARIO_TYPES = {
  graph: {
    id: "workflow_builder",
    icon: "/assets/icons/navbar/ic_sessions.svg",
  },
  dataset: {
    id: "import_dataset",
    icon: "/assets/icons/navbar/hugeicons.svg",
  },
  script: {
    id: "upload_script",
    icon: "/assets/icons/components/ic_script.svg",
  },
};

export const SCENARIO_STATUS = {
  PROCESSING: "Processing",
  COMPLETED: "Completed",
  FAILED: "Failed",
};
