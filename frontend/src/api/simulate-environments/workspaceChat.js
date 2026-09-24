// TODO: real builder chat
// Prototype console for the environment workspace: a seeded greeting plus mock
// replies on a fixed delay, in the Phase-2 BuilderConsole turn shape (role
// "builder"/"user", steps of { kind: "note", text }). To go live, replace the
// timer + mockWorkspaceReply with the builder chat backend; the return shape
// { turns, running, send } is what the console consumes, so keep it.
import { useEffect, useRef, useState } from "react";

import { getBuilderMode } from "src/sections/simulate/environments/buildEnvironment/console/builderModeBus";
import { getScenarioSelection, clearScenarioSelection } from "src/sections/simulate/environments/buildEnvironment/console/scenarioSelectionBus";

export const REPLY_MS = 900;

// When the user has scenario rows selected and sends a message, the builder
// treats it as a bulk edit against exactly those rows and names them back.
export function mockBulkEditReply(rows, userText) {
  const names = (rows || []).map((r) => r?.name || r?.title || "scenario");
  const shown = names.slice(0, 3).join(", ");
  const rest = names.length > 3 ? ` +${names.length - 3} more` : "";
  const n = names.length;
  return `Applied "${(userText || "").trim()}" to the ${n} selected scenario${n === 1 ? "" : "s"} — ${shown}${rest}. The Scenarios tab reflects the change.`;
}

// Guided-mode question generator, ported from the designer. Picks a decision
// that plausibly matches the user's message so the demo feels responsive:
// rule-adjacent → policy question, eval → grader question, scenario → coverage
// question, otherwise a generic default. Returns the AskUserQuestionCard shape
// { prompt, options, multiSelect, step, total }.
export function mockGuidedQuestion(userText) {
  const t = (userText || "").toLowerCase();

  if (/rule|refund|policy|escalat/.test(t)) {
    return {
      step: 1, total: 1,
      prompt: "How strict should the refund rule be?",
      multiSelect: false,
      options: [
        { label: "Require supervisor approval above $200",
          description: "Matches the current policy. Escalations still allowed." },
        { label: "Auto-approve up to $500",
          description: "Faster resolution, higher exposure. Above $500 still escalates." },
        { label: "Escalate every refund",
          description: "Slowest, safest. Every refund goes through a supervisor." },
      ],
    };
  }

  if (/eval|grader|grade|score/.test(t)) {
    return {
      step: 1, total: 1,
      prompt: "Which graders should the new evaluation include?",
      multiSelect: true,
      options: [
        { label: "task_success", description: "Was the caller's goal met?" },
        { label: "policy_adherence", description: "Did the agent follow the hard rules?" },
        { label: "tone", description: "LLM-graded against the tone rubric." },
        { label: "latency", description: "Any turn slower than the budget fails." },
      ],
    };
  }

  if (/scenario|persona|caller|customer/.test(t)) {
    return {
      step: 1, total: 1,
      prompt: "What kind of scenarios should I generate?",
      multiSelect: true,
      options: [
        { label: "Happy path", description: "Standard requests, no edge cases." },
        { label: "Adversarial", description: "Rushed callers, off-topic tangents, skepticism." },
        { label: "Tool-fault cases", description: "The tool returns unexpected results — does the agent handle it?" },
      ],
    };
  }

  return {
    step: 1, total: 1,
    prompt: "How would you like this applied?",
    multiSelect: false,
    options: [
      { label: "Apply to this environment version only",
        description: "The change lands on the current version; older versions keep their behavior." },
      { label: "Fork a new version",
        description: "Mint v(N+1) with the change and pin it active." },
    ],
  };
}

// Prototype builder replies keyed off the user's phrasing.
export function mockWorkspaceReply(userText) {
  const t = (userText || "").toLowerCase();
  if (/drop|remove|cut/.test(t) && /scenario/.test(t)) {
    return "Dropped the matching scenarios — the Scenarios tab on the right is updated.";
  }
  if (/add/.test(t) && /scenario/.test(t)) {
    return "Added a scenario. You'll see it in the Scenarios tab on the right.";
  }
  if (/rule|refund|escalat/.test(t)) {
    return "Updated the rule. The grader will enforce the new wording on the next run.";
  }
  if (/eval|grader|grade/.test(t)) {
    return "Added that grader on the Evaluations tab — it'll score every scenario on the next run.";
  }
  if (/run|fail|last/.test(t)) {
    return "Pulled that from the latest run — open the Runs tab on the right for the full breakdown.";
  }
  return "Applied that to the environment — the panels on the right reflect the change.";
}

const greetingText = (env) =>
  `${env.name} is live. Ask me to tweak scenarios, tighten a rule, or add an eval — or edit directly on the right.`;

export function useWorkspaceChat(env) {
  const [turns, setTurns] = useState([]);
  const [running, setRunning] = useState(false);

  const timers = useRef([]);
  const idCounter = useRef(0);
  const nextId = (prefix) => `${prefix}-${(idCounter.current += 1)}`;

  const envRef = useRef(env);
  envRef.current = env;

  // Seed the greeting once the env resolves. Idempotent: the prev.length guard
  // makes a StrictMode re-run or an env swap a no-op, so a deep link that
  // hydrates the env late still gets exactly one greeting.
  useEffect(() => {
    const current = envRef.current;
    if (!current) return;
    setTurns((prev) =>
      prev.length
        ? prev
        : [{ id: "ws-greet", role: "builder", steps: [{ kind: "note", text: greetingText(current) }] }],
    );
  }, [env?.id, env?.name]);

  // Tear down any in-flight reply timer on unmount so a deferred append never
  // fires after the console leaves the tree.
  useEffect(
    () => () => {
      timers.current.forEach(clearTimeout);
      timers.current = [];
    },
    [],
  );

  // `attachments` is accepted for parity with the console's onSend(text,
  // attachments) contract; the prototype drops them (nothing here uploads).
  const send = (text, _attachments) => {
    const trimmed = (text || "").trim();
    if (!trimmed) return;
    setTurns((prev) => [...prev, { id: nextId("u"), role: "user", text: trimmed }]);
    setRunning(true);

    // A message sent while scenario rows are selected is a bulk edit against
    // exactly those rows. Snapshot + clear the selection so the "Editing N
    // scenarios" chip drops on send, name the rows back, and skip guided mode
    // for this turn (the scope is already settled by the selection).
    const selection = getScenarioSelection();
    if (selection?.ids?.length > 0) {
      const rows = selection.rows;
      clearScenarioSelection();
      timers.current.push(
        setTimeout(() => {
          setRunning(false);
          setTurns((prev) => [
            ...prev,
            { id: nextId("a"), role: "builder", steps: [{ kind: "note", text: mockBulkEditReply(rows, trimmed) }] },
          ]);
        }, REPLY_MS),
      );
      return;
    }

    // Manual (guided) mode: the builder pauses at a real decision and asks the
    // user to settle it before applying, rendered as a Claude-style
    // AskUserQuestion card inline in the chat. In Auto (the default), it just
    // applies its own guess and moves on.
    if (getBuilderMode() !== "auto") {
      timers.current.push(
        setTimeout(() => {
          setRunning(false);
          const question = mockGuidedQuestion(trimmed);
          const qid = nextId("q");
          setTurns((prev) => [
            ...prev,
            {
              id: qid,
              role: "builder",
              steps: [
                { kind: "note", text: "Before I apply that, one decision:" },
                {
                  kind: "ask",
                  question,
                  onSubmit: (answers) => {
                    const chosen = (answers || []).join(", ");
                    setTurns((cur) => [
                      ...cur,
                      {
                        id: `${qid}-r`,
                        role: "builder",
                        steps: [{ kind: "note", text: `Applied — went with "${chosen}". The panels on the right reflect the change.` }],
                      },
                    ]);
                  },
                  onSkip: () => {
                    setTurns((cur) => [
                      ...cur,
                      {
                        id: `${qid}-s`,
                        role: "builder",
                        steps: [{ kind: "note", text: "Skipped — I used the default and moved on." }],
                      },
                    ]);
                  },
                },
              ],
            },
          ]);
        }, REPLY_MS),
      );
      return;
    }

    timers.current.push(
      setTimeout(() => {
        setRunning(false);
        setTurns((prev) => [
          ...prev,
          { id: nextId("a"), role: "builder", steps: [{ kind: "note", text: mockWorkspaceReply(trimmed) }] },
        ]);
      }, REPLY_MS),
    );
  };

  return { turns, running, send };
}
