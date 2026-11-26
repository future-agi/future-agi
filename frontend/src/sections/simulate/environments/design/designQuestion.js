export function designQuestion(conversation) {
  const input = conversation?.blocking_input;
  if (
    !input ||
    conversation.messages?.some((m) => m.reply_to === input.message_id)
  )
    return null;
  // Older harness versions send the same explicit approval choices and a Markdown proposal.
  return input.design_proposal ||
    (Array.isArray(input.options) &&
      input.options.some(
        (o) => typeof o === "string" && o.startsWith("Build this:"),
      ) &&
      input.options.some(
        (o) => typeof o === "string" && o.startsWith("Change something:"),
      ))
    ? input
    : null;
}
