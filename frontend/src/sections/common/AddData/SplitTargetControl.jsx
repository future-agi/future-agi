import React from "react";
import PropTypes from "prop-types";
import { MenuItem, Stack, TextField, Typography } from "@mui/material";
import Term from "src/sections/common/ux/Term";

const NEW = "__new_split__";
const MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split(" ");
export const SPLIT_NAME_RE = /^[a-z0-9][a-z0-9._-]{0,29}$/;

/**
 * `prod-failures-oct-18`: today in UTC, the same day the mock's
 * `Experiment #2 · Oct 18` suggestion uses; `-2`, `-3` when taken (pure).
 */
export function newSplitName(splits = [], now = Date.now()) {
  const d = new Date(now);
  const stem = `prod-failures-${MONTHS[d.getUTCMonth()]}-${d.getUTCDate()}`;
  const taken = new Set((splits ?? []).map((s) => s.name));
  let name = stem;
  for (let i = 2; taken.has(name) && i < 100; i += 1) name = `${stem}-${i}`;
  return name;
}

/** Why a new split name cannot be used, or null (pure). */
export function newSplitProblem(name, splits = []) {
  const n = String(name ?? "").trim();
  if (!n) return "Name the split.";
  if (!SPLIT_NAME_RE.test(n))
    return "Lowercase letters, digits, dots, dashes or underscores (max 30).";
  if ((splits ?? []).some((s) => s.name === n))
    return `“${n}” already exists. Pick it in the list instead.`;
  return null;
}

/**
 * IT-R1 G5 P2/P4: `Put in a split` as a labelled field in the import sheet
 * (was under More ▾), with `+ New split…`. A new split is created when the
 * rows are added, so a cancelled import leaves nothing behind.
 * `value`: an existing split id, `{newName}` for a new one, or null.
 */
const SplitTargetControl = ({ splits = [], value, onChange, disabled }) => {
  const isNew = value != null && typeof value === "object";
  const problem = isNew ? newSplitProblem(value.newName, splits) : null;
  return (
    <Stack spacing={0.75}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ flexShrink: 0, width: 128 }}
          component="label"
          htmlFor="add-data-split"
        >
          Put in a <Term k="splits">split</Term>
        </Typography>
        <TextField
          id="add-data-split"
          select
          size="small"
          fullWidth
          disabled={disabled}
          value={isNew ? NEW : value ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            if (v === NEW) onChange({ newName: newSplitName(splits) });
            else onChange(v || null);
          }}
          SelectProps={{ displayEmpty: true }}
          sx={{ "& .MuiSelect-select": { typography: "s2", py: 0.75 } }}
          inputProps={{ "data-testid": "add-data-split" }}
        >
          <MenuItem value="" sx={{ typography: "s2" }}>
            No split
          </MenuItem>
          {(splits ?? []).map((s) => (
            <MenuItem key={s.id} value={s.id} sx={{ typography: "s2" }}>
              {s.name}
            </MenuItem>
          ))}
          <MenuItem
            value={NEW}
            sx={{ typography: "s2", color: "primary.main" }}
            data-testid="add-data-new-split"
          >
            + New split…
          </MenuItem>
        </TextField>
      </Stack>
      {isNew ? (
        <TextField
          size="small"
          autoFocus
          label="New split name"
          value={value.newName}
          onChange={(e) => onChange({ newName: e.target.value })}
          error={Boolean(problem)}
          helperText={
            problem ?? "Created when you add the rows. Rename it any time."
          }
          inputProps={{
            "data-testid": "add-data-new-split-name",
            spellCheck: false,
          }}
          sx={{
            ml: "136px !important",
            "& input": { typography: "s2", fontFamily: "monospace" },
          }}
        />
      ) : null}
    </Stack>
  );
};

SplitTargetControl.propTypes = {
  splits: PropTypes.array,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

export default SplitTargetControl;
