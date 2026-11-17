import { formatExactMoney } from "src/sections/common/format/money";
import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  FormControlLabel,
  Link,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import { outputText } from "../outputText";
import { runLabel, runShortName } from "src/sections/common/Runs/runLabel";
import { formatMs } from "./alignSteps";
import {
  choiceText,
  pairwiseOptions,
} from "src/sections/develop-detail/Review/reviewModel";

const money = (v) => formatExactMoney(v, { missing: null });

function ResponseColumn({ title, color, run, result, blind, mode }) {
  const meta = blind
    ? []
    : [
        Number.isFinite(result?.latency_ms)
          ? formatMs(result.latency_ms)
          : null,
        Number.isFinite(result?.tokens)
          ? `${Math.round(result.tokens).toLocaleString()} tok`
          : null,
        money(result?.cost),
        result?.steps?.steps ? `${result.steps.steps} steps` : null,
      ].filter(Boolean);
  return (
    <Stack spacing={0.75} sx={{ minWidth: 0 }}>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        {color ? (
          <Box
            sx={{
              width: 3,
              height: 14,
              borderRadius: 1,
              bgcolor: color,
              flexShrink: 0,
            }}
          />
        ) : null}
        <Typography typography="s2" fontWeight="fontWeightSemiBold" noWrap>
          {title}
        </Typography>
        {!blind && run ? (
          <Typography typography="s3" color="text.secondary" noWrap>
            {runShortName(run)}
          </Typography>
        ) : null}
      </Stack>
      <Box
        sx={{
          p: 1.25,
          borderRadius: 0.5,
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
          minHeight: 64,
          maxHeight: 260,
          overflow: "auto",
        }}
      >
        {result?.status === "error" ? (
          <Typography typography="s2" color="error.main">
            {result.error || "This run errored on this row."}
          </Typography>
        ) : result?.status === "missing" || !result ? (
          <Typography
            typography="s2"
            color="text.disabled"
            sx={{ fontStyle: "italic" }}
          >
            Not run on this row
          </Typography>
        ) : (
          <ValueViewer
            value={mode === "raw" ? result.output : outputText(result.output)}
            mode={mode}
            clampChars={900}
            emptyLabel="Empty output"
          />
        )}
      </Box>
      {meta.length ? (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {meta.join(" · ")}
        </Typography>
      ) : null}
      {!blind ? <ScoreChips scores={result?.scores} /> : null}
    </Stack>
  );
}

// `eval:task_completion` -> `Task completion`.
const metricName = (key) => {
  const s = String(key)
    .replace(/^[a-z]+:/, "")
    .replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
};

/** The row's eval results for one run (hidden when blind). */
function ScoreChips({ scores }) {
  const entries = Object.entries(scores ?? {}).filter(
    ([, v]) => v && (v.display != null || Number.isFinite(v.value)),
  );
  if (!entries.length) return null;
  return (
    <Stack direction="row" gap={0.5} flexWrap="wrap">
      {entries.map(([key, v]) => {
        const display = v.display ?? Number(v.value).toFixed(2);
        const pass =
          /^(pass|passed)$/i.test(display) ||
          (/^\d+\/\d+$/.test(display) &&
            display.split("/")[0] === display.split("/")[1]);
        const fail =
          /^(fail|failed)$/i.test(display) || /^0\/\d+$/.test(display);
        const tone = pass ? "success" : fail ? "error" : null;
        return (
          <Box
            key={key}
            component="span"
            sx={(t) => ({
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              height: 20,
              px: 0.75,
              borderRadius: 0.5,
              typography: "s3",
              whiteSpace: "nowrap",
              bgcolor: tone
                ? alpha(t.palette[tone].main, 0.12)
                : "action.hover",
              color: tone
                ? t.palette.mode === "dark"
                  ? t.palette[tone].light
                  : t.palette[tone].dark
                : "text.secondary",
            })}
          >
            {metricName(key)} · {display}
          </Box>
        );
      })}
    </Stack>
  );
}

ScoreChips.propTypes = { scores: PropTypes.object };

ResponseColumn.propTypes = {
  title: PropTypes.string,
  color: PropTypes.string,
  run: PropTypes.object,
  result: PropTypes.object,
  blind: PropTypes.bool,
  mode: PropTypes.string,
};

/**
 * Pairwise card (§3.11 Review tab): baseline run (A) vs the focused
 * candidate (B) on one row. Blind mode shows `Response 1/2` on seeded sides
 * and hides names, scores and cost. Saving reviews isn't available in this
 * release: the votes (left / tie / right) and Auto-advance stay on screen
 * turned off, described by the sentence the card shows once. `Your vote`
 * and Clear choice show only for a pick held in the session's map, which
 * nothing in the app writes; Clear choice then shows turned off the same way.
 */
export default function PairwiseCard({
  runA,
  runB,
  resultA,
  resultB,
  colors,
  blind,
  onBlindChange,
  left,
  preference,
  myChoice,
  comment,
  onCommentChange,
  autoAdvance,
  mode,
}) {
  const unavailableId = useId();
  const labelA = runLabel(runA);
  const labelB = runLabel(runB);
  const options = pairwiseOptions({ left, blind, labelA, labelB });
  const sideRun = (side) => (side === "A" ? runA : runB);
  const sideResult = (side) => (side === "A" ? resultA : resultB);
  const sideColor = (side) =>
    blind ? null : side === "A" ? colors.a : colors.b;
  const right = left === "A" ? "B" : "A";
  const titleFor = (side, n) =>
    blind ? `Response ${n}` : runLabel(sideRun(side));
  const blindName = (choice) =>
    choice === "tie" ? "Tie" : `Response ${choice === left ? 1 : 2} is better`;
  // While Blind is on, the team's choice is neither named nor highlighted
  // (it would anchor the reviewer); only your vote is.
  const selected = myChoice ?? (blind ? null : preference.choice) ?? null;
  const fromTeam = !myChoice && !blind && preference.choice;
  const teamVotes =
    preference.votes.A + preference.votes.B + preference.votes.tie;

  const [commentOpen, setCommentOpen] = useState(Boolean(comment));
  const commentRef = useRef(null);
  useEffect(() => {
    if (comment) setCommentOpen(true);
  }, [comment]);

  const judgeLine = useMemo(() => {
    if (blind || !preference.judge?.choice) return null;
    const j = preference.judge;
    return `LLM judge (${j.model ?? "judge"}): ${choiceText(j.choice, { labelA, labelB })}${j.reason ? ` — ${j.reason}` : ""}`;
  }, [blind, preference.judge, labelA, labelB]);

  return (
    <Stack spacing={1.5}>
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          sx={{ flex: 1 }}
        >
          Which response is better for this row?
        </Typography>
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title="Hide run names, scores and cost; shuffle sides"
        >
          <FormControlLabel
            sx={{ mr: 0 }}
            control={
              <Switch
                size="small"
                checked={Boolean(blind)}
                onChange={(e) => onBlindChange(e.target.checked)}
              />
            }
            label={<Typography typography="s2">Blind</Typography>}
          />
        </CustomTooltip>
      </Stack>

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
          gap: 1.5,
        }}
      >
        <ResponseColumn
          title={titleFor(left, 1)}
          color={sideColor(left)}
          run={sideRun(left)}
          result={sideResult(left)}
          blind={blind}
          mode={mode}
        />
        <ResponseColumn
          title={titleFor(right, 2)}
          color={sideColor(right)}
          run={sideRun(right)}
          result={sideResult(right)}
          blind={blind}
          mode={mode}
        />
      </Box>

      {judgeLine ? (
        <Stack
          direction="row"
          gap={0.75}
          alignItems="flex-start"
          sx={(t) => ({
            px: 1.25,
            py: 1,
            borderRadius: 0.5,
            bgcolor: alpha(t.palette.info.main, 0.08),
          })}
        >
          <Iconify
            icon="mdi:robot-outline"
            width={16}
            sx={{ color: "info.main", mt: "2px", flexShrink: 0 }}
          />
          <Typography typography="s2" color="text.primary">
            {judgeLine}
          </Typography>
        </Stack>
      ) : null}

      <Stack direction="row" gap={1}>
        {options.map((opt) => {
          const active = selected === opt.choice;
          return (
            <Button
              key={opt.key}
              fullWidth
              size="medium"
              variant={active ? "contained" : "outlined"}
              color={active ? "primary" : "inherit"}
              disabled
              aria-describedby={unavailableId}
              aria-pressed={active}
              // Blind buttons name the sides the card shows; the visible label
              // is the side (it fits a third of the drawer), the accessible
              // name and title say the whole choice.
              {...(blind && opt.choice !== "tie"
                ? {
                    "aria-label": blindName(opt.choice),
                    title: blindName(opt.choice),
                  }
                : {})}
              data-review-vote={opt.key}
              sx={{
                justifyContent: "flex-start",
                gap: 1,
                borderColor: active ? undefined : "divider",
                ...(active && fromTeam
                  ? {
                      bgcolor: (t) => alpha(t.palette.primary.main, 0.12),
                      color: "primary.main",
                      boxShadow: "none",
                      "&:hover": {
                        bgcolor: (t) => alpha(t.palette.primary.main, 0.2),
                      },
                    }
                  : {}),
              }}
            >
              <Typography typography="s1" fontWeight="fontWeightMedium" noWrap>
                {blind && opt.choice !== "tie"
                  ? `Response ${opt.choice === left ? 1 : 2}`
                  : opt.label}
              </Typography>
            </Button>
          );
        })}
      </Stack>
      <Typography id={unavailableId} typography="s2" color="text.secondary">
        Saving reviews isn&apos;t available in this release.
      </Typography>

      <Stack direction="row" alignItems="center" gap={1} sx={{ minHeight: 24 }}>
        <Typography typography="s2" color="text.secondary" sx={{ flex: 1 }}>
          {myChoice
            ? `Your vote: ${blind ? blindName(myChoice) : choiceText(myChoice, { labelA, labelB })}`
            : fromTeam
              ? `Current choice from ${teamVotes} ${teamVotes === 1 ? "reviewer" : "reviewers"}: ${choiceText(preference.choice, { labelA, labelB })}.`
              : blind && teamVotes > 0
                ? `${teamVotes} ${teamVotes === 1 ? "reviewer has" : "reviewers have"} voted; their choice stays hidden while Blind is on.`
                : "Not reviewed yet."}
        </Typography>
        {myChoice ? (
          <Link
            component="button"
            type="button"
            typography="s2"
            underline="hover"
            disabled
            aria-describedby={unavailableId}
          >
            Clear choice
          </Link>
        ) : null}
      </Stack>

      {commentOpen ? (
        <TextField
          inputRef={commentRef}
          size="small"
          multiline
          minRows={2}
          placeholder="Why?"
          value={comment}
          onChange={(e) => onCommentChange(e.target.value)}
          fullWidth
        />
      ) : (
        <Link
          component="button"
          type="button"
          typography="s2"
          underline="hover"
          sx={{ alignSelf: "flex-start" }}
          onClick={() => {
            setCommentOpen(true);
            window.requestAnimationFrame(() => commentRef.current?.focus());
          }}
        >
          + Comment
        </Link>
      )}

      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <FormControlLabel
          sx={{ ml: 0 }}
          control={
            <Switch
              size="small"
              checked={autoAdvance}
              disabled
              inputProps={{ "aria-describedby": unavailableId }}
            />
          }
          label={
            <Typography typography="s2">
              Auto-advance to the next row
            </Typography>
          }
        />
        <Typography typography="s3" color="text.disabled">
          S skips
        </Typography>
      </Stack>
    </Stack>
  );
}

PairwiseCard.propTypes = {
  runA: PropTypes.object.isRequired,
  runB: PropTypes.object.isRequired,
  resultA: PropTypes.object,
  resultB: PropTypes.object,
  colors: PropTypes.shape({ a: PropTypes.string, b: PropTypes.string })
    .isRequired,
  blind: PropTypes.bool,
  onBlindChange: PropTypes.func.isRequired,
  left: PropTypes.oneOf(["A", "B"]).isRequired,
  preference: PropTypes.shape({
    choice: PropTypes.string,
    votes: PropTypes.object,
    judge: PropTypes.object,
  }).isRequired,
  myChoice: PropTypes.string,
  comment: PropTypes.string,
  onCommentChange: PropTypes.func.isRequired,
  autoAdvance: PropTypes.bool,
  mode: PropTypes.string,
};
