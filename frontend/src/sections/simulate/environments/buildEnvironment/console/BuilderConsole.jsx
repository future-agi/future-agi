import PropTypes from "prop-types";
import { useEffect, useReducer, useRef } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, TextField, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";

import { BUILD_TONES } from "../buildTones";
import { CONSOLE_COPY } from "../build.constants";
import { Turn, Working } from "./ConsoleTurn";
import VoiceInput from "./VoiceInput";

const MONO = "ui-monospace, Menlo, monospace";

const INITIAL = { draft: "", attachments: [] };

function composerReducer(state, action) {
  switch (action.type) {
    case "draft":
      return { ...state, draft: action.value };
    case "attach":
      return { ...state, attachments: [...state.attachments, ...action.files] };
    case "remove":
      return { ...state, attachments: state.attachments.filter((_, i) => i !== action.index) };
    case "clear":
      return INITIAL;
    default:
      return state;
  }
}

/**
 * The console, as a chat.
 *
 * Read like a conversation rather than a log: a measured column, the user's own
 * turns in a bubble and the builder's in plain text, tool calls folded into
 * quiet rows. Steps stream in one at a time so you can watch the work and
 * interrupt it.
 *
 * Attachments are held in component state only and handed to `onSend(text,
 * attachments)` as-is — nothing here uploads them; the caller decides.
 * `preComposer` is kept for parity with the designer (an intake questionnaire
 * pins there); Phase-2 never passes it.
 */
export default function BuilderConsole({ turns, running, chips, onSend, onChip, preComposer }) {
  const [state, dispatch] = useReducer(composerReducer, INITIAL);
  const { draft, attachments } = state;
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, running]);

  const hasContent = draft.trim() || attachments.length > 0;

  const send = () => {
    const text = draft.trim();
    if ((!text && attachments.length === 0) || running) return;
    dispatch({ type: "clear" });
    onSend?.(text, attachments);
  };

  return (
    <Stack sx={{ height: "100%", minWidth: 0 }}>
      <Stack
        direction="row" alignItems="center" spacing={1.25}
        sx={{ flexShrink: 0, px: 2.5, py: 1.5, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Box
          sx={{
            width: 30, height: 30, borderRadius: 1, display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.16 : 0.1),
            color: BUILD_TONES.accent,
          }}
        >
          <Iconify icon="solar:chat-round-line-linear" width={15} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s3", color: "text.subtitle", lineHeight: 1.2 }}>
            {running ? CONSOLE_COPY.working : CONSOLE_COPY.idle}
          </Typography>
        </Box>
      </Stack>

      <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 3 }}>
        <Stack spacing={4}>
          {(turns || []).length === 0 && !running ? (
            <Stack alignItems="center" spacing={1.25} sx={{ py: 6, opacity: 0.7 }}>
              <Box
                sx={{
                  width: 36, height: 36, borderRadius: 999, display: "grid", placeItems: "center",
                  bgcolor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.16 : 0.1),
                  color: BUILD_TONES.accent,
                }}
              >
                <Iconify icon="solar:chat-round-line-linear" width={17} />
              </Box>
              <Typography sx={{ typography: "s2", color: "text.subtitle", textAlign: "center", maxWidth: 320 }}>
                {CONSOLE_COPY.empty}
              </Typography>
            </Stack>
          ) : (
            <>
              {(turns || []).map((turn) => <Turn key={turn.id} turn={turn} />)}
              {running && <Working label={CONSOLE_COPY.workingDot} />}
            </>
          )}
          <Box ref={endRef} />
        </Stack>
      </Box>

      {(chips || []).length > 0 && !running && (
        <Stack direction="row" spacing={1} sx={{ px: 2.5, pb: 1.5, flexWrap: "wrap", rowGap: 1 }}>
          {(chips || []).map((c) => (
            <Button
              key={c}
              size="small"
              onClick={() => onChip?.(c)}
              sx={{
                typography: "s2", fontWeight: "fontWeightMedium", px: 1.5, borderRadius: 5,
                color: "text.secondary", border: "1px solid", borderColor: "divider",
                "&:hover": { borderColor: "text.subtitle", bgcolor: "action.hover" },
              }}
            >
              {c}
            </Button>
          ))}
        </Stack>
      )}

      {/* Slot for anything a caller wants to pin above the composer (the intake
          questionnaire drops in here). Phase-2 never passes it. */}
      {preComposer && <Box sx={{ px: 2.5, pb: 1 }}>{preComposer}</Box>}

      <Box sx={{ px: 2.5, pb: 2.5, pt: 1 }}>
        <Box
          sx={{
            p: 1.5, borderRadius: 2, border: "1.5px solid",
            borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.14),
            bgcolor: "background.paper",
            boxShadow: (t) => (t.palette.mode === "dark"
              ? "0 4px 20px rgba(0,0,0,0.25)"
              : "0 2px 10px rgba(16,24,40,0.05)"),
            transition: "border-color 0.15s ease, box-shadow 0.15s ease",
            "&:focus-within": {
              borderColor: BUILD_TONES.accent,
              boxShadow: (t) => `0 0 0 3px ${alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.18 : 0.12)}`,
            },
          }}
        >
          {attachments.length > 0 && (
            <Stack direction="row" spacing={0.75} sx={{ flexWrap: "wrap", rowGap: 0.75, mb: 1 }}>
              {attachments.map((f, i) => (
                <Stack
                  key={`${f.name}-${i}`}
                  direction="row" alignItems="center" spacing={0.75}
                  sx={{
                    px: 1, py: 0.5, borderRadius: 1,
                    bgcolor: "background.neutral",
                    border: "1px solid", borderColor: "divider",
                    maxWidth: 260,
                  }}
                >
                  <Iconify icon="solar:paperclip-linear" width={12} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                  <Typography noWrap sx={{ typography: "s3", fontFamily: MONO, flex: 1, minWidth: 0 }}>
                    {f.name}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
                    {(f.size / 1024).toFixed(0)} kB
                  </Typography>
                  <IconButton
                    size="small"
                    aria-label={`Remove ${f.name}`}
                    onClick={() => dispatch({ type: "remove", index: i })}
                    sx={{ p: 0, ml: 0.25 }}
                  >
                    <Iconify icon="solar:close-circle-linear" width={13} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                </Stack>
              ))}
            </Stack>
          )}

          <Stack direction="row" alignItems="flex-end" spacing={1}>
            <TextField
              fullWidth
              multiline
              maxRows={8}
              variant="standard"
              placeholder={CONSOLE_COPY.placeholder}
              value={draft}
              disabled={running}
              onChange={(e) => dispatch({ type: "draft", value: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
              }}
              InputProps={{ disableUnderline: true, sx: { typography: "s2", lineHeight: 1.55, px: 1, py: 0.5 } }}
            />
            <IconButton
              component="label"
              aria-label="Attach a file"
              title={CONSOLE_COPY.attach}
              disabled={running}
              sx={{
                width: 34, height: 34, borderRadius: 1.25,
                color: "text.subtitle",
                "&:hover": { bgcolor: "action.hover", color: "text.primary" },
              }}
            >
              <Iconify icon="solar:paperclip-linear" width={17} />
              <input
                hidden multiple type="file"
                accept={CONSOLE_COPY.attachAccept}
                onChange={(e) => dispatch({ type: "attach", files: Array.from(e.target.files || []) })}
              />
            </IconButton>
            <VoiceInput onTranscript={(text) => dispatch({ type: "draft", value: text })} disabled={running} />
            <IconButton
              aria-label="Send"
              disabled={!hasContent || running}
              onClick={send}
              sx={{
                width: 34, height: 34, borderRadius: 1.25,
                bgcolor: hasContent && !running ? BUILD_TONES.accent : undefined,
                color: hasContent && !running ? "common.white" : undefined,
                "&:hover": { bgcolor: hasContent && !running ? BUILD_TONES.accentHover : undefined },
                "&.Mui-disabled": {
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
                  color: "text.disabled",
                },
              }}
            >
              <Iconify icon="solar:arrow-up-bold" width={17} />
            </IconButton>
          </Stack>
        </Box>
      </Box>
    </Stack>
  );
}

BuilderConsole.propTypes = {
  turns: PropTypes.array,
  running: PropTypes.bool,
  chips: PropTypes.array,
  onSend: PropTypes.func,
  onChip: PropTypes.func,
  preComposer: PropTypes.node,
};
