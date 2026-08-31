/**
 * Display order, with the tab each stage's output lands in. Kept out of the component
 * file so fast refresh keeps working — a module that exports both a component and a
 * constant is reloaded wholesale.
 */
export const ALK_STAGES = [
  { key: "reception", label: "Agent", tab: null },
  { key: "understand", label: "Contract", tab: "contract" },
  { key: "build", label: "Environment", tab: "world" },
  { key: "scenarios", label: "Scenarios", tab: "scenarios" },
  // Runs is hidden for now — uncomment to bring the stage back.
  // { key: "run", label: "Runs", tab: "runs" },
];
