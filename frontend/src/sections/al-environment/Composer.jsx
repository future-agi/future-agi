import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "./alkTokens";
import { quickChips } from "./quickChips";
import { shortPath } from "./parts/shortPath";

/**
 * The box the harness is talked to through. Send sits inside the border rather than beside
 * it, so the whole thing reads as one field; the chips above are the shortcuts for the
 * things you would otherwise type out every session.
 */
const Composer = ({
  onSay,
  onRun,
  onStop,
  streaming,
  status,
  sessionId,
  artifactsPath,
}) => {
  const [text, setText] = useState("");
  const boxRef = useRef(null);
  const hasSession = Boolean(status?.session);
  const busy = Boolean(status?.busy);
  const ready = text.trim().length > 0 && !streaming && hasSession && !busy;

  // "/" focuses the composer from anywhere on the page, which is what the placeholder
  // advertises. Ignored while already typing somewhere, or it would eat the character.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "/") return;
      const tag = document.activeElement?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      event.preventDefault();
      boxRef.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const send = () => {
    if (!ready) return;
    onSay(text.trim());
    setText("");
  };

  const chips = hasSession ? quickChips(status) : [];

  return (
    <Box
      sx={{
        px: 3,
        pt: 1.5,
        pb: 1.75,
        position: "relative",
        // Matches the toolbar rule: inset to the content, not run to the pane edge.
        "&::before": {
          content: '""',
          position: "absolute",
          left: 24,
          right: 24,
          top: 0,
          borderTop: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      {chips.length > 0 && (
        <Stack
          direction="row"
          spacing={0.5}
          flexWrap="wrap"
          useFlexGap
          sx={{ mb: 1 }}
        >
          {chips.map((chip) => (
            <Box
              key={chip.label}
              component="button"
              type="button"
              disabled={streaming || busy}
              onClick={() =>
                chip.run !== undefined ? onRun(chip.run) : onSay(chip.say)
              }
              sx={{
                px: 1.25,
                py: 0.4,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 20,
                background: "none",
                color: "text.secondary",
                fontSize: 12,
                fontFamily: "inherit",
                cursor: "pointer",
                "&:hover:not(:disabled)": {
                  color: "text.primary",
                  borderColor: "text.secondary",
                },
                "&:disabled": { opacity: 0.5, cursor: "default" },
              }}
            >
              {chip.label}
            </Box>
          ))}
        </Stack>
      )}

      <Box
        sx={{
          display: "flex",
          alignItems: "flex-end",
          gap: 1,
          px: 1,
          py: 0.75,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1.25,
          bgcolor: "background.default",
          "&:focus-within": { borderColor: "text.secondary" },
        }}
      >
        <Box
          component="textarea"
          ref={boxRef}
          rows={2}
          value={text}
          placeholder="Tell it what you want…  ( / to focus )"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send();
            }
          }}
          sx={{
            flexGrow: 1,
            minWidth: 0,
            border: 0,
            outline: "none",
            resize: "none",
            background: "transparent",
            color: "text.primary",
            fontFamily: (theme) => theme.typography.fontFamily,
            fontSize: 14.5,
            lineHeight: 1.45,
            maxHeight: "9em",
          }}
        />
        {streaming && (
          <Button
            size="small"
            variant="outlined"
            color="error"
            onClick={onStop}
          >
            Stop
          </Button>
        )}
        <Button
          size="small"
          variant="contained"
          disabled={!ready}
          onClick={send}
        >
          {streaming ? "…" : "Send"}
        </Button>
      </Box>

      <Typography
        sx={{
          mt: 0.6,
          fontFamily: ALK_MONO,
          fontSize: 11,
          color: "text.secondary",
          overflowWrap: "anywhere",
        }}
      >
        {hasSession && sessionId
          ? `${sessionId}  ·  ${shortPath(artifactsPath)}`
          : "no session"}
      </Typography>
    </Box>
  );
};

Composer.propTypes = {
  onSay: PropTypes.func.isRequired,
  onRun: PropTypes.func.isRequired,
  onStop: PropTypes.func.isRequired,
  streaming: PropTypes.bool,
  status: PropTypes.object,
  sessionId: PropTypes.string,
  artifactsPath: PropTypes.string,
};

export default Composer;
