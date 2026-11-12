// Plain values kept in component memory, never browser storage. Call this at
// submission with the public target model, not the console transport/key.
// Copy messages so later editor changes cannot mutate the submitted request.
export function buildChatRequestSnapshot({
  model,
  messages,
  temperature,
  max_tokens,
  top_p,
  stream = false,
}) {
  const snapshot = {
    model,
    messages: JSON.parse(JSON.stringify(messages)),
    stream: Boolean(stream),
  };
  for (const [name, value] of Object.entries({ temperature, max_tokens, top_p })) {
    if (value != null) snapshot[name] = value;
  }
  return snapshot;
}
