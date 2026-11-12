import React, { useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import {
  DISCLOSURE_PARTS,
  OBSERVED_LABEL,
  DISCLOSURE_LENGTHS,
  disclosureGaps,
  disclosureShapeErrors,
  observedFacts,
} from "../lib/disclosure";

import ToneChip from "./ToneChip";

const ROWS = [{ key: "summary", label: "Summary" }, ...DISCLOSURE_PARTS];

function groupFacts(facts) {
  const groups = {};
  for (const { key, fact } of facts)
    (groups[key] = groups[key] || []).push(fact);
  return Object.entries(groups)
    .map(
      ([key, list]) =>
        `${OBSERVED_LABEL[key] || key}: ${list.slice(0, 6).join(", ")}${list.length > 6 ? ` and ${list.length - 6} more` : ""}`,
    )
    .join("; ");
}

// The parts that need the reviewer's words: an observed fact with an empty
// part, or a length the server refuses.
function partsToWrite(value, observed) {
  const gaps = disclosureGaps(value, observed);
  const shape = disclosureShapeErrors(value);
  return ROWS.map((row) => row.key).filter(
    (key) => gaps.includes(key) || shape[key],
  );
}

// The confirmed disclosure (SPEC §5.4), as six readable rows: the summary and
// the five parts, each pre-filled from the publisher's draft with its own Edit.
// An edit applies as it is typed; Done closes the box and Cancel puts back the
// text it opened with. A part the checks observed facts for shows them under
// it, and must not be empty or "None."; such a part, and one whose length the
// server refuses, opens for writing from the start. The rows are one named
// group, and each text box's own name says it is part of the disclosure
// ("Network disclosure"), beside its visible label, so a screen reader moving
// between the review page's boxes knows which is which.
export default function DisclosureEditor({
  value,
  onChange,
  observed,
  draft,
  label = "Disclosure, as it will be published",
}) {
  const baseId = useId();
  const [open, setOpen] = useState(() => partsToWrite(value, observed));
  const [before, setBefore] = useState(() =>
    Object.fromEntries(
      partsToWrite(value, observed).map((key) => [key, value[key] || ""]),
    ),
  );
  const inputs = useRef({});
  const edits = useRef({});
  const pendingFocus = useRef(null);
  const gaps = disclosureGaps(value, observed);
  const shape = disclosureShapeErrors(value);
  const set = (key, text) => onChange({ ...value, [key]: text });

  useEffect(() => {
    const next = pendingFocus.current;
    if (!next) return;
    pendingFocus.current = null;
    (next.input ? inputs.current : edits.current)[next.key]?.focus();
  });

  const edit = (key) => {
    setBefore((saved) => ({ ...saved, [key]: value[key] || "" }));
    setOpen((keys) => (keys.includes(key) ? keys : [...keys, key]));
    pendingFocus.current = { key, input: true };
  };
  const close = (key, revert = false) => {
    if (revert) set(key, before[key] ?? "");
    setOpen((keys) => keys.filter((k) => k !== key));
    pendingFocus.current = { key, input: false };
  };

  return (
    <Box
      role="group"
      aria-label={label}
      data-testid="hub-disclosure-editor"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
      }}
    >
      <Box component="dl" sx={{ m: 0 }}>
        {ROWS.map((row, index) => {
          const editing = open.includes(row.key);
          const text = value[row.key] || "";
          const facts = observedFacts(observed, row.key);
          const gap = gaps.includes(row.key);
          const [min, max] = DISCLOSURE_LENGTHS[row.key];
          const edited = draft && (draft[row.key] || "") !== text;
          const inputId = `${baseId}-${row.key}`;
          return (
            <Box
              key={row.key}
              data-testid={`hub-disclosure-row-${row.key}`}
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "minmax(0, 1fr)",
                  sm: "136px minmax(0, 1fr)",
                },
                columnGap: 2,
                rowGap: 0.5,
                px: 2,
                py: 1.5,
                borderTop: index ? "1px solid" : 0,
                borderColor: "divider",
              }}
            >
              <Box
                component="dt"
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "flex-start",
                  gap: 0.5,
                }}
              >
                <Typography
                  component={editing ? "label" : "span"}
                  htmlFor={editing ? inputId : undefined}
                  typography="s1"
                  fontWeight={500}
                  color="text.primary"
                >
                  {row.label}
                </Typography>
                {edited && <ToneChip tone="info" label="Edited in review" />}
              </Box>
              <Box
                component="dd"
                sx={{
                  m: 0,
                  minWidth: 0,
                  display: "flex",
                  flexDirection: "column",
                  gap: 0.75,
                }}
              >
                {editing ? (
                  <Box
                    sx={{ display: "flex", flexDirection: "column", gap: 1 }}
                  >
                    <TextField
                      id={inputId}
                      size="small"
                      fullWidth
                      multiline
                      minRows={2}
                      value={text}
                      error={gap || Boolean(shape[row.key])}
                      onChange={(e) => set(row.key, e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key !== "Escape") return;
                        e.preventDefault();
                        close(row.key);
                      }}
                      inputRef={(node) => {
                        inputs.current[row.key] = node;
                      }}
                      inputProps={{
                        maxLength: max,
                        "aria-label": `${row.label} disclosure`,
                        "data-testid": `hub-disclosure-${row.key}-input`,
                      }}
                      helperText={`${text.trim().length} / ${max}, at least ${min}`}
                    />
                    <Box sx={{ display: "flex", gap: 1 }}>
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => close(row.key)}
                        aria-label={`Done editing ${row.label}`}
                      >
                        Done
                      </Button>
                      <Button
                        size="small"
                        color="inherit"
                        onClick={() => close(row.key, true)}
                        aria-label={`Cancel editing ${row.label}`}
                      >
                        Cancel
                      </Button>
                    </Box>
                  </Box>
                ) : (
                  <Box
                    sx={{ display: "flex", alignItems: "flex-start", gap: 1 }}
                  >
                    <Typography
                      typography="s1"
                      color={text ? "text.primary" : "text.secondary"}
                      sx={{
                        flex: 1,
                        minWidth: 0,
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                      }}
                    >
                      {text || "Not written."}
                    </Typography>
                    <Button
                      size="small"
                      color="inherit"
                      ref={(node) => {
                        edits.current[row.key] = node;
                      }}
                      onClick={() => edit(row.key)}
                      aria-label={`Edit ${row.label}`}
                      data-testid={`hub-disclosure-${row.key}-edit`}
                      sx={{ flexShrink: 0, color: "text.secondary" }}
                    >
                      Edit
                    </Button>
                  </Box>
                )}
                {gap ? (
                  <Typography typography="s2" color="error.main">
                    The checks observed facts for this part: say what it does.
                  </Typography>
                ) : shape[row.key] ? (
                  <Typography typography="s2" color="error.main">
                    {`Write ${min} to ${max} characters.`}
                  </Typography>
                ) : null}
                {facts.length > 0 && (
                  <Typography
                    typography="s2"
                    color={gap ? "error.main" : "text.secondary"}
                    data-testid={`hub-observed-${row.key}`}
                    sx={{ overflowWrap: "anywhere" }}
                  >
                    {`Observed by the checks: ${groupFacts(facts)}`}
                  </Typography>
                )}
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}

DisclosureEditor.propTypes = {
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  observed: PropTypes.object,
  draft: PropTypes.object,
  label: PropTypes.string,
};
