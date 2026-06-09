import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Tooltip } from "@mui/material";
import { classifyChoice, CHOICE_TONE } from "../evalTaskMock";

// ---------------------------------------------------------------------------
// §4.6.1 / FR-3.4 collated choice-eval cell. A choice eval's per-choice columns
// are merged into one column; this renders the occurring labels as chips.
//
// Choice results are NEVER shown as a percentage (per product direction) — the
// chip shows the choice label only. Chip styling follows the Future AGI
// standard outlined chip used elsewhere for choice evals
// (EvaluateArrayCellRenderer): outlined, borderRadius theme.spacing(0.5),
// typography "s3", tone-coloured border/text.
// ---------------------------------------------------------------------------

// Tone → standard FAGI colour tokens (green / amber / red).
const TONE = {
  [CHOICE_TONE.GOOD]: { border: "green.500", color: "green.500" },
  [CHOICE_TONE.PARTIAL]: { border: "warning.main", color: "warning.dark" },
  [CHOICE_TONE.BAD]: { border: "red.500", color: "red.500" },
};

const MAX_CHIPS = 3;

const ToneChip = ({ label, tone }) => {
  const t = TONE[tone] || TONE[CHOICE_TONE.BAD];
  return (
    <Chip
      size="small"
      label={label}
      variant="outlined"
      sx={{
        borderRadius: (theme) => theme.spacing(0.5),
        borderColor: t.border,
        color: t.color,
        fontWeight: 400,
        typography: "s3",
        height: 22,
      }}
    />
  );
};
ToneChip.propTypes = { label: PropTypes.string, tone: PropTypes.string };

const ChoiceClusterCell = (params) => {
  const choices = params?.value?.choices;
  if (!Array.isArray(choices) || choices.length === 0) {
    return (
      <div
        style={{
          padding: "0 12px",
          display: "flex",
          alignItems: "center",
          height: "100%",
        }}
      >
        -
      </div>
    );
  }

  // Only labels that actually occurred (pct > 0), ordered by prevalence; the
  // pct drives ordering only — it is never shown.
  const items = [...choices]
    .filter((c) => (c.pct ?? 0) > 0)
    .sort((a, b) => (b.pct ?? 0) - (a.pct ?? 0));
  if (items.length === 0) {
    return (
      <div
        style={{
          padding: "0 12px",
          display: "flex",
          alignItems: "center",
          height: "100%",
        }}
      >
        -
      </div>
    );
  }

  const shown = items.slice(0, MAX_CHIPS);
  const overflow = items.slice(MAX_CHIPS);

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        px: 1.5,
        height: "100%",
        flexWrap: "wrap",
      }}
    >
      {shown.map((it) => (
        <ToneChip
          key={it.label}
          label={`${it.label} ${Math.round(it.pct)}%`}
          tone={classifyChoice(it.label)}
        />
      ))}
      {overflow.length > 0 && (
        <Tooltip arrow title={overflow.map((i) => i.label).join(", ")}>
          <Box
            component="span"
            sx={{ fontSize: 11, fontWeight: 600, color: "text.secondary" }}
          >
            +{overflow.length}
          </Box>
        </Tooltip>
      )}
    </Box>
  );
};

ChoiceClusterCell.propTypes = {
  value: PropTypes.shape({ choices: PropTypes.array }),
};

export default ChoiceClusterCell;
