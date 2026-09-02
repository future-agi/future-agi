/**
 * Environment catalog.
 *
 * An environment is a composite:
 *   agentType — the kind of agent it needs (this is also how the gallery groups)
 *   surface   — the channel the agent is reached on
 *   seed      — the mock world: tables, row counts, notable fixtures
 *   tools     — what the agent may call inside that world
 *   rules     — constraints the graders lean on
 *
 * Grouping by agent type rather than by business domain means the Environments
 * gallery and the Connect step present the same taxonomy, and picking an
 * environment already answers "what kind of agent is this" — which is why the
 * connect step can skip its type picker entirely.
 *
 * The technical set mirrors HUD's library; the voice and chat environments are
 * ours. Agent-type groups with no template simply do not render a section.
 */

import { AGENT_TYPE_GROUPS, getAgentType } from "./agentTypes.js";
import { TWIN_CATALOG } from "./twins.js";

/*
  Generate one env template per twin service so browsing the twin
  catalog happens on the same Environments page as every other
  template. Each entry is a fully-formed template (agentType,
  surface, tools, rules, evalPreset) so downstream code
  (TemplateSetupPanel, gallery grouping, compat check) treats them
  the same as any other template. Twin identity is carried on
  `twinBacking` — the same shape the two hand-authored twin
  templates below use — plus `singleService: true` so the setup
  panel knows to one-click-provision instead of running the
  multi-service wizard.
*/
const twinServiceTemplates = TWIN_CATALOG.map((twin) => ({
  id: `env-twin-service-${twin.id}`,
  agentType: "twin_backed",
  name: twin.name,
  surface: twin.id === "gmail" ? "email"
    : twin.id === "slack" || twin.id === "discord" ? "chat"
    : twin.id === "github" || twin.id === "linear" || twin.id === "jira" ? "coding"
    : "chat",
  domain: "twin",
  tagline: `${twin.name} sandbox`,
  description: `A live ${twin.name} sandbox for your agent — provisioned per run, torn down after. Evals check what actually landed.`,
  official: true,
  popularity: 1200,
  difficulty: "Advanced",
  seed: { tables: [] },
  twinBacking: {
    services: [twin.id],
    seedPrompt: "",
  },
  singleService: true,
  tools: (twin.depth || []).slice(0, 4).map((label) => ({ name: `${twin.id}.${label.toLowerCase().replace(/\s+/g, "_")}`, desc: `${label} operations` })),
  rules: [
    `Never write outside the target ${twin.name} target — no cross-service side effects`,
    "Match the tone and format the sandbox seed established",
  ],
  evalPreset: ["task_success", "twin_end_state_match", "twin_no_extra_writes"],
}));

export const ENVIRONMENT_TEMPLATES = [
  /* ─────────────────────── Voice & chat ─────────────────────── */
  {
    id: "env-voice-support",
    agentType: "voice_platform",
    name: "Customer Support Line",
    surface: "voice",
    domain: "ecommerce",
    tagline: "Inbound phone support for an online storefront",
    description:
      "A returns-and-orders phone line for a mid-size retailer. The agent answers calls chasing deliveries, requesting refunds and disputing charges.",
    official: true,
    popularity: 4820,
    difficulty: "Starter",
    seed: {
      tables: [
        { name: "orders", rows: 500, note: "12% delayed, 6% lost in transit" },
        { name: "customers", rows: 200, note: "40 with loyalty tier" },
        { name: "returns", rows: 85, note: "20 outside the return window" },
        { name: "products", rows: 340, note: "18 discontinued" },
      ],
    },
    tools: [
      { name: "lookup_order", desc: "Fetch order by ID, email or phone" },
      { name: "shipment_status", desc: "Live carrier tracking" },
      { name: "issue_refund", desc: "Refund up to the order total" },
      { name: "escalate_to_human", desc: "Warm transfer to a supervisor" },
    ],
    rules: [
      "Refunds above $200 need supervisor approval",
      "Never confirm identity from the phone number alone",
      "Return window is 30 days from delivery",
      "Do not disclose another customer's order details",
    ],
    evalPreset: ["task_success", "policy_adherence", "tone", "latency"],
  },
  {
    id: "env-chat-banking",
    agentType: "chat_webhook",
    name: "Retail Banking Assistant",
    surface: "chat",
    domain: "fintech",
    tagline: "Regulated chat support with hard compliance lines",
    description:
      "In-app chat for a retail bank. Card disputes, standing orders and fraud reports — with strict KYC and disclosure rules the grader enforces.",
    official: true,
    popularity: 2760,
    difficulty: "Advanced",
    seed: {
      tables: [
        { name: "accounts", rows: 400, note: "30 frozen, 12 joint" },
        { name: "transactions", rows: 15000, note: "180 flagged as suspicious" },
        { name: "cards", rows: 520, note: "45 reported lost" },
        { name: "disputes", rows: 140, note: "35 past SLA" },
      ],
    },
    tools: [
      { name: "verify_identity", desc: "Two-factor KYC challenge" },
      { name: "list_transactions", desc: "Statement search" },
      { name: "freeze_card", desc: "Immediate card block" },
      { name: "raise_dispute", desc: "Open a chargeback case" },
    ],
    rules: [
      "Two identity factors before any account detail is read out",
      "Never give investment advice",
      "Fraud reports must be raised within the same session",
      "Always state the dispute resolution window (10 business days)",
    ],
    evalPreset: ["task_success", "compliance", "pii_leakage", "hallucination"],
  },
  {
    id: "env-chat-travel",
    agentType: "chat_webhook",
    name: "Airline Rebooking Assistant",
    surface: "chat",
    domain: "travel",
    tagline: "Disruption chat where every option costs money",
    description:
      "In-app chat for an airline during disruption. Passengers arrive angry about cancelled flights and want rebooking, refunds or compensation — and the rules about which they are owed are strict, so the grader checks the entitlement before the promise.",
    official: true,
    popularity: 3240,
    difficulty: "Advanced",
    seed: {
      tables: [
        { name: "bookings", rows: 900, note: "140 on cancelled flights" },
        { name: "flights", rows: 260, note: "18 cancelled, 34 delayed over 3h" },
        { name: "fare_rules", rows: 45, note: "12 non-refundable" },
        { name: "compensation_claims", rows: 75, note: "22 already paid" },
      ],
    },
    tools: [
      { name: "lookup_booking", desc: "Find a booking by reference or email" },
      { name: "search_flights", desc: "Alternative flights on the same route" },
      { name: "rebook_segment", desc: "Move a passenger to another flight" },
      { name: "check_compensation", desc: "Entitlement under the delay rules" },
      { name: "issue_voucher", desc: "Meal or hotel voucher during disruption" },
    ],
    rules: [
      "Never promise compensation before check_compensation returns eligible",
      "Offer same-day alternatives before discussing a refund",
      "State the fare difference before confirming any rebooking",
      "Do not rebook without confirming the passenger's booking reference",
    ],
    evalPreset: ["task_success", "policy_adherence", "hallucination", "tone"],
  },
  {
    id: "env-browser",
    agentType: "browser_agent",
    name: "Browser",
    surface: "browser",
    domain: "software",
    tagline: "A 2048 game and a todo app, in a real browser",
    description:
      "Browser agent environment: a 2048 game and a todo app the agent plays and operates in a real browser. Tests grounding, planning and whether clicks land where the agent thinks they do.",
    official: true,
    popularity: 3110,
    difficulty: "Intermediate",
    seed: {
      tables: [
        { name: "todo_items", rows: 120, note: "some already completed" },
        { name: "game_states", rows: 40, note: "seeded 2048 boards" },
        { name: "sessions", rows: 60 },
      ],
    },
    tools: [
      { name: "browser", desc: "Click, type, scroll, navigate" },
      { name: "read_screen", desc: "Accessibility tree snapshot" },
      { name: "screenshot", desc: "Capture the current frame" },
    ],
    rules: [
      "Every click must land on the element the agent named",
      "The 2048 board may only advance by a legal move",
      "Completed todos must not be deleted",
    ],
    evalPreset: ["task_success", "ui_grounding", "step_efficiency", "safety"],
  },

  /* ─────────────────────── Simulation ─────────────────────── */
  {
    id: "env-robotics",
    agentType: "sim_agent",
    name: "Robotics",
    surface: "sim",
    domain: "robotics",
    tagline: "MuJoCo + LIBERO manipulation tasks",
    description:
      "Standard robotics simulation tasks in MuJoCo with LIBERO evaluation. The policy observes joint and object state, emits actions, and is scored by each task's own success predicate.",
    official: true,
    popularity: 2410,
    difficulty: "Expert",
    seed: {
      tables: [
        { name: "tasks", rows: 130, note: "LIBERO-90 plus long-horizon" },
        { name: "scenes", rows: 40, note: "kitchen, desk, shelf layouts" },
        { name: "objects", rows: 220, note: "varied mass and friction" },
        { name: "demos", rows: 5000, note: "teleop demonstrations" },
      ],
    },
    tools: [
      { name: "reset", desc: "Start a fresh episode" },
      { name: "get_observation", desc: "Joint, gripper and object state" },
      { name: "step", desc: "Apply an action for one control tick" },
      { name: "render", desc: "Capture the viewport frame" },
    ],
    rules: [
      "Actions must stay inside the robot's joint limits",
      "Objects may not be teleported or clipped through geometry",
      "Episodes are capped at the task's step budget",
      "Success is the task's own predicate, not self-report",
    ],
    evalPreset: ["task_success", "reward_score", "constraint_violation", "step_efficiency"],
  },
  {
    id: "env-worldsim",
    agentType: "sim_agent",
    name: "Worldsim Robotics",
    surface: "sim",
    domain: "robotics",
    tagline: "A Newton physics scene as a live environment",
    description:
      "A Newton physics scene running as a live environment. Richer contact dynamics than the standard benchmarks, so policies that memorised a simulator tend to fall apart here.",
    official: true,
    popularity: 1180,
    difficulty: "Expert",
    seed: {
      tables: [
        { name: "scenes", rows: 24, note: "deformables and articulated bodies" },
        { name: "materials", rows: 60, note: "friction and restitution varied" },
        { name: "episodes", rows: 900 },
      ],
    },
    tools: [
      { name: "reset", desc: "Rebuild the scene" },
      { name: "query_state", desc: "Full rigid-body state" },
      { name: "apply_action", desc: "Torque or position target" },
      { name: "advance_physics", desc: "Step the solver" },
    ],
    rules: [
      "The solver must stay stable — no NaN or exploding states",
      "Force and torque limits are hard constraints",
      "Scene randomisation seeds may not be read by the policy",
    ],
    evalPreset: ["task_success", "reward_score", "constraint_violation", "step_efficiency"],
  },
  {
    id: "env-videogamebench",
    agentType: "game_agent",
    name: "Video Game Bench",
    surface: "sim",
    domain: "gaming",
    tagline: "Classic Game Boy titles, played from pixels",
    description:
      "Evaluating agents on classic Game Boy games. The agent sees frames and presses buttons — no game-specific API — so progress depends on reading the screen and forming a plan.",
    official: true,
    popularity: 1960,
    difficulty: "Expert",
    seed: {
      tables: [
        { name: "titles", rows: 12, note: "platformers, puzzle, RPG" },
        { name: "checkpoints", rows: 180, note: "graded milestones" },
        { name: "save_states", rows: 96 },
      ],
    },
    tools: [
      { name: "read_frame", desc: "Current screen as pixels" },
      { name: "press_button", desc: "D-pad, A, B, start, select" },
      { name: "read_score", desc: "The game's own counter" },
    ],
    rules: [
      "Inputs are capped at a human-plausible rate",
      "Save states may not be used to retry a failed sequence",
      "Score comes from the game, never from the agent's claim",
    ],
    evalPreset: ["task_success", "reward_score", "rule_inference", "step_efficiency"],
  },
  {
    id: "env-arc-agi-3",
    agentType: "game_agent",
    name: "ARC-AGI-3",
    surface: "sim",
    domain: "gaming",
    tagline: "Interactive games with rules you have to work out",
    description:
      "ARC-AGI-3 public tasks: interactive games that test an agent's ability to learn the rules of a novel world from scratch, within a single episode and without prior exposure.",
    official: true,
    popularity: 2870,
    difficulty: "Expert",
    seed: {
      tables: [
        { name: "games", rows: 60, note: "each with unseen mechanics" },
        { name: "levels", rows: 240, note: "escalating difficulty" },
        { name: "action_spaces", rows: 60, note: "differs per game" },
      ],
    },
    tools: [
      { name: "observe", desc: "Current grid state" },
      { name: "act", desc: "Take one action from this game's space" },
      { name: "reset", desc: "Restart the level" },
    ],
    rules: [
      "No external hints or documentation about the game",
      "Rules must be inferred inside the episode",
      "Each level has a fixed action budget",
    ],
    evalPreset: ["task_success", "rule_inference", "reward_score", "step_efficiency"],
  },

  /* ─────────────────────── Tools & protocol ─────────────────────── */
  {
    id: "env-deep-research",
    agentType: "mcp_agent",
    name: "Deep Research",
    surface: "mcp",
    domain: "research",
    tagline: "Live web research where every claim needs a source",
    description:
      "Live deep research environment: web search via Exa, plus people and company research. The agent produces a briefing and is graded on whether each claim traces back to a real retrieved source.",
    official: true,
    popularity: 3650,
    difficulty: "Advanced",
    seed: {
      tables: [
        { name: "queries", rows: 240, note: "60 with contradictory sources" },
        { name: "companies", rows: 300, note: "filings and transcripts" },
        { name: "people", rows: 800, note: "40 with name collisions" },
      ],
    },
    tools: [
      { name: "web_search", desc: "Exa search over the live web" },
      { name: "fetch_page", desc: "Retrieve and read a result" },
      { name: "find_company", desc: "Company and funding lookup" },
      { name: "cite", desc: "Attach a claim to a source span" },
    ],
    rules: [
      "Every factual claim must carry a citation to a retrieved source",
      "State the as-of date when figures are time-sensitive",
      "Resolve name collisions before reporting on a person",
      "Say so explicitly when the web does not answer the question",
    ],
    evalPreset: ["task_success", "hallucination", "completeness", "tool_correctness"],
  },
  {
    id: "env-autonomous-business",
    agentType: "api_agent",
    name: "Autonomous Business",
    surface: "api",
    domain: "operations",
    tagline: "Turn real demand into verified business value",
    description:
      "Turning real-world demand into verified value: the agent takes an inbound opportunity through to a delivered, checkable outcome. Graded on the outcome, not on activity.",
    official: true,
    popularity: 1530,
    difficulty: "Expert",
    seed: {
      tables: [
        { name: "leads", rows: 400, note: "35% unqualified" },
        { name: "orders", rows: 260, note: "40 needing follow-up" },
        { name: "suppliers", rows: 90, note: "8 unreliable" },
        { name: "ledger", rows: 1200, note: "margin per transaction" },
      ],
    },
    tools: [
      { name: "qualify_lead", desc: "Score and filter demand" },
      { name: "quote", desc: "Price a piece of work" },
      { name: "place_order", desc: "Commit to a supplier" },
      { name: "verify_delivery", desc: "Confirm the outcome landed" },
    ],
    rules: [
      "Never commit spend above the per-task budget",
      "A deal only counts once delivery is verified",
      "Margin may not go negative on any transaction",
      "Unreliable suppliers require a fallback before committing",
    ],
    evalPreset: ["task_success", "policy_adherence", "tool_correctness", "safety"],
  },
  {
    id: "env-gdpval",
    agentType: "api_agent",
    name: "GDPval",
    surface: "api",
    domain: "operations",
    tagline: "Real-world business tasks across occupations",
    description:
      "Evaluating agents on real-world business scenarios drawn from actual occupations — the deliverable is a work product a professional would recognise, graded against expert reference output.",
    official: true,
    popularity: 2240,
    difficulty: "Expert",
    seed: {
      tables: [
        { name: "tasks", rows: 220, note: "44 occupations" },
        { name: "reference_outputs", rows: 220, note: "expert-authored" },
        { name: "source_files", rows: 900, note: "spreadsheets, decks, PDFs" },
      ],
    },
    tools: [
      { name: "read_brief", desc: "The task as a professional receives it" },
      { name: "read_files", desc: "Attached source material" },
      { name: "produce_deliverable", desc: "Submit the work product" },
    ],
    rules: [
      "The deliverable must match the requested format exactly",
      "Every figure must be traceable to a source file",
      "Do not invent data the brief did not supply",
    ],
    evalPreset: ["task_success", "completeness", "hallucination", "diff_quality"],
  },

  /* ─────────────────────────── Code ─────────────────────────── */
  {
    id: "env-coding",
    agentType: "coding_agent",
    name: "Coding",
    surface: "cli",
    domain: "software",
    tagline: "Fix a bug in a Python web app, graded by tests",
    description:
      "The agent gets a Python web app with a reproducible bug and a failing test. It must find the fault, patch it, and leave the suite green without touching the tests.",
    official: true,
    popularity: 4180,
    difficulty: "Advanced",
    seed: {
      tables: [
        { name: "repos", rows: 12, note: "flask and django apps" },
        { name: "issues", rows: 60, note: "each with a failing test" },
        { name: "test_suites", rows: 12, note: "avg 130 tests" },
      ],
    },
    tools: [
      { name: "shell", desc: "Run any command in the container" },
      { name: "read_file", desc: "Read repository files" },
      { name: "apply_patch", desc: "Write a diff" },
      { name: "run_tests", desc: "Execute the suite" },
    ],
    rules: [
      "The fix must not edit the test files",
      "All pre-existing tests must still pass",
      "No network access during the patch phase",
    ],
    evalPreset: ["task_success", "test_pass_rate", "diff_quality", "step_efficiency"],
  },
  {
    id: "env-ml-research",
    agentType: "coding_agent",
    name: "ML Research & Training",
    surface: "cli",
    domain: "ml",
    tagline: "Research and training workflows, on GPU",
    description:
      "A GPU box with datasets, a training harness and a budget. The agent runs experiments, reads the curves and improves a metric — without leaking the test set or blowing the compute cap.",
    official: true,
    popularity: 2960,
    difficulty: "Expert",
    seed: {
      tables: [
        { name: "datasets", rows: 18, note: "held-out splits enforced" },
        { name: "experiments", rows: 340, note: "80 diverged" },
        { name: "checkpoints", rows: 620 },
        { name: "gpu_budget", rows: 1, note: "capped GPU-hours per task" },
      ],
    },
    tools: [
      { name: "launch_job", desc: "Queue a training run" },
      { name: "read_metrics", desc: "Loss and eval curves" },
      { name: "edit_config", desc: "Change hyperparameters" },
      { name: "run_eval", desc: "Score a checkpoint" },
    ],
    rules: [
      "The test split may never be trained on",
      "Every reported number must come from run_eval, not from the loss curve",
      "Stay inside the GPU-hour budget",
      "Seeds and hyperparameters must be logged for every run",
    ],
    evalPreset: ["task_success", "reproducibility", "hallucination", "step_efficiency"],
  },
  {
    id: "env-ml-triage",
    agentType: "coding_agent",
    name: "ML Triage",
    surface: "cli",
    domain: "ml",
    tagline: "Diagnose broken training runs, on CPU",
    description:
      "Training runs that failed, stalled or silently regressed. The agent reads logs and configs to find the actual cause — cheap to run, because everything needed is already on disk.",
    official: true,
    popularity: 1420,
    difficulty: "Advanced",
    seed: {
      tables: [
        { name: "failed_runs", rows: 180, note: "OOM, NaN, silent regressions" },
        { name: "logs", rows: 180, note: "avg 40k lines" },
        { name: "configs", rows: 180, note: "diffs against a known-good run" },
      ],
    },
    tools: [
      { name: "read_logs", desc: "Tail or grep a run's output" },
      { name: "diff_configs", desc: "Compare against a healthy run" },
      { name: "reproduce_run", desc: "Re-run a short repro on CPU" },
      { name: "file_report", desc: "Submit the diagnosis" },
    ],
    rules: [
      "The diagnosis must cite the log line that proves it",
      "Reproduce before concluding, where a repro is possible",
      "Do not recommend a restart without a root cause",
    ],
    evalPreset: ["task_success", "hallucination", "reproducibility", "step_efficiency"],
  },
  {
    id: "env-verilog",
    agentType: "coding_agent",
    name: "Verilog",
    surface: "cli",
    domain: "hardware",
    tagline: "RTL and verification graded by real EDA flows",
    description:
      "The agent solves a Verilog/SystemVerilog task against a spec and a testbench, checked by an actual simulation and lint flow rather than a string match.",
    official: true,
    popularity: 1090,
    difficulty: "Expert",
    seed: {
      tables: [
        { name: "specs", rows: 90, note: "ALUs, FSMs, arbiters, FIFOs" },
        { name: "testbenches", rows: 90, note: "golden reference outputs" },
        { name: "lint_rules", rows: 40, note: "synthesis-blocking subset" },
      ],
    },
    tools: [
      { name: "write_rtl", desc: "Author or edit a module" },
      { name: "run_simulation", desc: "Simulate against the testbench" },
      { name: "run_lint", desc: "Check synthesisability" },
      { name: "read_waveform", desc: "Inspect signal traces" },
    ],
    rules: [
      "The module must pass the provided testbench unmodified",
      "No inferred latches in synthesisable blocks",
      "Stay inside the synthesisable language subset",
      "Clock-domain crossings need explicit synchronisers",
    ],
    evalPreset: ["task_success", "test_pass_rate", "diff_quality", "constraint_violation"],
  },

  /* ─────────────────────────── Service twins ─────────────────────────── */
  /*
    Twin-backed templates. Unlike the seeded-data envs above, these
    ship a `twinBacking` object that names live services (Slack,
    Notion, Gmail, Salesforce) plus a natural-language seed prompt.
    Setting one up (UseTemplate.finish) provisions the twin, seeds
    the sandbox, and drops starter scenarios that exercise the exact
    services listed here.

    Two curated templates for now — the two shapes we hear most from
    design partners: a support desk and a RevOps copilot. Each names
    the services, the initial world, and the eval preset that a real
    version of that agent would be graded on.
  */
  {
    id: "env-twin-support-desk",
    agentType: "twin_backed",
    name: "Support desk (twin)",
    surface: "chat",
    domain: "support",
    tagline: "Slack + Notion + Gmail sandbox, seeded with real support state",
    description:
      "A clone-backed environment for a support copilot. Slack channels seeded with active tickets, a Notion knowledge base of playbooks and product FAQs, and a Gmail inbox with unread refund requests. Runs restart against a fresh copy of the same seeded state every time.",
    official: true,
    popularity: 2140,
    difficulty: "Advanced",
    seed: { tables: [] },
    twinBacking: {
      services: ["slack", "notion", "gmail"],
      seedPrompt:
        "Slack: #support-urgent has 3 escalated customer threads (angry, waiting >24h), #general has 5 recent product-question messages. Notion: a Playbooks database (12 pages) with refund, escalation and outage-response runbooks; a shared Pricing FAQ page. Gmail: 4 unread emails tagged Support — two refund requests, one legal escalation, one meeting invite.",
    },
    tools: [
      { name: "slack.post_message", desc: "Reply in-thread or in a channel" },
      { name: "slack.dm", desc: "Direct-message a teammate" },
      { name: "notion.read_page", desc: "Fetch a playbook or FAQ" },
      { name: "notion.add_comment", desc: "Note a follow-up on a database row" },
      { name: "gmail.reply", desc: "Reply to an inbound email in-thread" },
      { name: "gmail.apply_label", desc: "Tag an email for triage" },
    ],
    rules: [
      "Never reply in the wrong Slack channel",
      "Escalations go by DM to the on-call, not in public channels",
      "Refund policy comes from the Notion playbook, not the model",
      "Never send more than one reply per customer thread",
    ],
    evalPreset: [
      "task_success",
      "twin_end_state_match",
      "twin_write_target",
      "twin_no_extra_writes",
      "tone",
    ],
  },
  {
    id: "env-twin-revops",
    agentType: "twin_backed",
    name: "RevOps copilot (twin)",
    surface: "email",
    domain: "sales",
    tagline: "Gmail + Salesforce + Notion twin — email in, CRM writes out",
    description:
      "A clone-backed environment for a RevOps copilot. Gmail is the inbound surface (renewal questions, refund asks, product asks from CSMs), Salesforce holds accounts + opportunities, and Notion carries the enablement playbooks. Each run resets both the inbox and the CRM to the same seed.",
    official: true,
    popularity: 1520,
    difficulty: "Advanced",
    seed: { tables: [] },
    twinBacking: {
      services: ["gmail", "salesforce", "notion"],
      seedPrompt:
        "Gmail: 3 unread emails — two renewal questions from Acme and Beacon Corp CSMs, one refund escalation. Salesforce: 4 accounts (Acme, Beacon Corp, Zenith, Cirrus), each with an active Q4 opportunity; Acme is 15 days from close, Beacon has no next step set. Notion: a Renewals Playbook page and a Refunds Policy page shared with CS.",
    },
    tools: [
      { name: "gmail.reply", desc: "Reply to an email in-thread" },
      { name: "salesforce.lookup_account", desc: "Fetch an account and its opportunities" },
      { name: "salesforce.log_task", desc: "Log a Task on an account or opportunity" },
      { name: "salesforce.update_opportunity", desc: "Change stage, next-step, or close date" },
      { name: "notion.read_page", desc: "Fetch the renewals or refund playbook" },
    ],
    rules: [
      "Never quote a discount that isn't in the playbook",
      "Every email reply that changes CRM state must log a Task",
      "Do not close an opportunity — only the account owner can",
      "Escalate refund requests over $10k to the CSM DM before replying",
    ],
    evalPreset: [
      "task_success",
      "twin_end_state_match",
      "twin_write_target",
      "twin_field_semantics",
      "twin_side_effect_budget",
    ],
  },
];

/*
  Splice the auto-generated single-service twin templates in after the
  two curated multi-service twin templates. Kept mutable at module
  init so consumers importing `ENVIRONMENT_TEMPLATES` get the full
  list — no code paths need to know about the generator.
*/
ENVIRONMENT_TEMPLATES.push(...twinServiceTemplates);

export const DIFFICULTIES = ["Starter", "Intermediate", "Advanced", "Expert"];

export const DIFFICULTY_COLOR = {
  Starter: "#16A34A",
  Intermediate: "#2563EB",
  Advanced: "#EA580C",
  Expert: "#DC2626",
};

export const getEnvironment = (id) =>
  ENVIRONMENT_TEMPLATES.find((e) => e.id === id);

const GROUP_BLURBS = {
  "Voice & chat": "Conversational agents on a phone line or a chat endpoint, graded on outcome, policy and tone.",
  "Computer use": "Agents that drive real software by looking at the screen.",
  Robotics: "Physical AI in a physics engine — policies that act and get scored.",
  "Games & worldsims": "Agents that play, explore and beat interactive worlds.",
  "Tools & protocol": "Agents acting through tool APIs against live-looking systems.",
  Code: "Agents that read, write and ship code, graded by real toolchains.",
  Composite: "Multi-agent systems, scored on handoffs as well as outcomes.",
};

/**
 * Bucket environments under the agent-type group they need, in the same order
 * the connect screen lists those groups. Empty groups drop out.
 */
export const groupByAgentGroup = (envs) =>
  AGENT_TYPE_GROUPS.map((group) => ({
    id: group,
    label: group,
    blurb: GROUP_BLURBS[group],
    items: envs.filter((e) => getAgentType(e.agentType)?.group === group),
  })).filter((g) => g.items.length);

