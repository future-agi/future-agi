/*
  Shared build recipe for the scratch flow.

  The pipeline the user watches on the ScratchBuildingView screen is
  the same pipeline whose transcript we want to preserve in the chat
  panel after they land on the review layout. Keeping the stages and
  the transcript-builder here (rather than duplicated on both sides)
  guarantees the recap the user sees on review matches, step for
  step, what streamed during the build. Also lets us re-render the
  history after a hard refresh — the env's stored intake is enough.
*/

export const SCRATCH_STAGES = [
  {
    id: "read",
    label: "Reading your flow",
    detail: "Parsing what the agent should do, its triggers, and where it hands off.",
    icon: "solar:document-text-linear",
    ms: 1400,
    chatTitle: "READING",
    steps: [
      { kind: "note", text: "Reading through your description. I'll pull out the triggers, the tools you mentioned, and where the agent hands off to a human." },
      { kind: "tool", label: "parse_flow", result: "3 triggers · 5 tools mentioned · 2 hand-offs" },
      { kind: "tool", label: "extract_entities", result: "Salesforce · Slack · refund policy · $500 threshold" },
    ],
  },
  {
    id: "tools",
    label: "Extracting tools & personas",
    detail: "Guessing the services the agent needs and the people it talks to.",
    icon: "solar:widget-6-linear",
    ms: 1600,
    chatTitle: "EXTRACTING",
    steps: [
      { kind: "note", text: "Spotted the tools and the people the agent talks to. I'll draft first-cut definitions you can tweak on the Personas tab." },
      { kind: "tool", label: "detect_tools", result: "salesforce.lookup_order · slack.post_message · refund.issue · escalate_to_human" },
      { kind: "tool", label: "draft_personas", result: "3 personas · frustrated buyer · repeat customer · abuse pattern" },
    ],
  },
  {
    id: "world",
    label: "Building the world",
    detail: "Seeding the awkward rows and edges the use cases actually need.",
    icon: "solar:database-linear",
    ms: 1800,
    chatTitle: "SEEDING",
    steps: [
      { kind: "note", text: "The tools have to hit something that answers truthfully. Seeding what the use cases need — including the awkward rows a world of happy customers wouldn't prove anything against." },
      { kind: "tool", label: "seed_world", result: "1,437 rows across 5 tables" },
      { kind: "file", path: "world/orders.py", note: "610 orders · 12 disputed · 3 in refund window" },
      { kind: "file", path: "world/handlers.py", note: "answers every tool call from real state" },
    ],
  },
  {
    id: "scenarios",
    label: "Drafting starter scenarios",
    detail: "Composing one scenario per real use case, each with sub-goals.",
    icon: "solar:list-check-linear",
    ms: 2000,
    chatTitle: "SCENARIOS",
    steps: [
      { kind: "note", text: "Drafted a starter pack. Golden-path cases, a couple of edge cases, and one adversarial to make sure the agent actually escalates." },
      { kind: "tool", label: "draft_scenarios", result: "8 drafted across the use-case range" },
      { kind: "tool", label: "gate · ready", result: "8 / 8 the world holds what each scenario presupposes" },
      { kind: "tool", label: "gate · solvable", result: "8 / 8 the reference solution passes the scenario" },
      { kind: "tool", label: "gate · not vacuous", result: "7 / 8 running nothing must fail the checks" },
    ],
  },
  {
    id: "evals",
    label: "Wiring eval graders",
    detail: "Picking graders that decide whether a run passed.",
    icon: "solar:target-linear",
    ms: 1200,
    chatTitle: "EVALUATIONS",
    steps: [
      { kind: "note", text: "Wired a small set of graders — task success, tool-choice correctness, and one LLM judge for hand-off quality. Add more from the Evaluations tab." },
      { kind: "tool", label: "task_success", result: "code · reads world state after the run" },
      { kind: "tool", label: "tool_choice", result: "code · was the right tool called at the right step" },
      { kind: "tool", label: "handoff_quality", result: "llm judge · scored 0-3 on courtesy and accuracy" },
    ],
  },
  {
    id: "ready",
    label: "Finalising",
    detail: "Packing everything up and handing you off to the review page.",
    icon: "solar:check-circle-linear",
    ms: 800,
    chatTitle: "READY",
    steps: [
      { kind: "note", text: "All done. Handing you off to the review page — tweak scenarios or evals on the tabs, or connect an agent and run the first simulation." },
    ],
  },
];

/*
  Recompose the transcript that streamed during the build screen so
  the review page opens with the full history in the chat panel —
  same shape the build-from-agent flow's Assistant sidebar keeps
  after derivation completes.

  Inputs: env.name and env.builtFrom.intake.flow (persisted on the
  env at build time). Output: an array of `turns` the
  AssistantConsole knows how to render.
*/
export function buildScratchTranscript(env) {
  const flow = env?.builtFrom?.intake?.flow || "";
  const turns = [
    {
      id: "greet",
      role: "assistant",
      steps: [{
        kind: "note",
        text: `Starting the build for ${env?.name || "your environment"}. I'll walk through it step by step — you can watch the pipeline on the right.`,
      }],
    },
  ];
  if (flow.trim()) {
    turns.push({ id: "u-flow", role: "user", text: flow.trim() });
  }
  SCRATCH_STAGES.forEach((stage) => {
    turns.push({
      id: `a-${stage.id}`,
      role: "assistant",
      title: stage.chatTitle,
      steps: stage.steps,
    });
  });
  return turns;
}
