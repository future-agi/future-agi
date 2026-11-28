// Older harness versions send the same explicit approval choices and a Markdown proposal.
const asksForApproval = (input) =>
  Boolean(
    input?.design_proposal ||
      (Array.isArray(input?.options) &&
        input.options.some(
          (o) => typeof o === "string" && o.startsWith("Build this:"),
        ) &&
        input.options.some(
          (o) => typeof o === "string" && o.startsWith("Change something:"),
        )),
  );

export function designQuestion(conversation) {
  const input = conversation?.blocking_input;
  if (
    !input ||
    conversation.messages?.some((m) => m.reply_to === input.message_id)
  )
    return null;
  return asksForApproval(input) ? input : null;
}

// The words that pick an option, the way the builder reads an answer: its id,
// its label, or the flattened "label: description".
const optionWords = (option) =>
  typeof option === "string"
    ? [option, option.split(": ")[0]]
    : [
        option.id,
        option.label,
        option.description
          ? `${option.label}: ${option.description}`
          : option.label,
      ];

// Whether the person approved a design proposal in the chat. The builder
// refuses an approval while the proposal still has dependencies to settle.
export function designApproved(conversation) {
  const messages = conversation?.messages || [];
  return messages.some((question) => {
    const payload = question.payload;
    if (
      question.kind !== "question" ||
      !asksForApproval(payload) ||
      payload.design_proposal?.dependencies?.length
    )
      return false;
    const approve = payload.options?.find((o) =>
      typeof o === "string" ? o.startsWith("Build this") : o?.id === "approve",
    );
    return Boolean(
      approve &&
        messages.some(
          (m) =>
            m.role === "user" &&
            m.reply_to === question.message_id &&
            optionWords(approve).includes(String(m.content || "").trim()),
        ),
    );
  });
}
