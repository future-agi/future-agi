// When a scenario's actor comes in, in the five ways the harness runs: one meaning on every runtime.
// A phone menu answers the call before anyone speaks.

// Someone who takes the phone waits until the person has it: never before the agent's second turn.
const TAKES_PHONE_FROM_TURN = 2;

const seconds = (value) => `${Number(value) || 0} s`;

const where = (trigger) =>
  Object.entries(trigger.where || {})
    .map(([key, value]) => `${key} ${JSON.stringify(value)}`)
    .join(", ");

export const callActorWhen = (trigger = {}, channel) => {
  const handoff = channel === "audio_handoff";
  const first = handoff ? "second" : "first";
  const onCall = !channel;
  const start = onCall
    ? "Present from the start"
    : `Present from the start: after the agent's ${first} turn`;
  switch (trigger?.kind) {
    case "mid_journey":
      return `After the agent's turn ${handoff ? Math.max(trigger.turn, TAKES_PHONE_FROM_TURN) : trigger.turn}`;
    case "on_tool":
      return trigger.on_call > 1
        ? `On call ${trigger.on_call} of ${trigger.tool}`
        : `When the agent calls ${trigger.tool}`;
    case "on_keyword":
      return `When the agent says ${(trigger.words || []).join(", ")}`;
    case "contacted":
      return "When the agent contacts them";
    case "after_seconds":
      return `${seconds(trigger.seconds)} after the start`;
    case "conditional":
      return `Once a ${trigger.collection || "record"} record${where(trigger) ? ` with ${where(trigger)}` : ""} exists`;
    case "probability":
      return `In about ${Math.round((trigger.p || 0) * 100)}% of calls, ${start.toLowerCase()}`;
    default:
      return channel === "ivr" ? "Answers the call" : start;
  }
};

// The trigger that really runs for an actor in a scenario: the scenario's own, else the actor's activation.
export const effectiveTrigger = (entry, actor) =>
  entry?.trigger && Object.keys(entry.trigger).length
    ? entry.trigger
    : actor?.activation?.trigger || { kind: "always" };

// An actor's timeout and what happens when it does not answer in time; empty when it has no timeout.
export const callActorTimeout = (actor, names = {}) => {
  const activation = actor?.activation;
  if (!activation?.timeout_seconds) return "";
  const fallback = activation.fallback || "silent";
  const then =
    fallback === "silent"
      ? "nothing is sent"
      : fallback === "decline"
        ? "it declines"
        : names[fallback]
          ? `${names[fallback]} answers instead`
          : `${fallback} is taken for it`;
  return `Waits ${seconds(activation.timeout_seconds)}, then ${then}`;
};
