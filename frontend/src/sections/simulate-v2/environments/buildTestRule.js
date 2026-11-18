/*
  Which builds can leave "What should this environment test?" empty: the ones
  whose agent the builder can read — a hosted platform's prompt and tools, a
  repository or uploaded code, a prompt built here (builder.md step 2: "answer
  only requirements the harness cannot infer from the stored contract/source").
  An endpoint or phone number only shows what it says back, and a scratch
  build has no agent, so those still ask.
*/
export const READABLE_SOURCE_KINDS = [
  "platform",
  "repo",
  "upload",
  "builtin",
  "prompt",
];

/** The connect picker's choices whose agent the builder reads. */
export const READABLE_CONNECT_CHOICES = [
  "hosted",
  "repo",
  "upload",
  "coding",
  "framework",
  "builtin",
];

export const testIsOptional = (kind) => READABLE_SOURCE_KINDS.includes(kind);
