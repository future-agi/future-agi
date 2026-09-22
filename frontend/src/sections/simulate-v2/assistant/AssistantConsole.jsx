import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, TextField, IconButton, Collapse, Menu, MenuItem, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import VoiceInput from "./VoiceInput";
import AskUserQuestionCard from "./AskUserQuestionCard";
import { BUILDER_MODES, getBuilderMode, subscribeBuilderMode, setBuilderMode } from "../_mock/builderModeBus";
import { subscribeComposerScaffold } from "../_mock/composerScaffoldBus";

/**
 * The console, as a chat.
 *
 * Read like a conversation rather than a log: a measured column instead of the
 * full pane width, prose at a comfortable size and line height, the user's own
 * turns in a bubble and the builder's in plain text, and tool calls folded into
 * quiet rows you can open rather than a wall of green ticks.
 *
 * Steps still stream in one at a time — the point of the screen is that you can
 * watch the work and interrupt it, which a finished wall of text does not
 * convey.
 */

export default function StudioConsole({ turns, running, chips, onSend, onChip, preComposer, frozen = false, frozenReason }) {
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState([]);
  /*
    Scaffolds — suggestion chips pinned above the text field.
    Populated by callers that publish through composerScaffoldBus
    (e.g. the scenarios SelectionBar). Each chip's text gets
    prepended to the message on send; × removes just that chip.
  */
  const [scaffolds, setScaffolds] = useState([]);
  const endRef = useRef(null);

  useEffect(
    () => subscribeComposerScaffold((s) => {
      setScaffolds((prev) => (prev.some((x) => x.label === s.label) ? prev : [...prev, s]));
    }),
    [],
  );

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, running]);

  /* Frozen === env is not Live yet (still building / re-deriving), so
     the builder can't accept edits. Blocks input the same way `running`
     does, but stays blocked until the env becomes Live rather than
     until the last message finishes. */
  const blocked = running || frozen;

  const send = () => {
    const text = draft.trim();
    const scaffoldText = scaffolds.map((s) => s.prompt).join(". ");
    const combined = [scaffoldText, text].filter(Boolean).join(scaffoldText && text ? ". " : "");
    if ((!combined && attachments.length === 0) || blocked) return;
    setDraft("");
    setAttachments([]);
    setScaffolds([]);
    onSend(combined, attachments);
  };
  const removeScaffold = (i) =>
    setScaffolds((prev) => prev.filter((_, idx) => idx !== i));

  const removeAttachment = (i) =>
    setAttachments((prev) => prev.filter((_, idx) => idx !== i));

  return (
    <Stack sx={{ height: "100%", minWidth: 0 }}>
      {/*
        Header, because the pane sat unlabelled and read as a mystery panel —
        someone had to click the input to work out it was chat. A small header
        with a live status dot (amber while the builder is working, green when
        it's your turn) is the shape every chat UI settled on for the same
        reason: a conversation surface has to announce itself.
      */}
      <Stack
        direction="row" alignItems="center" spacing={1.25}
        sx={{
          flexShrink: 0, px: 2.5, py: 1.5,
          borderBottom: "1px solid", borderColor: "divider",
        }}
      >
        <Box
          sx={{
            width: 30, height: 30, borderRadius: 1, display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1),
            color: "#7857FC",
          }}
        >
          <Iconify icon="solar:chat-round-line-linear" width={15} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s3", color: "text.subtitle", lineHeight: 1.2 }}>
            {frozen
              ? (frozenReason || "Environment is not live yet")
              : running
                ? "Working on your last message…"
                : "Ask, correct, or steer what it builds"}
          </Typography>
        </Box>
        {/*
          Status dot removed — the "Working on your last message…"
          swap in the header already conveys the busy state, and the
          idle green dot read as decorative chrome next to it.
        */}
      </Stack>

      <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 3 }}>
        <Stack spacing={4}>
          {turns.length === 0 && !running ? (
            /* Calm empty state — a soft centered line so the pane
               doesn't look broken while the caller collects intake
               below. Replaces the earlier "greeting turn" which read
               as two empty tick pills. */
            <Stack alignItems="center" spacing={1.25} sx={{ py: 6, opacity: 0.7 }}>
              <Box
                sx={{
                  width: 36, height: 36, borderRadius: 999, display: "grid", placeItems: "center",
                  bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1),
                  color: "#7857FC",
                }}
              >
                <Iconify icon="solar:chat-round-line-linear" width={17} />
              </Box>
              <Typography sx={{ typography: "s2", color: "text.subtitle", textAlign: "center", maxWidth: 320 }}>
                Answer the questions below to kick things off. You can also just start typing.
              </Typography>
            </Stack>
          ) : (
            <>
              {turns.map((turn) => <Turn key={turn.id} turn={turn} />)}
              {running && <Working />}
            </>
          )}
          <Box ref={endRef} />
        </Stack>
      </Box>

      {chips.length > 0 && !blocked && (
        <Stack direction="row" spacing={1} sx={{ px: 2.5, pb: 1.5, flexWrap: "wrap", rowGap: 1 }}>
          {chips.map((c) => (
            <Button
              key={c}
              size="small"
              onClick={() => onChip(c)}
              sx={{
                typography: "s2", fontWeight: 500, px: 1.5, borderRadius: 5,
                color: "text.secondary", border: "1px solid", borderColor: "divider",
                "&:hover": { borderColor: "text.subtitle", bgcolor: "action.hover" },
              }}
            >
              {c}
            </Button>
          ))}
        </Stack>
      )}

      {/*
        Slot right above the composer for anything a caller wants to pin
        there — the intake questionnaire drops in here so the question
        card sits directly on top of the input the same way Claude's
        own AskUserQuestion does.
      */}
      {preComposer && (
        <Box sx={{ px: 2.5, pb: 1 }}>
          {preComposer}
        </Box>
      )}

      {/*
        The composer, given the visual weight the input in Claude, ChatGPT or
        Lovable has: an accent-coloured focus ring, a subtle shadow, and a
        larger send button — this is where every interaction starts, so making
        it read as background chrome was the wrong signal entirely.
      */}
      <Box sx={{ px: 2.5, pb: 2.5, pt: 1 }}>
        <Box
          sx={{
            p: 1.25, borderRadius: 2, border: "1px solid",
            borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.14 : 0.12),
            bgcolor: "background.paper",
            transition: "border-color 0.15s ease",
            "&:focus-within": {
              borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.32 : 0.28),
            },
          }}
        >
          {/*
            Scaffold chips — pinned suggestions injected from elsewhere
            (e.g. the scenario SelectionBar). Each chip becomes part
            of the outgoing message on send; × removes it in place,
            same shape Falcon's ChatInput uses for detected skills.
          */}
          {scaffolds.length > 0 && (
            <Stack direction="row" spacing={0.75} sx={{ flexWrap: "wrap", rowGap: 0.75, mb: 1 }}>
              {scaffolds.map((s, i) => (
                <Stack
                  key={`${s.label}-${i}`}
                  direction="row" alignItems="center" spacing={0.5}
                  sx={{
                    pl: 0.875, pr: 0.375, py: 0.375, borderRadius: 999,
                    bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.14 : 0.08),
                    border: "1px solid", borderColor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.35 : 0.24),
                    maxWidth: "100%",
                  }}
                >
                  <Iconify icon={s.icon || "solar:magic-stick-3-linear"} width={12} sx={{ color: "#7857FC", flexShrink: 0 }} />
                  <Typography
                    sx={{
                      typography: "s3", fontWeight: 600, color: "#7857FC",
                      maxWidth: 260, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                    }}
                  >
                    {s.label}
                  </Typography>
                  <IconButton size="small" onClick={() => removeScaffold(i)} sx={{ p: 0, ml: 0.25 }} aria-label={`Remove ${s.label}`}>
                    <Iconify icon="solar:close-circle-linear" width={13} sx={{ color: (t) => alpha("#7857FC", 0.6) }} />
                  </IconButton>
                </Stack>
              ))}
            </Stack>
          )}

          {/*
            Attached-file chips row — shown inside the composer above
            the text field so the user sees what's about to be sent.
          */}
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
                  <Typography noWrap sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", flex: 1, minWidth: 0 }}>
                    {f.name}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
                    {(f.size / 1024).toFixed(0)} kB
                  </Typography>
                  <IconButton size="small" onClick={() => removeAttachment(i)} sx={{ p: 0, ml: 0.25 }}>
                    <Iconify icon="solar:close-circle-linear" width={13} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                </Stack>
              ))}
            </Stack>
          )}

          {/* Row 1: the text field on its own so long placeholders / long
              drafts get the full width. The Claude / ChatGPT / Lovable
              composer shape. */}
          <TextField
            fullWidth
            multiline
            maxRows={8}
            variant="standard"
            placeholder={frozen ? (frozenReason || "Environment is not live yet") : "Reply to the builder…"}
            value={draft}
            disabled={blocked}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
            }}
            InputProps={{ disableUnderline: true, sx: { typography: "s2", lineHeight: 1.55, px: 0.75, py: 0.5 } }}
          />

          {/* Row 2: toolbar — utility icons on the left, mode picker in
              the middle, send button on the right. */}
          <Stack direction="row" alignItems="center" spacing={0.25} sx={{ mt: 0.5, pl: 0.25 }}>
            <IconButton
              component="label"
              disabled={blocked}
              title="Attach a dataset, CSV or file"
              sx={{
                width: 30, height: 30, borderRadius: 1,
                color: "text.subtitle",
                "&:hover": { bgcolor: "action.hover", color: "text.primary" },
              }}
            >
              <Iconify icon="solar:paperclip-linear" width={15} />
              <input
                hidden multiple type="file"
                accept=".csv,.tsv,.json,.jsonl,.xlsx,.txt,.md,.pdf"
                onChange={(e) => setAttachments((prev) => [...prev, ...Array.from(e.target.files || [])])}
              />
            </IconButton>
            <VoiceInput onTranscript={setDraft} disabled={blocked} />
            <ModePicker disabled={blocked} />

            <Box flex={1} />

            <IconButton
              disabled={(!draft.trim() && attachments.length === 0 && scaffolds.length === 0) || blocked}
              onClick={send}
              sx={{
                width: 30, height: 30, borderRadius: 1,
                bgcolor: (draft.trim() || attachments.length > 0 || scaffolds.length > 0) && !blocked ? "#7857FC" : undefined,
                color: (draft.trim() || attachments.length > 0 || scaffolds.length > 0) && !blocked ? "#fff" : undefined,
                "&:hover": { bgcolor: (draft.trim() || attachments.length > 0 || scaffolds.length > 0) && !blocked ? "#6B4EE6" : undefined },
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

StudioConsole.propTypes = {
  turns: PropTypes.array, running: PropTypes.bool,
  chips: PropTypes.array, onSend: PropTypes.func, onChip: PropTypes.func,
  preComposer: PropTypes.node,
  frozen: PropTypes.bool, frozenReason: PropTypes.string,
};

/**
 * Mode picker — Auto vs Guided.
 *
 * Compact icon+label chip in the composer row that opens a two-item
 * menu. Shape matches Claude Code's own mode selector: current mode
 * name + caret, click surfaces a list of modes each with a hint line.
 * Selection stores in a module-level bus so the derivation stream on
 * the other side of the tree can read it without prop-threading.
 */
function ModePicker({ disabled }) {
  const [mode, setMode] = useState(() => getBuilderMode());
  const [anchor, setAnchor] = useState(null);
  useEffect(() => subscribeBuilderMode(setMode), []);
  const current = BUILDER_MODES.find((m) => m.id === mode) || BUILDER_MODES[0];

  return (
    <>
      <Tooltip arrow title="Builder mode — Auto or Guided">
        <Button
          size="small"
          disabled={disabled}
          onClick={(e) => setAnchor(e.currentTarget)}
          startIcon={<Iconify icon={current.icon} width={12} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={10} />}
          sx={{
            height: 30, borderRadius: 1, px: 0.75, minWidth: 0,
            typography: "s3", fontWeight: 600,
            color: "text.subtitle",
            "& .MuiButton-startIcon": { mr: 0.5 },
            "& .MuiButton-endIcon": { ml: 0.25 },
            "&:hover": { bgcolor: "action.hover", color: "text.primary" },
          }}
        >
          {current.label}
        </Button>
      </Tooltip>
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
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>{m.label}</Typography>
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

/* ── one turn ────────────────────────────────────────────────────────────── */

function Turn({ turn }) {
  if (turn.role === "user") {
    return (
      <Stack alignItems="flex-end">
        <Box
          sx={{
            maxWidth: "82%", px: 1.75, py: 1.125, borderRadius: 2,
            bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.14 : 0.08),
            border: "1px solid",
            borderColor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.22 : 0.16),
          }}
        >
          <Typography sx={{ typography: "s2", lineHeight: 1.55, color: "text.primary" }}>{turn.text}</Typography>
        </Box>
      </Stack>
    );
  }

  return (
    <Stack spacing={1.5}>
      {turn.title && (
        /*
          Section header — read like a document heading, not a chip
          label. Uppercase + tracked so it clearly marks a new phase in
          the builder's narrative, and doesn't visually collide with
          the tool-call rows underneath.
        */
        <Typography
          sx={{
            typography: "s3", fontWeight: 700,
            color: "text.primary", textTransform: "uppercase", letterSpacing: 0.6,
            pt: 0.5,
          }}
        >
          {turn.title}
        </Typography>
      )}
      {turn.steps.map((step, i) => <Step key={i} step={step} />)}
    </Stack>
  );
}
Turn.propTypes = { turn: PropTypes.object };

/* ── one step ────────────────────────────────────────────────────────────── */

/**
 * Steps used to render as uniform green-tinted chips with tick icons —
 * every event looked identical and the eye had nothing to land on. The
 * new treatment is quieter: prose reads like a document, tool calls
 * and file writes render as a *line* (a small colored dot + mono label
 * + muted result), and expandable payloads stay expandable but drop
 * their heavy background fill.
 */
function Step({ step }) {
  const [open, setOpen] = useState(false);

  if (step.kind === "think" || step.kind === "note") {
    return (
      <Typography
        sx={{
          typography: "s2", lineHeight: 1.65,
          color: step.kind === "think" ? "text.secondary" : "text.primary",
        }}
      >
        {step.text}
      </Typography>
    );
  }

  if (step.kind === "ask") {
    return (
      <AskUserQuestionCard
        question={step.question}
        onSubmit={step.onSubmit}
        onSkip={step.onSkip}
      />
    );
  }

  if (step.kind === "file") {
    return (
      <QuietRow tint="#16A34A">
        <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", flexShrink: 0, color: "text.primary" }}>
          {step.path}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
          {step.note}
        </Typography>
      </QuietRow>
    );
  }

  if (step.kind === "json") {
    return (
      <Box>
        <Stack
          direction="row" alignItems="center" spacing={1}
          onClick={() => setOpen((o) => !o)}
          sx={{
            py: 0.5, cursor: "pointer", borderRadius: 1,
            "&:hover": { bgcolor: "action.hover" },
            px: 0.75,
          }}
        >
          <Iconify
            icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
            width={12}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary" }}>
            {step.label}
          </Typography>
        </Stack>
        <Collapse in={open} unmountOnExit>
          <Typography
            sx={{
              ml: 2.5, mt: 0.5, px: 1.25, py: 1, borderRadius: 1,
              typography: "s3", color: "text.secondary", whiteSpace: "pre-wrap",
              fontFamily: "ui-monospace, Menlo, monospace",
              bgcolor: "background.neutral",
            }}
          >
            {step.value}
          </Typography>
        </Collapse>
      </Box>
    );
  }

  // tool call
  return (
    <QuietRow tint="#16A34A">
      <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", flexShrink: 0, color: "text.primary" }}>
        {step.label}
      </Typography>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
        {step.result}
      </Typography>
    </QuietRow>
  );
}
Step.propTypes = { step: PropTypes.object };

/**
 * The step-row primitive: a colored dot on the left, whatever the
 * caller passes on the right. No background fill, no border — just
 * the dot and the content. Repeated ten times in a row it reads as a
 * list of results, not ten stacked chips.
 */
function QuietRow({ tint, children }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.25}
      sx={{ px: 0.5, py: 0.25 }}
    >
      <Box
        sx={{
          width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
          bgcolor: tint,
          boxShadow: (t) => `0 0 0 3px ${alpha(tint, t.palette.mode === "dark" ? 0.15 : 0.12)}`,
        }}
      />
      {children}
    </Stack>
  );
}
QuietRow.propTypes = { tint: PropTypes.string, children: PropTypes.node };

function Working() {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Box
        sx={{
          width: 7, height: 7, borderRadius: "50%", bgcolor: "text.subtitle",
          animation: "pulse 1.2s ease-in-out infinite",
          "@keyframes pulse": { "0%,100%": { opacity: 0.3 }, "50%": { opacity: 1 } },
        }}
      />
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>Working…</Typography>
    </Stack>
  );
}
