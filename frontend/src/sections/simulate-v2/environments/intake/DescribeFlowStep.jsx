import PropTypes from "prop-types";
import { useCallback, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, IconButton, Button, TextField, Tooltip,
  InputBase, CircularProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { formatFileSize } from "src/utils/utils";
import VoiceInput from "../../assistant/VoiceInput";

/*
  Merged describe-your-flow step for the scratch intake. One card,
  same outer shell as AskUserQuestionCard. The composer takes prose
  (typed or voice), attached files (paperclip / drag-and-drop), and
  now Falcon-AI-generated drafts (Write with Falcon AI button — same
  pattern the eval-instruction editor uses).

  Falcon flow (mirrors src/sections/evals/components/InstructionEditor.jsx):
    - Click the Falcon icon → inline AI bar appears above the textarea
    - Type a short prompt ("agent that triages GitHub issues") → Enter
    - Bar shows "Generating…" while the draft is composed
    - Result replaces the textarea content; original is stashed
    - Reject restores the original; Accept keeps the draft and closes
    - Escape / close button behaves like Reject

  No backend for the scratch flow yet — the draft is mocked from the
  user's prompt so the CEO demo shows the end-to-end shape. Swap
  fakeFalconWriter() for the real endpoint when it's ready.
*/
export default function DescribeFlowStep({
  step, total, prompt, description, placeholder, chips,
  value, onChange,
  files, onAdd, onRemove,
  onBack, onSkip, onNext, canSubmit, submitLabel,
  accept,
}) {
  const inputRef = useRef(null);
  const followUpRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  /* ── Falcon AI state ─────────────────────────────────────────────── */
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [hasResult, setHasResult] = useState(false);
  const [originalValue, setOriginalValue] = useState(null);

  const submitAI = useCallback(async (instruction) => {
    const trimmed = (instruction || "").trim();
    if (!trimmed) return;
    setAiLoading(true);
    if (originalValue === null) setOriginalValue(value || "");
    try {
      const draft = await fakeFalconWriter(trimmed, value);
      if (draft) {
        onChange(draft);
        setHasResult(true);
        setAiPrompt(trimmed);
        setTimeout(() => followUpRef.current?.focus(), 60);
      }
    } finally {
      setAiLoading(false);
    }
  }, [originalValue, value, onChange]);

  const handleAccept = () => {
    setAiOpen(false);
    setHasResult(false);
    setOriginalValue(null);
    setAiPrompt("");
  };

  const handleReject = () => {
    if (originalValue !== null) onChange(originalValue);
    setHasResult(false);
    setOriginalValue(null);
    setAiPrompt("");
  };

  const handleClose = () => {
    if (hasResult && originalValue !== null) onChange(originalValue);
    setAiOpen(false);
    setHasResult(false);
    setOriginalValue(null);
    setAiPrompt("");
  };

  /* ── attachments ────────────────────────────────────────────────── */
  const onFiles = (list) => {
    if (!list || !list.length) return;
    const next = Array.from(list).map((f) => ({
      id: `${f.name}-${f.size}-${f.lastModified}`,
      name: f.name, size: f.size, type: f.type,
    }));
    onAdd(next);
  };

  return (
    <Box
      sx={{
        borderRadius: 2, border: "1px solid",
        borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.08),
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.75, py: 1.25 }}>
        <Box
          sx={{
            px: 0.75, py: 0.125, borderRadius: 999, flexShrink: 0,
            bgcolor: (t) => alpha("#B45309", t.palette.mode === "dark" ? 0.28 : 0.16),
            color: "#B45309",
            typography: "s3", fontWeight: 700,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {step + 1}/{total}
        </Box>
        <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1, minWidth: 0 }}>
          {prompt}
        </Typography>
        <IconButton size="small" sx={{ color: "text.subtitle", p: 0.5 }}>
          <Iconify icon="solar:alt-arrow-down-linear" width={14} />
        </IconButton>
        <IconButton size="small" sx={{ color: "text.subtitle", p: 0.5 }}>
          <Iconify icon="mingcute:close-line" width={14} />
        </IconButton>
      </Stack>

      <Box sx={{ px: 1.75, pb: 1.25 }}>
        {description && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.25 }}>
            {description}
          </Typography>
        )}

        {/* composer — textarea + inline action row + optional AI bar on top */}
        <Box
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            onFiles(e.dataTransfer.files);
          }}
          sx={{
            borderRadius: 1.5, border: "1.5px solid",
            borderColor: (t) => (dragging
              ? "#7857FC"
              : alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.14)),
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.02),
            transition: "border-color .15s ease, background-color .15s ease",
            "&:focus-within": {
              borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.32 : 0.28),
            },
            overflow: "hidden",
          }}
        >
          {/* Falcon AI inline bar — same shape as InstructionEditor */}
          {aiOpen && (
            <Box
              sx={{
                borderBottom: "1px solid", borderColor: "divider",
                bgcolor: (t) => (t.palette.mode === "dark" ? "#1a1a2e" : "#fafafe"),
              }}
            >
              <Stack direction="row" alignItems="center" sx={{ px: 1.5, pt: 1 }}>
                {aiLoading ? (
                  <Stack direction="row" alignItems="center" spacing={1} sx={{ flex: 1 }}>
                    <CircularProgress size={14} />
                    <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                      Falcon is drafting…
                    </Typography>
                  </Stack>
                ) : !hasResult ? (
                  <InputBase
                    autoFocus fullWidth
                    placeholder={
                      value?.trim()
                        ? "Describe changes — e.g. 'make it escalate faster'"
                        : "Describe your agent — e.g. 'triages GitHub issues into Linear'"
                    }
                    value={aiPrompt}
                    onChange={(e) => setAiPrompt(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        submitAI(aiPrompt);
                      }
                      if (e.key === "Escape") handleClose();
                    }}
                    sx={{ fontSize: 13, flex: 1 }}
                  />
                ) : (
                  <Typography
                    sx={{
                      flex: 1, typography: "s3",
                      color: "text.secondary", fontStyle: "italic",
                    }}
                  >
                    {aiPrompt}
                  </Typography>
                )}

                <Stack direction="row" alignItems="center" spacing={0.5} sx={{ ml: 1, flexShrink: 0 }}>
                  {hasResult && (
                    <>
                      <Button
                        size="small" onClick={handleReject}
                        sx={{ textTransform: "none", fontSize: 12, color: "text.secondary", minWidth: 0, px: 1 }}
                      >
                        Reject
                      </Button>
                      <Button
                        size="small" variant="outlined" onClick={handleAccept}
                        sx={{ textTransform: "none", fontSize: 12, minWidth: 0, px: 1.5, fontWeight: 600 }}
                      >
                        Accept
                      </Button>
                    </>
                  )}
                  {!hasResult && !aiLoading && (
                    <IconButton
                      size="small" onClick={() => submitAI(aiPrompt)}
                      disabled={!aiPrompt.trim()} sx={{ p: 0.5 }}
                    >
                      <Iconify
                        icon="mdi:arrow-up-circle" width={20}
                        sx={{ color: aiPrompt.trim() ? "primary.main" : "text.disabled" }}
                      />
                    </IconButton>
                  )}
                  <IconButton size="small" onClick={handleClose} sx={{ p: 0.25 }}>
                    <Iconify icon="mdi:close" width={16} sx={{ color: "text.disabled" }} />
                  </IconButton>
                </Stack>
              </Stack>

              {hasResult && (
                <Box sx={{ px: 1.5, pb: 1, pt: 0.5 }}>
                  <InputBase
                    inputRef={followUpRef} fullWidth
                    placeholder="Add a follow-up…"
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey && e.target.value.trim()) {
                        e.preventDefault();
                        submitAI(e.target.value);
                        e.target.value = "";
                      }
                      if (e.key === "Escape") handleClose();
                    }}
                    sx={{ fontSize: 13, borderTop: "1px solid", borderColor: "divider", pt: 0.75 }}
                  />
                </Box>
              )}
            </Box>
          )}

          <Box sx={{ p: 1.5 }}>
            <TextField
              fullWidth multiline minRows={5} maxRows={12} variant="standard"
              placeholder={placeholder}
              value={value}
              onChange={(e) => onChange(e.target.value)}
              InputProps={{
                disableUnderline: true,
                sx: { typography: "s2", lineHeight: 1.6, px: 0.5, py: 0.25 },
              }}
            />
            <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mt: 0.5 }}>
              <Typography sx={{ typography: "s3", color: "text.disabled", flex: 1 }}>
                {value.trim()
                  ? `${value.trim().split(/\s+/).length} words`
                  : "Type, drop a file, or use the tools →"}
              </Typography>
              <Tooltip arrow title="Write with Falcon AI" placement="top">
                <IconButton
                  size="small"
                  onClick={() => setAiOpen(true)}
                  sx={{
                    p: 0.5,
                    color: aiOpen ? "primary.main" : "text.subtitle",
                    "&:hover": {
                      bgcolor: (t) => (t.palette.mode === "dark"
                        ? "rgba(124,77,255,0.12)"
                        : "rgba(124,77,255,0.06)"),
                      color: "primary.main",
                    },
                  }}
                >
                  <SvgColor
                    src="/assets/icons/navbar/ic_falcon_ai.svg"
                    sx={{ width: 18, height: 18 }}
                  />
                </IconButton>
              </Tooltip>
              <Tooltip arrow title="Attach reference material">
                <IconButton
                  size="small" onClick={() => inputRef.current?.click()}
                  sx={{ color: "text.subtitle" }}
                >
                  <Iconify icon="solar:paperclip-linear" width={16} />
                </IconButton>
              </Tooltip>
              <VoiceInput onTranscript={(text) => onChange(value ? `${value} ${text}` : text)} />
            </Stack>
          </Box>
        </Box>

        {/* suggested chips */}
        {Array.isArray(chips) && chips.length > 0 && (
          <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75} sx={{ mt: 1.25 }}>
            {chips.map((c) => (
              <Box
                key={c}
                onClick={() => onChange(value ? `${value.trim()} ${c}` : c)}
                sx={{
                  px: 1, py: 0.375, borderRadius: 0.875,
                  border: "1px solid", borderColor: "divider",
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.02),
                  cursor: "pointer",
                  typography: "s3", color: "text.secondary",
                  "&:hover": { borderColor: "text.disabled", color: "text.primary" },
                }}
              >
                {c}
              </Box>
            ))}
          </Stack>
        )}

        {/* attached files list */}
        {files.length > 0 && (
          <Stack spacing={0.5} sx={{ mt: 1.25, maxHeight: 180, overflowY: "auto" }}>
            {files.map((f) => (
              <Stack
                key={f.id}
                direction="row" alignItems="center" spacing={1}
                sx={{
                  px: 1.25, py: 0.75, borderRadius: 1,
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.05 : 0.03),
                }}
              >
                <Iconify icon={iconForFile(f.name)} width={16} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                <Typography noWrap sx={{ typography: "s2", color: "text.primary", flex: 1, minWidth: 0 }}>
                  {f.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.disabled", flexShrink: 0 }}>
                  {formatFileSize(f.size)}
                </Typography>
                <IconButton size="small" onClick={() => onRemove(f.id)} sx={{ p: 0.25, color: "text.subtitle" }}>
                  <Iconify icon="mingcute:close-line" width={12} />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        )}

        <input
          ref={inputRef}
          type="file" multiple hidden
          accept={accept}
          onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }}
        />
      </Box>

      <Stack direction="row" alignItems="center" sx={{ px: 1.5, py: 1.25 }}>
        {onBack ? (
          <Button
            size="small" onClick={onBack}
            sx={{
              typography: "s2", fontWeight: 600, color: "text.primary",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
              px: 1.5, borderRadius: 1,
              "&:hover": {
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.12 : 0.08),
              },
            }}
          >
            Back
          </Button>
        ) : <Box />}
        <Box flex={1} />
        <Stack direction="row" spacing={1}>
          {onSkip && (
            <Button
              size="small" onClick={onSkip}
              sx={{
                typography: "s2", fontWeight: 600, color: "text.primary",
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
                px: 1.5, borderRadius: 1,
                "&:hover": {
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.12 : 0.08),
                },
              }}
            >
              Skip
            </Button>
          )}
          <Tooltip
            arrow
            title={hasResult ? "Accept or reject the Falcon AI draft first — the current text isn't committed yet." : ""}
          >
            <Box component="span">
              <Button
                size="small" onClick={onNext}
                disabled={!canSubmit || hasResult}
                variant="contained" color="primary"
                sx={{
                  typography: "s2", fontWeight: 700,
                  px: 1.75, borderRadius: 1,
                }}
              >
                {submitLabel || "Next"}
              </Button>
            </Box>
          </Tooltip>
        </Stack>
      </Stack>
    </Box>
  );
}

DescribeFlowStep.propTypes = {
  step: PropTypes.number, total: PropTypes.number,
  prompt: PropTypes.string, description: PropTypes.string, placeholder: PropTypes.string,
  chips: PropTypes.arrayOf(PropTypes.string),
  value: PropTypes.string, onChange: PropTypes.func,
  files: PropTypes.array, onAdd: PropTypes.func, onRemove: PropTypes.func,
  onBack: PropTypes.func, onSkip: PropTypes.func, onNext: PropTypes.func,
  canSubmit: PropTypes.bool, submitLabel: PropTypes.string, accept: PropTypes.string,
};

function iconForFile(name) {
  const ext = String(name).split(".").pop()?.toLowerCase();
  if (["csv", "tsv"].includes(ext)) return "solar:document-text-linear";
  if (["json"].includes(ext)) return "solar:code-square-linear";
  if (["md", "txt"].includes(ext)) return "solar:document-linear";
  if (["pdf"].includes(ext)) return "solar:document-medicine-linear";
  if (["docx", "doc"].includes(ext)) return "solar:document-add-linear";
  if (["wav", "mp3", "m4a"].includes(ext)) return "solar:microphone-3-linear";
  return "solar:paperclip-linear";
}

/* ── mock generator ──────────────────────────────────────────────────
   Simulates the Falcon-AI-drafts-a-flow endpoint. Reads keywords out
   of the user's short prompt to build a plausible flow description
   in the same shape a real writer would return. Swap for a real
   endpoint (POST /model-hub/ai-flow-writer/ or similar) when it's
   built — the surrounding UI is already wired to a controlled onChange
   so the drop-in is trivial.
*/
async function fakeFalconWriter(instruction, previous) {
  await new Promise((r) => setTimeout(r, 900));
  const p = instruction.toLowerCase();
  const isRefine = previous && previous.trim().length > 40;

  if (isRefine) {
    return `${previous.trim()}\n\nRefinement: ${instruction.trim()}`;
  }

  if (/support|ticket|refund|complain/.test(p)) {
    return "A customer emails us with a support request. The agent reads the message, classifies the issue, looks up the account in Salesforce, and drafts a reply from the knowledge base. Refunds under $100 are auto-approved; anything over is escalated to a human on-call in Slack. The task is done when the customer receives a resolution and the ticket is marked closed.";
  }
  if (/github|pr|code|issue|linear/.test(p)) {
    return "A new GitHub issue is opened. The agent reads the body, checks the linked repo for context, and triages it: bug reports get a severity label and a Linear ticket in the right project; feature requests get a comment thread with clarifying questions. Escalates to a maintainer if the issue mentions a security or data-loss keyword. The task is done when a Linear ticket exists or the issue is closed as invalid.";
  }
  if (/meeting|calendar|schedul|invite/.test(p)) {
    return "A user asks the agent to schedule a meeting. The agent reads the request, checks the invitee list against Google Calendar, proposes three times that work for everyone, and drafts a calendar invite once a slot is picked. Sends the invite from the user's account only after they confirm. The task is done when the invite is sent and all attendees have received it.";
  }
  if (/sales|crm|lead|deal|hubspot/.test(p)) {
    return "A new lead lands in HubSpot. The agent enriches the record from the company's LinkedIn and website, scores it against the current ICP, and either books an intro call (via the calendar tool) or drops it into a nurture sequence. Escalates to the AE if the deal is over $50k. The task is done when the lead has a next-step assigned in HubSpot.";
  }
  if (/doc|writ|draft|report|summar/.test(p)) {
    return "A user asks for a written document. The agent pulls source material from the linked Notion pages and Google Docs, outlines the doc, drafts each section, and posts a review link. Never publishes externally without approval. The task is done when the reviewer accepts the draft or requests revisions.";
  }
  return `An agent that ${instruction.trim().replace(/\.$/, "")}. It's triggered by an incoming request, reaches for the relevant tools, does the work step by step, and hands off to a human on anything uncertain or high-impact. The task is done when the outcome the user asked for is achieved and logged.`;
}
