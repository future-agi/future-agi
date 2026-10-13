import PropTypes from "prop-types";
import { Box, Button, Chip, Stack, Typography } from "@mui/material";

import { displayText, questionOptions, readable } from "./harnessShared";

const ACTION_LABELS = {
  drop: "Drop scenario",
  specialize: "Specialize scenario",
  add: "Add scenario",
};

function ProposalPreview({ proposal }) {
  const preview = proposal?.preview || {};
  const persona = preview.persona;
  return (
    <Box
      sx={{
        mt: 1,
        p: 1.25,
        borderRadius: 1,
        border: 1,
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
        <Chip
          size="small"
          label={
            ACTION_LABELS[proposal?.action] ||
            readable(displayText(proposal?.action))
          }
          color={proposal?.action === "drop" ? "warning" : "info"}
          variant="outlined"
        />
        <Typography variant="subtitle2" noWrap>
          {displayText(preview.name || proposal?.scenario_key)}
        </Typography>
      </Stack>
      {proposal?.reason && (
        <Typography variant="body2" color="text.secondary">
          {displayText(proposal.reason)}
        </Typography>
      )}
      {preview.instruction && (
        <Typography variant="body2" sx={{ mt: 0.75 }}>
          <b>Instruction:</b> {displayText(preview.instruction)}
        </Typography>
      )}
      {persona && (
        <Typography variant="body2" sx={{ mt: 0.5 }}>
          <b>Persona:</b>{" "}
          {typeof persona === "object"
            ? displayText(persona.name || persona.description || persona)
            : displayText(persona)}
        </Typography>
      )}
    </Box>
  );
}

ProposalPreview.propTypes = { proposal: PropTypes.object };

// The answerable part of an assistant `question` or `confirmation` message. Answering sends
// the reply the harness waits on (environment-releases.md §7.5): a question takes a
// `user_response` with `{value}`, a proposal an `approval` with `{approved}`. Once answered,
// the buttons stay visible but disabled so the transcript keeps the choice in view.
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
  if (item.kind === "confirmation" && item.payload?.proposal) {
    const answer = (approved) =>
      onAnswer({
        content: approved ? "Approve" : "Reject",
        replyTo: item.message_id,
        kind: "approval",
        payload: { approved },
      });
    return (
      <>
        <ProposalPreview proposal={item.payload.proposal} />
        <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
          <Button
            size="small"
            variant="contained"
            disabled={disabled || answered}
            onClick={() => answer(true)}
          >
            Approve
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={disabled || answered}
            onClick={() => answer(false)}
          >
            Reject
          </Button>
        </Stack>
      </>
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
