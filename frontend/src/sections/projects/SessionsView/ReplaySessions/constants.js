import _ from "lodash";

export const replaySessionsSteps = {
  new_group: [
    {
      id: 1,
      title: "Create Scenarios",
    },
    {
      id: 2,
      title: "Start Replay",
    },
    {
      id: 3,
      title: "Map Eval Variables",
    },
  ],
  existing_group: [
    {
      id: 1,
      title: "Create Scenarios",
    },
    {
      id: 2,
      title: "Confirm Replay",
    },
    {
      id: 3,
      title: "Add to Scenario Group",
    },
  ],
};

export const REPLAY_ITEMS = [
  {
    title: "Create new scenario group",
    description: "Create new scenarios and replay",
    iconSrc: "/icons/runTest/ic_settings.svg",
    id: "new_group",
  },
  // {
  //   title: "Add to existing scenario group",
  //   description: "Create new scenarios and add to existing scenario group",
  //   iconSrc: "/assets/icons/navbar/ic_sessions.svg",
  //   id: "existing_group",
  // },
];

// Bulk-action id of "Replay Sessions" / "Replay Calls" in the Observe toolbar.
export const REPLAY_ACTION_ID = "replay";

export const REPLAY_TYPES = {
  NEW_GROUP: "new_group",
  EXISTING_GROUP: "existing_group",
};
