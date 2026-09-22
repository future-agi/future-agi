import PropTypes from "prop-types";
import { useEffect, useReducer, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, TextField, IconButton, Menu, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

import { BUILD_TONES } from "../buildTones";
import { CONSOLE_COPY } from "../build.constants";
import { Turn, Working } from "./ConsoleTurn";
import VoiceInput from "./VoiceInput";
import { BUILDER_MODES, getBuilderMode, subscribeBuilderMode, setBuilderMode } from "./builderModeBus";
import { subscribeComposerScaffold } from "./composerScaffoldBus";

const INITIAL = { draft: "", scaffolds: [] };

function composerReducer(state, action) {
  switch (action.type) {
    case "draft":
      return { ...state, draft: action.value };
    case "scaffold":
      return state.scaffolds.includes(action.text)
        ? state
        : { ...state, scaffolds: [...state.scaffolds, action.text] };
    case "unscaffold":
      return { ...state, scaffolds: state.scaffolds.filter((_, i) => i !== action.index) };
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
 * `preComposer` is kept for parity with the designer (an intake questionnaire
 * or a selection-context chip pins there).
 *
 * `frozen` locks the composer until the environment is Live: it blocks input
 * the same way `running` does, but stays blocked until the env finishes
 * building rather than until the last message returns.
 */
export default function BuilderConsole({
  turns,
  running,
  chips,
  onSend,
  onChip,
  onStop,
  canStop = false,
  preComposer,
  frozen = false,
  frozenReason,
}) {
  const [state, dispatch] = useReducer(composerReducer, INITIAL);
  const { draft, scaffolds } = state;
  const endRef = useRef(null);

  useEffect(
    () => subscribeComposerScaffold((text) => dispatch({ type: "scaffold", text })),
    [],
  );

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, running]);

  // Frozen === the env is not Live yet, so the builder can't accept edits. It
  // blocks the composer exactly like `running`, but persists across turns.
  const blocked = running || frozen;
  const reason = frozenReason || CONSOLE_COPY.frozen;

  const hasContent = draft.trim() || scaffolds.length > 0;

  const send = () => {
    const text = draft.trim();
    const scaffoldText = scaffolds.join(". ");
    const combined = [scaffoldText, text].filter(Boolean).join(scaffoldText && text ? ". " : "");
    if (!combined || blocked) return;
    dispatch({ type: "clear" });
    onSend?.(combined);
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
            {frozen ? reason : running ? CONSOLE_COPY.working : CONSOLE_COPY.idle}
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

      {(chips || []).length > 0 && !blocked && (
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
          questionnaire or the selection-context chip drops in here). */}
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
          {scaffolds.length > 0 && (
            <Stack direction="row" spacing={0.75} sx={{ flexWrap: "wrap", rowGap: 0.75, mb: 1 }}>
              {scaffolds.map((s, i) => (
                <Stack
                  key={`${s}-${i}`}
                  direction="row" alignItems="center" spacing={0.5}
                  sx={{
                    pl: 1, pr: 0.5, py: 0.375, borderRadius: 999,
                    bgcolor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.14 : 0.08),
                    border: "1px solid",
                    borderColor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.35 : 0.24),
                    maxWidth: "100%",
                  }}
                >
                  <Iconify icon="solar:magic-stick-3-linear" width={12} sx={{ color: BUILD_TONES.accent, flexShrink: 0 }} />
                  <Typography
                    sx={{
                      typography: "s3", fontWeight: "fontWeightSemiBold", color: BUILD_TONES.accent,
                      maxWidth: 260, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                    }}
                  >
                    {s}
                  </Typography>
                  <IconButton
                    size="small"
                    aria-label={`Remove ${s}`}
                    onClick={() => dispatch({ type: "unscaffold", index: i })}
                    sx={{ p: 0, ml: 0.25 }}
                  >
                    <Iconify icon="solar:close-circle-linear" width={13} sx={{ color: alpha(BUILD_TONES.accent, 0.6) }} />
                  </IconButton>
                </Stack>
              ))}
            </Stack>
          )}

          {/* Row 1: the text field on its own line so long drafts get the full width. */}
          <TextField
            fullWidth
            multiline
            maxRows={8}
            variant="standard"
            placeholder={frozen ? reason : CONSOLE_COPY.placeholder}
            value={draft}
            disabled={blocked}
            onChange={(e) => dispatch({ type: "draft", value: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
            }}
            InputProps={{ disableUnderline: true, sx: { typography: "s2", lineHeight: 1.55, px: 0.75, py: 0.5 } }}
          />

          {/* Row 2: toolbar — voice · mode picker · flex-spacer · send. */}
          <Stack direction="row" alignItems="center" spacing={0.25} sx={{ mt: 0.5, pl: 0.25 }}>
            <VoiceInput onTranscript={(text) => dispatch({ type: "draft", value: text })} disabled={blocked} />
            <ModePicker disabled={blocked} />

            <Box flex={1} />

            {onStop && canStop && !frozen && (
              <IconButton
                aria-label="Stop"
                title={CONSOLE_COPY.stop}
                disabled={running}
                onClick={onStop}
                sx={{
                  width: 30, height: 30, borderRadius: 1, mr: 0.5,
                  color: "text.subtitle",
                  border: "1px solid", borderColor: "divider",
                  "&:hover": { bgcolor: "action.hover", color: "text.primary" },
                }}
              >
                <Iconify icon="solar:stop-bold" width={13} />
              </IconButton>
            )}

            <IconButton
              aria-label="Send"
              disabled={!hasContent || blocked}
              onClick={send}
              sx={{
                width: 30, height: 30, borderRadius: 1,
                bgcolor: hasContent && !blocked ? BUILD_TONES.accent : undefined,
                color: hasContent && !blocked ? "common.white" : undefined,
                "&:hover": { bgcolor: hasContent && !blocked ? BUILD_TONES.accentHover : undefined },
                "&.Mui-disabled": {
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
                  color: "text.disabled",
                },
              }}
            >
              <Iconify icon="solar:arrow-up-bold" width={15} />
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
  onStop: PropTypes.func,
  canStop: PropTypes.bool,
  preComposer: PropTypes.node,
  frozen: PropTypes.bool,
  frozenReason: PropTypes.string,
};

/**
 * Mode picker — Auto vs Manual.
 *
 * A compact icon+label chip in the composer toolbar that opens a two-item menu.
 * Selection stores in a module-level bus so the derivation stream on the other
 * side of the tree can read it without prop-threading; the bus fires on
 * subscribe, so the local mirror stays in sync.
 */
function ModePicker({ disabled }) {
  const [mode, setMode] = useState(getBuilderMode);
  const [anchor, setAnchor] = useState(null);
  useEffect(() => subscribeBuilderMode(setMode), []);
  const current = BUILDER_MODES.find((m) => m.id === mode) || BUILDER_MODES[0];

  return (
    <>
      <CustomTooltip show={!disabled} title={CONSOLE_COPY.mode} size="small" arrow>
        <Button
          size="small"
          disabled={disabled}
          onClick={(e) => setAnchor(e.currentTarget)}
          startIcon={<Iconify icon={current.icon} width={12} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={10} />}
          sx={{
            height: 30, borderRadius: 1, px: 0.75, minWidth: 0,
            typography: "s3", fontWeight: "fontWeightSemiBold",
            color: "text.subtitle",
            "& .MuiButton-startIcon": { mr: 0.5 },
            "& .MuiButton-endIcon": { ml: 0.25 },
            "&:hover": { bgcolor: "action.hover", color: "text.primary" },
          }}
        >
          {current.label}
        </Button>
      </CustomTooltip>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "bottom", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 280, mb: 0.5 } } }}
      >
        {BUILDER_MODES.map((m) => {
          const active = m.id === mode;
          return (
            <MenuItem
              key={m.id}
              onClick={() => { setBuilderMode(m.id); setAnchor(null); }}
              sx={{ alignItems: "flex-start", gap: 1.25, py: 1 }}
            >
              <Iconify
                icon={active ? "solar:check-circle-bold" : m.icon}
                width={16}
                sx={{ color: active ? "primary.main" : "text.subtitle", mt: "2px", flexShrink: 0 }}
              />
              <Box minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{m.label}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "normal" }}>
                  {m.hint}
                </Typography>
              </Box>
            </MenuItem>
          );
        })}
      </Menu>
    </>
  );
}
ModePicker.propTypes = { disabled: PropTypes.bool };
