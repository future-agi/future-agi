// TODO: real builder chat
// Prototype console for the environment workspace: a seeded greeting plus mock
// replies on a fixed delay, in the Phase-2 BuilderConsole turn shape (role
// "builder"/"user", steps of { kind: "note", text }). To go live, replace the
// timer + mockWorkspaceReply with the builder chat backend; the return shape
// { turns, running, send } is what the console consumes, so keep it.
import { useEffect, useRef, useState } from "react";

export const REPLY_MS = 900;

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

  const send = (text) => {
    const trimmed = (text || "").trim();
    if (!trimmed) return;
    setTurns((prev) => [...prev, { id: nextId("u"), role: "user", text: trimmed }]);
    setRunning(true);
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
