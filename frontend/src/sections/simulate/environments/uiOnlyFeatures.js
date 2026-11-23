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
    ask: "an endpoint to attach or change the agent an environment tests, with a run readiness check for the agent picked",
    note: "UI only: the agent picked is kept in this browser, and connecting a new one adds a sample agent. Nothing is sent.",
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
  environmentSources: {
    label: "Environment files",
    ask: "per-file state (reading, rows rejected, can't read, not supported), the parsed table or text preview with rejected rows, per-column personal data handling, tool, actor and scenario proposals cited to the file, and whether a Knowledge base file changed since it was added; in the draft review, each change's cited file with its name and the spot as people read it (used[].name, used[].place), and each failed change's file name and fix (source_name, fix: supply the records or attach a file)",
    note: "UI only: files are read from sample data and nothing reaches the environment.",
  },
  environmentSettings: {
    label: "Environment settings",
    ask: "editing simulation defaults, egress, environment variables and the simulated user's model",
  },
  designReview: {
    label: "Review the design",
    ask: "a design job endpoint (see #3612) that reads the description and attached files, returns the proposed records, tools, rules, people and scenarios with their sources and open questions, saves the draft, and starts the build on approval",
    note: "UI only: the design is proposed in this browser from sample rules. Nothing is sent, and approving runs a sample build.",
  },
  designBuild: {
    label: "Build an approved design",
    ask: "the design job's approve and build (see #3612): approving creates the draft environment and starts the cloud build, polled for its stages (records and data, tool handlers, rules and checks, scenarios) and its validation checks with what failed; the built draft's review and Save as v1 through the drafts API; a suggested_name on the design job",
    note: 'UI only: the environment is this browser\'s design draft, built on a timer, and a records file named with "broken" fails a check. Review changes lists what the build made and Save keeps v1 here. Nothing is built or saved.',
  },
  designDocuments: {
    label: "File uploads",
    ask: "the design documents upload (see #3612) wired here: one request per file with its progress, answered with the stored document's id, and refused as too large, unsupported or a repeated name",
    note: "UI only: uploads are simulated in this browser. Nothing is sent.",
  },
  environmentFiles: {
    label: "Files and what to test on build forms",
    ask: "build requests that accept attached files (with a personal-data choice) and a line saying what the environment should test, read into the design before the build",
    note: "UI only: the files and the test line stay in this form. The build doesn't receive them yet.",
  },
});
