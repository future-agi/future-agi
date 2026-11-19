// Recorded replay: a trial run again with every tool call answered from the
// original trial's recording. It never shows what the world would do now, so it
// is always named, never shown as a fresh run.
export const RECORDED_REPLAY_COPY = {
  label: "Recorded replay",
  about:
    "Every tool call was answered from the original trial's recording, and the test world never changed. It can't show what the world would do now.",
  action: "Replay with recorded tools",
  title: "Replay with recorded tools",
  explain:
    "The agent runs this conversation again. Every tool call it makes is answered from this trial's recording, and nothing in the test world changes. A tool call the trial never made stops the replay.",
  agentLabel: "Agent version",
  sameAgent: "Same as this trial",
  submit: "Replay",
  started: "Recorded replay started",
  failed: "The replay could not be started",
  original: (outcome) => `Original trial: ${outcome}`,
  openOriginal: "Open the original trial",
};
