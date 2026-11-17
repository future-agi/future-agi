import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Divider,
  ListSubheader,
  MenuItem,
  Select,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { kindIcon } from "src/sections/common/Values/kindIcons";
import { coveragePercent } from "src/sections/common/Values/inferSchema";
import {
  EMPTY_MAPPING,
  constantMapping,
  fixedMappingLabel,
  isConstantMapping,
  isEmptyMapping,
  mappingValueLabel,
  suggestedOption,
} from "./variableMapping";

// IT-R4 G1 P1: one variable's column picker. Suggested · Columns · a
// collapsed `Nested paths (n) ▸` group · Leave empty · Same value for
// every row… A variable with no column never blocks.
const NESTED = "__nested__";
const EMPTY = "__empty__";
const CONSTANT = "__constant__";

const sub = { typography: "s3", lineHeight: "28px" };

const coverageWords = (c) =>
  c?.total
    ? `in ${c.present.toLocaleString("en-US")} of ${c.total.toLocaleString("en-US")} rows`
    : null;

function OptionRow({ o, indent = false, showCoverageWords = false }) {
  return (
    <>
      {o.kind ? (
        <Iconify
          icon={kindIcon(o.kind)}
          width={14}
          sx={{ color: "text.secondary", mr: 0.75, flexShrink: 0 }}
        />
      ) : null}
      <Box
        component="span"
        sx={{
          fontFamily: o.path ? "monospace" : undefined,
          fontSize: o.path ? 12 : undefined,
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          pl: indent ? 0 : undefined,
        }}
      >
        {o.label}
      </Box>
      {o.coverage ? (
        <Typography
          component="span"
          typography="s3"
          sx={{
            ml: "auto",
            pl: 1,
            flexShrink: 0,
            color:
              o.coverage.present === o.coverage.total
                ? "text.disabled"
                : "amber.700",
          }}
        >
          {showCoverageWords
            ? `· ${coverageWords(o.coverage)}`
            : coveragePercent(o.coverage)}
        </Typography>
      ) : null}
      {o.role === "expected" ? (
        <Typography
          component="span"
          typography="s3"
          color="text.disabled"
          sx={{ ml: 1 }}
        >
          Expected
        </Typography>
      ) : null}
    </>
  );
}
OptionRow.propTypes = {
  o: PropTypes.object.isRequired,
  indent: PropTypes.bool,
  showCoverageWords: PropTypes.bool,
};

/** The Select value of a mapping (sentinels for empty / constant). */
export const selectValueOf = (value, ok) =>
  isEmptyMapping(value)
    ? EMPTY
    : isConstantMapping(value)
      ? CONSTANT
      : ok && typeof value === "string"
        ? value
        : "";

const VariableColumnSelect = ({
  variable,
  value,
  ok,
  options,
  onChange,
  onConstantStart,
}) => {
  const [open, setOpen] = useState(false);
  const keepOpen = useRef(false);
  const columns = options.filter((o) => !o.path);
  const paths = options.filter((o) => o.path);
  const suggestion = suggestedOption(variable, options);
  const isPathValue =
    typeof value === "string" && paths.some((p) => p.value === value);
  const [showNested, setShowNested] = useState(
    () => isPathValue && suggestion?.value !== value,
  );
  useEffect(() => {
    if (isPathValue && suggestion?.value !== value) setShowNested(true);
  }, [isPathValue, suggestion?.value, value]);

  const selectValue = selectValueOf(value, ok);
  const handle = (e) => {
    const v = e.target.value;
    if (v === NESTED) {
      keepOpen.current = true;
      setShowNested((s) => !s);
      return;
    }
    if (v === EMPTY) onChange({ ...EMPTY_MAPPING });
    else if (v === CONSTANT) {
      onChange(isConstantMapping(value) ? value : constantMapping(""));
      onConstantStart?.();
    } else onChange(v);
  };

  const labelOf = (val) => {
    if (val === EMPTY) return "Leave empty";
    if (val === CONSTANT) return "Same value";
    return mappingValueLabel(val, options);
  };

  return (
    <Select
      size="small"
      fullWidth
      displayEmpty
      open={open}
      onOpen={() => setOpen(true)}
      onClose={() => {
        if (keepOpen.current) {
          keepOpen.current = false;
          return;
        }
        setOpen(false);
      }}
      value={selectValue}
      onChange={handle}
      aria-label={`Column for ${variable}`}
      data-testid={`column-select-${variable}`}
      renderValue={(val) =>
        val ? (
          labelOf(val)
        ) : (
          <Typography typography="s2" color="text.disabled">
            Choose a column
          </Typography>
        )
      }
      sx={{
        height: 28,
        "& .MuiSelect-select": { typography: "s2", py: 0.5 },
      }}
      MenuProps={{
        slotProps: { paper: { sx: { maxHeight: 360, minWidth: 300 } } },
      }}
    >
      {suggestion ? <ListSubheader sx={sub}>Suggested</ListSubheader> : null}
      {suggestion ? (
        <MenuItem
          key={`s:${suggestion.value}`}
          value={suggestion.value}
          dense
          data-testid={`suggested-${variable}`}
          sx={{ pl: 1.5, typography: "s2" }}
        >
          <OptionRow o={suggestion} showCoverageWords />
        </MenuItem>
      ) : null}
      <ListSubheader sx={sub}>Columns</ListSubheader>
      {columns.map((o) => (
        <MenuItem
          key={o.value}
          value={o.value}
          dense
          sx={{ pl: 1.5, typography: "s2" }}
        >
          <OptionRow o={o} />
        </MenuItem>
      ))}
      {paths.length ? (
        <MenuItem
          value={NESTED}
          dense
          aria-expanded={showNested}
          data-testid={`nested-toggle-${variable}`}
          sx={{ pl: 1.5, typography: "s2", color: "text.secondary" }}
        >
          {`Nested paths (${paths.length}) ${showNested ? "▾" : "▸"}`}
        </MenuItem>
      ) : null}
      {showNested
        ? paths.map((o) => (
            <MenuItem
              key={o.value}
              value={o.value}
              dense
              sx={{ pl: 3, typography: "s2" }}
            >
              <OptionRow o={o} indent />
            </MenuItem>
          ))
        : null}
      <Divider sx={{ my: 0.5 }} />
      <MenuItem
        value={EMPTY}
        dense
        data-testid={`leave-empty-${variable}`}
        sx={{ pl: 1.5, typography: "s2" }}
      >
        Leave empty
        <Typography
          component="span"
          typography="s3"
          color="text.secondary"
          sx={{ ml: 0.75 }}
        >
          (every row gets an empty value)
        </Typography>
      </MenuItem>
      <MenuItem
        value={CONSTANT}
        dense
        data-testid={`same-value-${variable}`}
        sx={{ pl: 1.5, typography: "s2" }}
      >
        Same value for every row…
      </MenuItem>
    </Select>
  );
};

VariableColumnSelect.propTypes = {
  variable: PropTypes.string.isRequired,
  value: PropTypes.any,
  ok: PropTypes.bool,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  onConstantStart: PropTypes.func,
};

/**
 * The Value cell of a constant mapping: an inline text field while typing,
 * then `"enterprise" · same for every row` (click to edit).
 */
export function ConstantValueField({ variable, value, onChange, focusKey }) {
  const text = isConstantMapping(value) ? value.value ?? "" : "";
  const [editing, setEditing] = useState(!text || Boolean(focusKey));
  const inputRef = useRef(null);
  // The picker's menu hands focus back to its button as it closes; take it
  // after that, so typing goes into the field.
  useEffect(() => {
    if (!focusKey) return undefined;
    setEditing(true);
    const t = setTimeout(() => inputRef.current?.focus(), 250);
    return () => clearTimeout(t);
  }, [focusKey]);
  if (!editing && text)
    return (
      <Typography
        component="button"
        type="button"
        typography="s2"
        onClick={() => setEditing(true)}
        title="Edit the value"
        data-testid={`constant-label-${variable}`}
        sx={{
          border: 0,
          bgcolor: "transparent",
          p: 0,
          cursor: "text",
          color: "text.primary",
          textAlign: "left",
          maxWidth: "100%",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          fontFamily: "inherit",
        }}
      >
        {fixedMappingLabel(value)}
      </Typography>
    );
  return (
    <TextField
      size="small"
      fullWidth
      inputRef={inputRef}
      value={text}
      placeholder="Type the value every row gets"
      onChange={(e) => onChange(constantMapping(e.target.value))}
      onBlur={() => text && setEditing(false)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          if (text) setEditing(false);
        }
      }}
      inputProps={{
        "aria-label": `Value of ${variable} for every row`,
        "data-testid": `constant-input-${variable}`,
      }}
      sx={{ "& .MuiInputBase-input": { typography: "s2", py: 0.5 } }}
    />
  );
}
ConstantValueField.propTypes = {
  variable: PropTypes.string.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  focusKey: PropTypes.number,
};

export default VariableColumnSelect;
