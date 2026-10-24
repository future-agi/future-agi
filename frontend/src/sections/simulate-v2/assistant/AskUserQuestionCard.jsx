import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Button, TextField } from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../components/clickable";

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
 * onSubmit({ pick, picks, other })
 */
export default function AskUserQuestionCard({ question, onSubmit, onSkip }) {
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
        return {
          ...a,
          picks: has ? a.picks.filter((v) => v !== i) : [...a.picks, i],
        };
      });
    } else {
      setAnswer((a) => ({ ...a, pick: i }));
    }
  };

  const submit = () => {
    setResolved({ ...answer });
    onSubmit?.(answer);
  };
  const skip = () => {
    setResolved({ skipped: true });
    onSkip?.();
  };

  if (resolved) {
    return <ResolvedSummary question={question} answer={resolved} />;
  }

  return (
    <Box
      sx={{
        borderRadius: 2,
        border: "1px solid",
        borderColor: "border.default",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {/* header */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.75, py: 1.25 }}
      >
        <Box
          sx={{
            px: 0.75,
            py: 0.125,
            borderRadius: 999,
            flexShrink: 0,
            bgcolor: "action.selected",
            color: "text.secondary",
            typography: "s3",
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {question.step}/{question.total}
        </Box>
        <Typography
          sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}
        >
          {question.prompt}
        </Typography>
      </Stack>

      {/* options */}
      <Stack spacing={0.5} sx={{ px: 1.25, pb: 0.75 }}>
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
          onSelect={() => {
            /* focused when the user types */
          }}
          bottomChildren={
            <TextField
              size="small"
              fullWidth
              variant="outlined"
              value={answer.other}
              onChange={(e) =>
                setAnswer((a) => ({ ...a, other: e.target.value }))
              }
              placeholder="Type your own answer here"
              InputProps={{
                sx: {
                  typography: "s2",
                  bgcolor: "action.selected",
                  "& fieldset": { border: "none" },
                },
              }}
              sx={{ mt: 0.75 }}
            />
          }
        />
      </Stack>

      {/* bottom bar */}
      <Stack direction="row" alignItems="center" sx={{ px: 1.5, py: 1.25 }}>
        <Box flex={1} />
        <Stack direction="row" spacing={1}>
          <Button
            size="small"
            onClick={skip}
            sx={{
              color: "text.primary",
              bgcolor: "action.selected",
              "&:hover": {
                bgcolor: "action.focus",
              },
            }}
          >
            Skip
          </Button>
          <Button
            size="small"
            onClick={submit}
            disabled={!canSubmit}
            sx={{
              color: canSubmit ? "common.white" : "text.disabled",
              bgcolor: canSubmit ? "status.discovery.solid" : "action.selected",
              "&:hover": {
                bgcolor: canSubmit ? "status.discovery.solid" : undefined,
                opacity: canSubmit ? 0.9 : undefined,
              },
            }}
          >
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
};

function AskOptionRow({
  label,
  description,
  selected,
  multi,
  index,
  onSelect,
  bottomChildren,
}) {
  return (
    <Box
      {...clickable(onSelect, {
        role: multi ? "checkbox" : "radio",
        checked: selected,
      })}
      sx={{
        ...focusRing,
        p: 1.25,
        borderRadius: "8px",
        cursor: "pointer",
        bgcolor: "action.hover",
        "&:hover": {
          bgcolor: "action.selected",
        },
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.25}>
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}
          >
            {label}
          </Typography>
          {description && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}
            >
              {description}
            </Typography>
          )}
        </Box>
        {multi ? (
          <Box
            sx={{
              width: 18,
              height: 18,
              borderRadius: "4px",
              flexShrink: 0,
              mt: "1px",
              display: "grid",
              placeItems: "center",
              border: "1.5px solid",
              borderColor: selected ? "status.discovery.solid" : "border.hover",
              bgcolor: selected ? "status.discovery.solid" : "transparent",
              transition: "background-color .12s ease, border-color .12s ease",
            }}
          >
            {selected && (
              <Box
                component="svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={3.25}
                strokeLinecap="round"
                strokeLinejoin="round"
                sx={{
                  width: 11,
                  height: 11,
                  display: "block",
                  color: "status.discovery.onSolid",
                }}
              >
                <polyline points="5,12.5 10,17.5 19,7" />
              </Box>
            )}
          </Box>
        ) : (
          <Box
            sx={{
              minWidth: 22,
              height: 20,
              px: 0.75,
              borderRadius: "4px",
              flexShrink: 0,
              mt: "1px",
              display: "grid",
              placeItems: "center",
              border: "1px solid",
              borderColor: selected
                ? "status.discovery.solid"
                : "border.default",
              bgcolor: selected ? "status.discovery.solid" : "transparent",
              color: selected ? "status.discovery.onSolid" : "text.subtitle",
              typography: "s3",
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
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
  label: PropTypes.string,
  description: PropTypes.string,
  selected: PropTypes.bool,
  multi: PropTypes.bool,
  index: PropTypes.number,
  onSelect: PropTypes.func,
  bottomChildren: PropTypes.node,
};

/**
 * Compact summary shown after the user submits — mirrors Claude's
 * post-submit shape: prompt on top, chosen option(s) below with a
 * dimmed check icon, and a subtle divider.
 */
function ResolvedSummary({ question, answer }) {
  const picks = answer.skipped
    ? []
    : question.multiSelect
      ? (answer.picks || [])
          .map((i) => question.options[i]?.label)
          .filter(Boolean)
      : answer.pick != null
        ? [question.options[answer.pick]?.label].filter(Boolean)
        : [];
  const other = answer.other?.trim();

  return (
    <Box
      sx={{
        borderRadius: 2,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
        px: 1.75,
        py: 1.25,
      }}
    >
      <Typography
        sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}
      >
        {question.prompt}
      </Typography>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mt: 0.75 }}
      >
        <Iconify
          icon={
            answer.skipped
              ? "solar:close-circle-linear"
              : "solar:check-circle-bold"
          }
          width={14}
          sx={{
            color: answer.skipped ? "text.subtitle" : "primary.main",
            flexShrink: 0,
          }}
        />
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {answer.skipped
            ? "Skipped"
            : other && picks.length === 0
              ? other
              : [...picks, other].filter(Boolean).join(" · ")}
        </Typography>
      </Stack>
    </Box>
  );
}
ResolvedSummary.propTypes = {
  question: PropTypes.object,
  answer: PropTypes.object,
};
