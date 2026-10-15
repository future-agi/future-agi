import PropTypes from "prop-types";
import { Button, Stack } from "@mui/material";

import { questionOptions } from "./harnessShared";

// The answerable part of an assistant `question` message. Answering sends the reply the harness
// waits on (environment-releases.md §7.5): a `user_response` with the chosen option. Once
// answered, the buttons stay visible but disabled so the transcript keeps the choice in view.
export default function ConversationPrompt({
  item,
  answered,
  disabled,
  onAnswer,
}) {
  if (item.kind === "question") {
    const options = questionOptions(item.payload);
    if (!options.length) return null;
    return (
      <Stack
        direction="row"
        spacing={1}
        sx={{ mt: 1, flexWrap: "wrap", rowGap: 1 }}
      >
        {options.map((option) => (
          <Button
            key={String(option.value)}
            size="small"
            variant="outlined"
            disabled={disabled || answered}
            onClick={() =>
              onAnswer({
                content: option.label,
                replyTo: item.message_id,
                kind: "user_response",
                payload: { value: option.value },
              })
            }
          >
            {option.label}
          </Button>
        ))}
      </Stack>
    );
  }
  return null;
}

ConversationPrompt.propTypes = {
  item: PropTypes.shape({
    message_id: PropTypes.string,
    kind: PropTypes.string,
    payload: PropTypes.object,
  }).isRequired,
  answered: PropTypes.bool,
  disabled: PropTypes.bool,
  onAnswer: PropTypes.func.isRequired,
};
