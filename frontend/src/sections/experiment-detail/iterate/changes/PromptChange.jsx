import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import { ChangeDiff, PromptEditor } from "./changeParts";

/**
 * Prompt tab (prompt runs): every message editable, each with a live word
 * diff vs the parent version. v2.1 g4: the run uses the edited messages as
 * an unsaved edit of that version (no prompt version is saved here).
 */
const PromptChange = ({ dock }) => {
  const { parent, draft, patch, canEdit } = dock;
  const prompt = parent.config?.prompt ?? {};
  const messages = prompt.messages ?? [];
  if (!messages.length)
    return (
      <Typography typography="s2" color="text.secondary">
        This run has no prompt messages to edit.
      </Typography>
    );
  const values = draft.messages ?? messages.map((m) => m.content ?? "");
  const setAt = (i, text) => {
    const next = [...values];
    next[i] = text;
    patch({ messages: next });
  };
  return (
    <Stack spacing={1}>
      {messages.map((m, i) => {
        const from = m.content ?? "";
        return (
          <Stack key={`${m.role}-${i}`} spacing={0.5}>
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ textTransform: "capitalize" }}
            >
              {m.role}
            </Typography>
            <PromptEditor
              label={`${m.role} message`}
              rows={i === 0 ? 6 : 3}
              value={values[i]}
              readOnly={!canEdit}
              onChange={(t) => setAt(i, t)}
            />
            <ChangeDiff
              from={from}
              to={values[i]}
              caption={`vs ${prompt.version ?? "parent"}`}
            />
          </Stack>
        );
      })}
    </Stack>
  );
};

PromptChange.propTypes = { dock: PropTypes.object.isRequired };

export default PromptChange;
