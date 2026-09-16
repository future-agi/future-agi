import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip, TextField } from "@mui/material";

import Iconify from "src/components/iconify";
import { BUILD_TONES } from "../buildTones";
import { READ_AUDIT_COPY, blockedHint, skippedHint } from "../readAudit.constants";
import AskOptionRow from "./AskOptionRow";

// The open-questions stepper card. One question at a time, its options numbered
// as selectable rows plus a free-text "Other". On the last question the primary
// action becomes "Build the environment" — the questionnaire and the build are
// one continuous act; the button is disabled while any question is still open
// and a tooltip explains what's blocking. Boolean questions expand to plain
// Yes/No rows (the receipt's labels, not the legacy stepper's "Yes, enforced").
export default function AskUserQuestionCard({
  step,
  total,
  question,
  answer,
  onPick,
  onOtherChange,
  onBack,
  onSkip,
  onNext,
  onBuild,
  canSubmit,
  isLast,
  skipped,
  openCount,
  skippedCount,
}) {
  const buildBlocked = openCount > 0 && !(canSubmit || skipped);
  const buildHint = buildBlocked
    ? blockedHint(openCount)
    : skippedCount > 0
      ? skippedHint(skippedCount)
      : "";
  const options = question?.options || (question?.kind === "boolean"
    ? [{ id: "yes", label: "Yes" }, { id: "no", label: "No" }]
    : []);
  const otherIndex = options.length;
  const otherText = answer?.other?.trim() || "";
  const otherActive = otherText.length > 0;
  const rowIsSelected = (i) => answer?.pick === i;

  return (
    <Box
      sx={{
        borderRadius: 2,
        border: "1px solid",
        borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.08),
        bgcolor: "background.paper",
        overflow: "hidden",
        mt: 1,
      }}
    >
      {/* header */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.75, py: 1.25 }}>
        <Box
          sx={{
            px: 0.75, py: 0.125, borderRadius: 999, flexShrink: 0,
            bgcolor: (t) => alpha(BUILD_TONES.amberDeep, t.palette.mode === "dark" ? 0.28 : 0.16),
            color: BUILD_TONES.amberDeep,
            typography: "s3", fontWeight: "fontWeightBold",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {step + 1}/{total}
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", minWidth: 0 }}>
            {question?.title}
          </Typography>
          {question?.why && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>
              {question.why}
            </Typography>
          )}
        </Box>
      </Stack>

      {/* options */}
      <Stack spacing={0.5} sx={{ px: 1.25, pb: 0.75 }}>
        {options.map((opt, i) => (
          <AskOptionRow
            key={opt.id || opt.label}
            label={opt.label}
            description={opt.description}
            selected={rowIsSelected(i)}
            index={i + 1}
            onSelect={() => onPick?.(i)}
          />
        ))}
        <AskOptionRow
          label={READ_AUDIT_COPY.other}
          selected={otherActive}
          index={otherIndex + 1}
          onSelect={() => { /* focus stays with the input */ }}
          bottomChildren={(
            <TextField
              size="small" fullWidth variant="outlined"
              value={otherText}
              onChange={(e) => onOtherChange?.(e.target.value)}
              placeholder={READ_AUDIT_COPY.otherPlaceholder}
              InputProps={{
                sx: {
                  typography: "s2",
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03),
                  "& fieldset": { border: "none" },
                },
              }}
              sx={{ mt: 0.75 }}
            />
          )}
        />
      </Stack>

      {skipped && (
        <Stack
          direction="row" alignItems="center" spacing={0.75}
          sx={{
            mx: 1.5, mb: 1, px: 1.25, py: 0.75, borderRadius: 1,
            bgcolor: (t) => alpha(BUILD_TONES.amber, t.palette.mode === "dark" ? 0.12 : 0.08),
            color: BUILD_TONES.amber,
            width: "fit-content",
          }}
        >
          <Iconify icon="solar:info-circle-linear" width={14} />
          <Typography sx={{ typography: "s3", fontWeight: "fontWeightSemiBold" }}>
            {READ_AUDIT_COPY.skippedNote}
          </Typography>
        </Stack>
      )}

      {/* bottom bar */}
      <Stack direction="row" alignItems="center" sx={{ px: 1.5, py: 1.25 }}>
        {onBack ? (
          <Button
            size="small" onClick={onBack}
            sx={{
              typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.primary",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
              px: 1.5, borderRadius: 1,
              "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.12 : 0.08) },
            }}
          >
            {READ_AUDIT_COPY.back}
          </Button>
        ) : <Box />}
        <Box flex={1} />
        <Stack direction="row" spacing={1}>
          <Button
            size="small" onClick={onSkip}
            sx={{
              typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.primary",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
              px: 1.5, borderRadius: 1,
              "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.12 : 0.08) },
            }}
          >
            {READ_AUDIT_COPY.skip}
          </Button>
          {isLast ? (
            <Tooltip arrow title={buildHint}>
              <span>
                <Button
                  size="small"
                  variant="contained" color="primary"
                  onClick={() => onBuild?.()}
                  disabled={buildBlocked}
                  endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
                  sx={{ typography: "s2", fontWeight: "fontWeightBold", px: 1.5 }}
                >
                  {READ_AUDIT_COPY.build}
                </Button>
              </span>
            </Tooltip>
          ) : (
            <Button
              size="small" onClick={onNext}
              disabled={!canSubmit}
              sx={{
                typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.primary",
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.09),
                px: 1.5, borderRadius: 1,
                "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.22 : 0.14) },
                "&.Mui-disabled": {
                  color: "text.disabled",
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04),
                },
              }}
            >
              {READ_AUDIT_COPY.next}
            </Button>
          )}
        </Stack>
      </Stack>
    </Box>
  );
}

AskUserQuestionCard.propTypes = {
  step: PropTypes.number,
  total: PropTypes.number,
  question: PropTypes.shape({
    id: PropTypes.string,
    kind: PropTypes.string,
    title: PropTypes.string,
    why: PropTypes.string,
    options: PropTypes.arrayOf(PropTypes.shape({
      id: PropTypes.string,
      label: PropTypes.string,
      description: PropTypes.string,
    })),
  }),
  answer: PropTypes.shape({
    pick: PropTypes.number,
    other: PropTypes.string,
  }),
  onPick: PropTypes.func,
  onOtherChange: PropTypes.func,
  onBack: PropTypes.func,
  onSkip: PropTypes.func,
  onNext: PropTypes.func,
  onBuild: PropTypes.func,
  canSubmit: PropTypes.bool,
  isLast: PropTypes.bool,
  skipped: PropTypes.bool,
  openCount: PropTypes.number,
  resolvedCount: PropTypes.number,
  skippedCount: PropTypes.number,
};
