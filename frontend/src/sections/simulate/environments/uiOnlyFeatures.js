// Every part of the environment pages that is design without an API yet,
// with what the backend needs to wire it. UiOnlyFlag shows the ask in its
// tooltip; the backend asks list is generated from here.
export const UI_ONLY_FEATURES = Object.freeze({
  needsInput: {
    label: "Needs input",
    ask: "setup gaps per environment with their answers",
  },
  runInMoreEnvironments: {
    label: "Run in more environments",
    ask: "starting one run across several environments as a group",
  },
  runEstimate: {
    label: "Run estimate",
    ask: "a credit estimate and conversation cap for a run before it starts",
  },
  toolFit: {
    label: "Tools the world can't answer",
    ask: "a check before a run for tools the agent calls that the environment can't answer, and a rebuild that adds them",
  },
  runFromCode: {
    label: "Run from code",
    ask: "a CLI and SDK that run an environment by id",
  },
  connectAgent: {
    label: "Connect an agent",
    ask: "an endpoint to attach or change the agent an environment tests",
  },
  syncAgent: {
    label: "Sync agent",
    ask: "re-reading an agent's source and reporting what changed",
  },
  toolFidelity: {
    label: "Tool fidelity",
    ask: "a per-tool Fixed, Recorded, Simulated or Live setting",
  },
  toolFaults: {
    label: "Tool faults",
    ask: "per-tool fault injection settings",
  },
  mcpTools: {
    label: "MCP tools",
    ask: "adding, refreshing and removing tools from MCP servers",
  },
  compareRuns: {
    label: "Compare runs",
    ask: "a run comparison endpoint that pairs scenarios, flips and eval scores across runs",
    note: "UI only: the numbers are real, but the comparison is put together in the browser from each run's calls, not by the API.",
  },
  analyticsViews: {
    label: "Analytics views and layout",
    ask: "saving analytics views and widget layouts",
    note: "UI only: layout changes last until you reload. Views can't be saved yet.",
  },
  scenarioBatches: {
    label: "Scenario batches",
    ask: "who added which scenarios, how and when (batches with an author)",
  },
  scenarioChecks: {
    label: "Scenario checks",
    ask: "the checks that prove each scenario, with their code",
  },
  contractEdits: {
    label: "Contract edits",
    ask: "editing rules, use cases and argument limits",
  },
  actors: {
    label: "Actors",
    ask: "actors per environment with create, edit and remove",
  },
  scenarioSources: {
    label: "Scenario sources",
    ask: "turning Error Feed issues, failing cases, production calls, datasets and scripts into scenarios",
  },
  mustPass: { label: "Must-pass", ask: "a must-pass flag per scenario" },
  scenarioStates: {
    label: "Scenario states",
    ask: "scenario states (valid, re-check, outdated, stale, archived) with re-prove and archive",
  },
  evalRequired: {
    label: "Required evaluation",
    ask: "a required flag per evaluation",
  },
  evalHistory: {
    label: "Evaluation history",
    ask: "a change log per evaluation (added, edited, removed, with dates)",
  },
  runBaseline: { label: "Baseline run", ask: "a baseline run per environment" },
  chooseWinner: {
    label: "Choose winner",
    ask: "saving a chosen winner run with its weights",
  },
  deleteRun: { label: "Delete run", ask: "deleting a run" },
  shareRun: {
    label: "Share run",
    ask: "read-only share links and a PDF for a run",
  },
  rerunSnapshot: {
    label: "Re-run exact snapshot",
    ask: "re-running a run on its pinned scenario versions, environment version and seed",
  },
  regradeRun: {
    label: "Re-grade run",
    ask: "re-grading a finished run's stored calls after a grader changed, with its credit cost",
  },
  runEvalsOnCalls: {
    label: "Run evals on selected calls",
    ask: "grading chosen calls of a run with chosen evaluations, without new calls",
  },
  deleteEvalColumn: {
    label: "Delete eval column",
    ask: "removing one evaluation's results from a run",
  },
  environmentSettings: {
    label: "Environment settings",
    ask: "editing simulation defaults, egress, environment variables and the simulated user's model",
  },
});
