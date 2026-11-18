import { ANSWER_TYPES } from "../instantEvalPayloads";

// Questions worth asking each kind of row, offered on an empty composer.
export const EXAMPLES = {
  traces: [
    {
      question:
        "Did the agent promise a refund without checking the order first?",
      answerType: ANSWER_TYPES.YES_NO,
    },
    {
      question: "Is the customer frustrated or upset?",
      answerType: ANSWER_TYPES.YES_NO,
    },
    {
      question: "What is the customer contacting support about?",
      answerType: ANSWER_TYPES.CATEGORY,
      categories: [
        "Refund",
        "Shipping",
        "Product question",
        "Account",
        "Other",
      ],
    },
  ],
  spans: [
    {
      question: "Did the model follow the system prompt?",
      answerType: ANSWER_TYPES.YES_NO,
    },
    {
      question: "Does the output make a claim the input doesn't support?",
      answerType: ANSWER_TYPES.YES_NO,
    },
    {
      question: "What kind of step is this?",
      answerType: ANSWER_TYPES.CATEGORY,
      categories: ["Planning", "Tool call", "Retrieval", "Answer", "Other"],
    },
  ],
  sessions: [
    {
      question: "Did the customer get what they came for?",
      answerType: ANSWER_TYPES.YES_NO,
    },
    {
      question: "Did the customer have to repeat themselves?",
      answerType: ANSWER_TYPES.YES_NO,
    },
    {
      question: "How did the conversation end?",
      answerType: ANSWER_TYPES.CATEGORY,
      categories: ["Resolved", "Handed to a human", "Abandoned", "Unclear"],
    },
  ],
  users: [
    {
      question: "Did the customer get what they came for?",
      answerType: ANSWER_TYPES.YES_NO,
    },
    {
      question: "Did the customer ask to speak to a human?",
      answerType: ANSWER_TYPES.YES_NO,
    },
    {
      question: "What did the customer come for?",
      answerType: ANSWER_TYPES.CATEGORY,
      categories: [
        "Order status",
        "Returns and refunds",
        "Product advice",
        "Account",
        "Other",
      ],
    },
  ],
};
