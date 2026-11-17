import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { GatewayStatusChip } from "../components/frame";
import {
  useCreateGuardrailFeedback,
  useMyGuardrailFeedback,
  useUpdateGuardrailFeedback,
} from "./hooks/useGuardrailFeedback";

const OPTIONS = [
  { value: "correct", label: "Correct", tone: "healthy" },
  { value: "false_positive", label: "False positive", tone: "warning" },
  { value: "false_negative", label: "False negative", tone: "error" },
  { value: "unsure", label: "Unsure", tone: "neutral" },
];

const optionFor = (value) => OPTIONS.find((option) => option.value === value);

/**
 * A reviewer's verdict on one guardrail match of one request. It loads the
 * review this reviewer saved for this request and check (only its author may
 * change a review, so another reviewer's answer is never shown as "Your
 * review"), and an edit changes that review (PATCH) rather than adding
 * another.
 */
const FeedbackWidget = ({ requestLogId, checkName }) => {
  const saved = useMyGuardrailFeedback(requestLogId, checkName);
  const existing = saved.answer || null;

  const create = useCreateGuardrailFeedback();
  const update = useUpdateGuardrailFeedback();
  const [editing, setEditing] = useState(false);
  const [verdict, setVerdict] = useState("");
  const [comment, setComment] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setEditing(false);
    setDirty(false);
    setVerdict("");
    setComment("");
  }, [requestLogId, checkName]);

  useEffect(() => {
    if (saved.isLoading || saved.isError || dirty) return;
    setVerdict(existing?.feedback || "");
    setComment(existing?.comment || "");
  }, [existing?.id, existing?.feedback, existing?.comment, saved.isLoading, saved.isError, dirty]);

  const busy = create.isPending || update.isPending;

  const submit = () => {
    if (saved.isLoading || saved.isError || busy || !verdict) return;
    const done = {
      onSuccess: () => {
        enqueueSnackbar(existing ? "Review updated" : "Review saved", {
          variant: "success",
        });
        setEditing(false);
        setDirty(false);
      },
      onError: () =>
        enqueueSnackbar("The review could not be saved", { variant: "error" }),
    };
    if (existing) {
      update.mutate({ id: existing.id, feedback: verdict, comment }, done);
    } else {
      create.mutate(
        {
          request_log: requestLogId,
          check_name: checkName,
          feedback: verdict,
          comment,
        },
        done,
      );
    }
  };

  if (saved.isLoading) {
    return (
      <Typography typography="s2" color="text.secondary">
        Loading review…
      </Typography>
    );
  }

  if (saved.isError) {
    return (
      <Alert severity="warning" action={
        <Button color="inherit" size="small" disabled={saved.isFetching} onClick={() => saved.refetch()}>
          Retry review
        </Button>
      }>
        Your review could not be loaded. Retry before saving to avoid replacing a saved answer.
      </Alert>
    );
  }

  if (existing && !editing) {
    const option = optionFor(existing.feedback);
    return (
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}
      >
        <Typography typography="s2" color="text.secondary">
          Your review
        </Typography>
        <GatewayStatusChip
          tone={option?.tone || "neutral"}
          label={option?.label || existing.feedback}
          showIcon={false}
        />
        <Button size="small" onClick={() => setEditing(true)}>
          Edit
        </Button>
      </Box>
    );
  }

  const open = editing || Boolean(verdict);
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          flexWrap: "wrap",
        }}
      >
        <Typography typography="s2" color="text.secondary">
          Was this match right?
        </Typography>
        {OPTIONS.map((option) => (
          <Chip
            key={option.value}
            size="small"
            label={option.label}
            component="button"
            variant={verdict === option.value ? "filled" : "outlined"}
            color={verdict === option.value ? "primary" : "default"}
            aria-pressed={verdict === option.value}
            onClick={() => { setDirty(true); setVerdict(option.value); }}
          />
        ))}
      </Box>
      {open && (
        <>
          <TextField
            size="small"
            label="Comment"
            placeholder="Optional: why it was right or wrong"
            value={comment}
            onChange={(event) => { setDirty(true); setComment(event.target.value); }}
            multiline
            minRows={2}
          />
          <Box sx={{ display: "flex", gap: 1 }}>
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!verdict || busy}
              onClick={submit}
            >
              Save review
            </Button>
            <Button
              size="small"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setDirty(false);
                setVerdict(existing?.feedback || "");
                setComment(existing?.comment || "");
              }}
            >
              Cancel
            </Button>
          </Box>
        </>
      )}
    </Box>
  );
};

FeedbackWidget.propTypes = {
  requestLogId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  checkName: PropTypes.string,
};

export default FeedbackWidget;
