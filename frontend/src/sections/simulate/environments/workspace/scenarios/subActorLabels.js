// When a scenario's sub-actor enters, in the harness's own terms; a phone menu answers the call before anyone speaks.

// A sub-actor waiting on a tool call or a word that never comes enters after this agent turn, as the harness runs it.
const LATEST_ENTRY_TURN = 4;
// Someone who takes the phone waits until the person has it: never before the agent's second turn.
const TAKES_PHONE_FROM_TURN = 2;

export const subActorWhen = (trigger = {}, channel) => {
  const latest = `, or after the agent's turn ${LATEST_ENTRY_TURN}`;
  const handoff = channel === "audio_handoff";
  const first = handoff ? "second" : "first";
  switch (trigger.kind) {
    case "mid_journey":
      return `After the agent's turn ${handoff ? Math.max(trigger.turn, TAKES_PHONE_FROM_TURN) : trigger.turn}`;
    case "on_tool":
      return trigger.on_call > 1
        ? `On call ${trigger.on_call} of ${trigger.tool}${latest}`
        : `When the agent calls ${trigger.tool}${latest}`;
    case "on_keyword":
      return `When the agent says ${(trigger.words || []).join(", ")}${latest}`;
    case "probability":
      return `In about ${Math.round((trigger.p || 0) * 100)}% of calls, after the agent's ${first} turn`;
    default:
      return channel === "ivr"
        ? "Answers the call"
        : `After the agent's ${first} turn`;
  }
};
