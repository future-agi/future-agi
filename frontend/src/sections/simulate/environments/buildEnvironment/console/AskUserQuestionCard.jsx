import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Button, TextField, Checkbox } from "@mui/material";
import Iconify from "src/components/iconify";

import ChatMarkdown from "./ChatMarkdown";

// Prompts are the model's free text. Plain questions must show exactly as written,
// so one stray signal like `# of rooms` or a single `code` span is not enough: it
// takes two, or a table's `|---|---|` row (not just any line with a pipe in it).
const MARKDOWN_SIGNAL = /^(#{1,6} |[-*+] |\d+[.)] |> |```)|\*\*[^*\s][^*]*\*\*|`[^`\n]+`/gm;
const TABLE_DIVIDER = /^\s*\|?\s*:?-+:?\s*\|[\s|:-]*$/m;
const looksLikeMarkdown = (text = "") =>
  TABLE_DIVIDER.test(text) || (text.match(MARKDOWN_SIGNAL) || []).length >= 2;

function Prompt({ text, sx }) {
  if (looksLikeMarkdown(text)) {
    // The opening line reads as the question; anything after it, like a full
    // design to approve, stays regular weight.
    return (
      <ChatMarkdown
        text={text}
        sx={{ typography: "s1", "& > p:first-of-type": { fontWeight: 500 }, ...sx }}
      />
    );
  }
  return (
    <Typography sx={{ typography: "s1", fontWeight: 500, whiteSpace: "pre-wrap", color: "text.primary", ...sx }}>
      {text}
    </Typography>
  );
}
Prompt.propTypes = { text: PropTypes.string, sx: PropTypes.object };

/**
 * Claude-style AskUserQuestion card, ported for use inside the
 * workspace chat when the builder is in Guided mode.
 *
 * State-managed internally — the caller passes a `question` and an
 * `onSubmit(answer)` callback, everything in between (single/multi
 * select, "Other" free text, Skip, Submit gating) is handled here.
 *
 * question:
 *   {
 *     step: 1, total: 3,
 *     prompt: "How should refunds above $200 behave?",
 *     multiSelect: false,
 *     options: [
 *       { label: "Require supervisor approval", description: "..." },
 *       { label: "Auto-approve up to $500", description: "..." },
 *     ],
 *   }
 *
 * onSubmit(answers) — chosen labels (+ any "Other" text)
 */
export default function AskUserQuestionCard({
  question,
  onSubmit,
  onSkip,
  resolved: resolvedProp = false,
  answerText,
}) {
  const [answer, setAnswer] = useState({ pick: null, picks: [], other: "" });
  const [resolved, setResolved] = useState(null);

  const otherIndex = question.options.length;
  const otherActive = answer.other.trim().length > 0;

  const rowIsSelected = (i) => {
    if (i === otherIndex) return otherActive;
    if (question.multiSelect) return answer.picks.includes(i);
    return answer.pick === i;
  };

  const canSubmit = question.multiSelect
    ? answer.picks.length > 0 || otherActive
    : answer.pick != null || otherActive;

  const pick = (i) => {
    if (i === otherIndex) return;
    if (question.multiSelect) {
      setAnswer((a) => {
        const has = a.picks.includes(i);
        return { ...a, picks: has ? a.picks.filter((v) => v !== i) : [...a.picks, i] };
      });
    } else {
      setAnswer((a) => ({ ...a, pick: i }));
    }
  };

  const chosenLabels = () => {
    const labels = question.multiSelect
      ? answer.picks.map((i) => question.options[i]?.label).filter(Boolean)
      : answer.pick != null ? [question.options[answer.pick]?.label].filter(Boolean) : [];
    const other = answer.other.trim();
    return other ? [...labels, other] : labels;
  };

  const submit = () => {
    setResolved({ ...answer });
    onSubmit?.(chosenLabels());
  };
  const skip = () => {
    setResolved({ skipped: true });
    onSkip?.();
  };

  // Resolved either by this session's submit (internal state) or because the
  // conversation came back already answered (props) — e.g. after a page refresh.
  if (resolved || resolvedProp) {
    return (
      <ResolvedSummary
        question={question}
        answer={resolved || { external: true }}
        text={resolved ? undefined : answerText}
      />
    );
  }

  return (
    <Box
      sx={{
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {/* header */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1.5 }}>
        <Box
          sx={{
            px: 0.75, py: 0.25, borderRadius: "4px", flexShrink: 0,
            bgcolor: "action.selected",
            color: "text.secondary",
            typography: "s3", fontWeight: 500, fontVariantNumeric: "tabular-nums",
          }}
        >
          {question.step}/{question.total}
        </Box>
        <Prompt text={question.prompt} sx={{ flex: 1, minWidth: 0 }} />
      </Stack>

      {/* options */}
      <Stack spacing={0.5} sx={{ px: 1.5, pb: 1 }}>
        {question.options.map((opt, i) => (
          <AskOptionRow
            key={opt.label}
            label={opt.label}
            description={opt.description}
            selected={rowIsSelected(i)}
            multi={question.multiSelect}
            index={i + 1}
            onSelect={() => pick(i)}
          />
        ))}
        <AskOptionRow
          label="Other"
          selected={otherActive}
          multi={question.multiSelect}
          index={otherIndex + 1}
          onSelect={() => { /* focused when the user types */ }}
          bottomChildren={(
            <TextField
              size="small" fullWidth variant="outlined"
              value={answer.other}
              onChange={(e) => setAnswer((a) => ({ ...a, other: e.target.value }))}
              placeholder="Type your own answer here"
              InputProps={{
                sx: {
                  typography: "m3",
                  bgcolor: "background.paper",
                },
              }}
              sx={{ mt: 0.75 }}
            />
          )}
        />
      </Stack>

      {/* bottom bar */}
      <Stack direction="row" alignItems="center" sx={{ px: 2, py: 1.5 }}>
        <Box flex={1} />
        <Stack direction="row" spacing={1}>
          {onSkip && (
            <Button size="small" variant="outlined" color="inherit" onClick={skip}>
              Skip
            </Button>
          )}
          <Button size="small" variant="contained" onClick={submit} disabled={!canSubmit}>
            Submit
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}
AskUserQuestionCard.propTypes = {
  question: PropTypes.object.isRequired,
  onSubmit: PropTypes.func,
  onSkip: PropTypes.func,
  // When the conversation returns an already-answered question, the card renders
  // its resolved summary from these props instead of internal submit state.
  resolved: PropTypes.bool,
  answerText: PropTypes.string,
};

function AskOptionRow({ label, description, selected, multi, index, onSelect, bottomChildren }) {
  return (
    <Box
      onClick={onSelect}
      sx={{
        p: 1.5, borderRadius: "8px", cursor: "pointer",
        bgcolor: "background.neutral",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 500, color: "text.primary" }}>
            {label}
          </Typography>
          {description && (
            <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>
              {description}
            </Typography>
          )}
        </Box>
        {multi ? (
          // The theme's checkbox as a display: the row owns the click.
          <Checkbox
            checked={Boolean(selected)}
            tabIndex={-1}
            size="small"
            onChange={() => {}}
            inputProps={{ "aria-hidden": true }}
            sx={{ p: 0, flexShrink: 0, pointerEvents: "none" }}
          />
        ) : (
          <Box
            sx={{
              minWidth: 24, height: 20, px: 0.75, borderRadius: "4px", flexShrink: 0, mt: "1px",
              display: "grid", placeItems: "center",
              bgcolor: selected ? "text.primary" : "action.selected",
              color: selected ? "background.paper" : "text.secondary",
              typography: "s3", fontWeight: 500, fontVariantNumeric: "tabular-nums",
            }}
          >
            {index}
          </Box>
        )}
      </Stack>
      {bottomChildren}
    </Box>
  );
}
AskOptionRow.propTypes = {
  label: PropTypes.string, description: PropTypes.string,
  selected: PropTypes.bool, multi: PropTypes.bool, index: PropTypes.number,
  onSelect: PropTypes.func, bottomChildren: PropTypes.node,
};

/**
 * Compact summary shown after the user submits — mirrors Claude's
 * post-submit shape: prompt on top, chosen option(s) below with a
 * dimmed check icon, and a subtle divider.
 */
function ResolvedSummary({ question, answer, text }) {
  const picks = answer.skipped
    ? []
    : (question.multiSelect
      ? (answer.picks || []).map((i) => question.options[i]?.label).filter(Boolean)
      : answer.pick != null ? [question.options[answer.pick]?.label].filter(Boolean) : []);
  const other = answer.other?.trim();
  // An externally-resolved question (from a refreshed conversation) carries only
  // the answer text — show it verbatim rather than reconstructing picks.
  const summary = text != null
    ? text
    : answer.skipped
      ? "Skipped"
      : (other && picks.length === 0 ? other : [...picks, other].filter(Boolean).join(" · "));

  return (
    <Box
      sx={{
        borderRadius: "8px",
        border: "1px solid", borderColor: "divider",
        bgcolor: "background.neutral",
        px: 2, py: 1.5,
      }}
    >
      <Prompt text={question.prompt} />
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: 0.75 }}>
        <Iconify
          icon={answer.skipped ? "solar:close-circle-linear" : "solar:check-circle-bold"}
          width={14}
          sx={{ color: answer.skipped ? "text.secondary" : "status.success.solid", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {summary}
        </Typography>
      </Stack>
    </Box>
  );
}
ResolvedSummary.propTypes = { question: PropTypes.object, answer: PropTypes.object, text: PropTypes.string };
