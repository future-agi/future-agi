import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import {
  DISCLOSURE_PARTS,
  OBSERVED_LABEL,
  DISCLOSURE_LENGTHS,
  SUMMARY_LENGTH,
  disclosureGaps,
  observedFacts,
} from "../lib/disclosure";

function groupFacts(facts) {
  const groups = {};
  for (const { key, fact } of facts) (groups[key] = groups[key] || []).push(fact);
  return Object.entries(groups)
    .map(([key, list]) => `${OBSERVED_LABEL[key] || key}: ${list.slice(0, 6).join(", ")}${list.length > 6 ? ` and ${list.length - 6} more` : ""}`)
    .join("; ");
}

// The confirmed disclosure (SPEC §5.4): pre-filled from the publisher's
// draft, each part kept, edited or written by the reviewer. Parts the checker
// observed facts for show those facts, and must not be empty or "None.".
export default function DisclosureEditor({ value, onChange, observed, draft }) {
  const gaps = disclosureGaps(value, observed);
  const set = (key, text) => onChange({ ...value, [key]: text });
  return (
    <Box data-testid="hub-disclosure-editor" sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <TextField
        label="Summary"
        size="small"
        multiline
        value={value.summary || ""}
        onChange={(e) => set("summary", e.target.value)}
        inputProps={{ maxLength: SUMMARY_LENGTH[1], "data-testid": "hub-disclosure-summary-input" }}
        helperText={`${(value.summary || "").trim().length} / ${SUMMARY_LENGTH[1]}, at least ${SUMMARY_LENGTH[0]}`}
      />
      {DISCLOSURE_PARTS.map((part) => {
        const facts = observedFacts(observed, part.key);
        const gap = gaps.includes(part.key);
        const edited = draft && (draft[part.key] || "") !== (value[part.key] || "");
        return (
          <Box key={part.key} sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
            <TextField
              label={part.label}
              size="small"
              multiline
              error={gap}
              value={value[part.key] || ""}
              onChange={(e) => set(part.key, e.target.value)}
              inputProps={{ maxLength: DISCLOSURE_LENGTHS[part.key][1], "data-testid": `hub-disclosure-${part.key}-input` }}
              helperText={
                gap
                  ? "The checks observed facts for this part: say what it does."
                  : edited
                    ? "Edited in review"
                    : undefined
              }
            />
            {facts.length > 0 && (
              <Typography typography="s3" color={gap ? "error.main" : "text.secondary"} data-testid={`hub-observed-${part.key}`} sx={{ overflowWrap: "anywhere" }}>
                {`Observed: ${groupFacts(facts)}`}
              </Typography>
            )}
          </Box>
        );
      })}
    </Box>
  );
}

DisclosureEditor.propTypes = {
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  observed: PropTypes.object,
  draft: PropTypes.object,
};
