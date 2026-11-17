import React from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import { keyframes } from "@mui/system";
import Iconify from "src/components/iconify";

const pulse = keyframes`
  0% { box-shadow: 0 0 0 0 rgba(255, 171, 0, 0.55); }
  100% { box-shadow: 0 0 0 8px rgba(255, 171, 0, 0); }
`;

/**
 * IT-R1 G5 P1c: the selected rows only exist as pending changes. One
 * sentence (what blocks the run) + one primary button (the obvious next
 * step). `nudge` bumps when the user tried to move on; the banner pulses.
 */
const UnsavedRowsBanner = ({
  sentence,
  saveLabel,
  onSave,
  nudge = 0,
  note,
  onEditNote,
  saving = false,
}) => {
  if (!sentence) return null;
  return (
    <Stack
      key={nudge}
      direction="row"
      alignItems="center"
      gap={1}
      role="alert"
      data-testid="pending-rows"
      sx={{
        px: 1.25,
        py: 0.75,
        borderRadius: 0.5,
        border: "1px solid",
        borderColor: "rgba(255, 171, 0, 0.48)",
        bgcolor: "rgba(255, 171, 0, 0.12)",
        ...(nudge ? { animation: `${pulse} 0.6s ease-out 2` } : null),
      }}
    >
      <Iconify
        icon="mdi:alert-outline"
        width={16}
        sx={{ color: "warning.dark", flexShrink: 0 }}
      />
      <Stack sx={{ flex: 1, minWidth: 0 }}>
        <Typography typography="s2">{sentence}</Typography>
        {note && onEditNote ? (
          // IT-R2 G5 P2: the save is direct; the note it gets is shown and
          // `Edit note` opens the Save version dialog instead.
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="save-note"
          >
            {`Version note: ${note} · `}
            <Button
              size="small"
              variant="text"
              onClick={onEditNote}
              sx={{
                p: 0,
                minWidth: 0,
                typography: "s3",
                verticalAlign: "baseline",
              }}
            >
              Edit note
            </Button>
          </Typography>
        ) : null}
      </Stack>
      <Button
        size="small"
        variant="contained"
        color="primary"
        onClick={onSave}
        disabled={saving}
        sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
        data-testid="save-and-continue"
      >
        {saving ? "Saving…" : saveLabel}
      </Button>
    </Stack>
  );
};

UnsavedRowsBanner.propTypes = {
  sentence: PropTypes.string,
  saveLabel: PropTypes.string.isRequired,
  onSave: PropTypes.func.isRequired,
  nudge: PropTypes.number,
  note: PropTypes.string,
  onEditNote: PropTypes.func,
  saving: PropTypes.bool,
};

export default UnsavedRowsBanner;
