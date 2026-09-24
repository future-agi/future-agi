// TODO: real builder chat
// The environment workspace's builder console, still unwired. It holds the
// Phase-2 BuilderConsole turn shape (role "builder"/"user", steps of
// { kind: "note", text }) and the send/running plumbing, so going live is a
// matter of replacing the timer with the builder chat backend; the return shape
// { turns, running, send } is what the console consumes, so keep it.
//
// Until then it must not pretend to act. The replies it used to give were keyed
// off the user's phrasing and claimed edits it had never made — "Dropped the
// matching scenarios", "Added that grader" — while nothing in the environment
// changed. There is exactly one reply now, and it says the console is not
// connected.
import { useEffect, useRef, useState } from "react";

export const REPLY_MS = 900;

export const NOT_CONNECTED_REPLY =
  "The builder chat isn't connected yet, so I can't change anything here. Edit scenarios, rules and evaluations directly in the panels on the right.";

const greetingText = (env) =>
  `${env.name} is ready. The builder chat isn't connected yet — make changes in the panels on the right.`;

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
          { id: nextId("a"), role: "builder", steps: [{ kind: "note", text: NOT_CONNECTED_REPLY }] },
        ]);
      }, REPLY_MS),
    );
  };

  return { turns, running, send };
}
