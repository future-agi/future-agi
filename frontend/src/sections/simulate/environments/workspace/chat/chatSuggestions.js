// Vel's suggested prompts per tab, shown above the builder's composer. A
// chip sends its text to the chat as if typed. World has none in his design.
export const CHAT_SUGGESTIONS = {
  overview: [
    "Summarise what's in this environment",
    "What's still missing before we can run?",
    "Explain the tools and rules to me",
  ],
  contract: [
    "When does a run end early?",
    "Add an actor who wants something else",
    "How is a run scored?",
  ],
  scenarios: [
    "Add a dispute case",
    "Add an edge case where a tool fails",
    "Rewrite the rushed-caller persona",
  ],
  evals: [
    "Add a grader for tool-choice correctness",
    "Tighten the hand-off quality bar",
    "Explain what each grader measures",
  ],
  runs: ["Summarise the last run", "Which scenarios fail most often?"],
  settings: [
    "Rotate my OpenAI key",
    "Which environment variables is the grader reading?",
    "Change the task timeout to 10 minutes",
  ],
};
