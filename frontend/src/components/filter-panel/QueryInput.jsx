/* eslint-disable react/prop-types */
/**
 * QueryInput — the token-based Query tab builder used by the shared Filter
 * panel (TraceFilterPanel). Fields are `{ value, label, type, choices }`; a
 * field may carry `choiceLabels: {value: label}` when its `choices` are opaque
 * keys (enum values, UUIDs) that should never be shown to the user.
 */
import {
  Radio,
  Checkbox,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  InputAdornment,
  MenuItem,
  Popover,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import React, {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import Iconify from "src/components/iconify";

const STRING_OPERATORS = [
  { value: "contains", label: "Contains" },
  { value: "equals", label: "Equals" },
  { value: "not_equals", label: "Not equals" },
  { value: "starts_with", label: "Starts with" },
  { value: "not_contains", label: "Does not contain" },
];

const ENUM_OPERATORS = [
  { value: "is", label: "Is" },
  { value: "is_not", label: "Is not" },
];

function getOperators(fieldDef) {
  const fieldType = typeof fieldDef === "string" ? fieldDef : fieldDef?.type;
  const base = fieldType === "enum" ? ENUM_OPERATORS : STRING_OPERATORS;
  const allowed =
    typeof fieldDef === "object" && Array.isArray(fieldDef?.operators)
      ? fieldDef.operators
      : null;
  return allowed ? base.filter((op) => allowed.includes(op.value)) : base;
}

// Checkbox multi-select popover (matches trace filter design).
function EnumValuePicker({
  choices,
  value = [],
  onChange,
  single = false,
  choiceLabels,
}) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");

  // `choices` holds the values sent to the API. When a field supplies
  // `choiceLabels`, those raw values are opaque to the user (enum keys, UUIDs)
  // and only the mapped label is ever shown or searched.
  const labelOf = useCallback(
    (choice) => choiceLabels?.[choice] ?? choice,
    [choiceLabels],
  );

  const filtered = useMemo(() => {
    if (!search) return choices;
    const q = search.toLowerCase();
    return choices.filter((c) => labelOf(c).toLowerCase().includes(q));
  }, [choices, search, labelOf]);

  const toggle = useCallback(
    (val) => {
      if (single) {
        onChange(value.includes(val) ? [] : [val]);
        setAnchorEl(null);
        setSearch("");
        return;
      }
      onChange(
        value.includes(val) ? value.filter((v) => v !== val) : [...value, val],
      );
    },
    [value, onChange, single],
  );

  return (
    <>
      <Box
        onClick={(e) => setAnchorEl(e.currentTarget)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          flexWrap: "wrap",
          minHeight: 30,
          minWidth: 130,
          flex: 1,
          maxWidth: 250,
          px: 1,
          py: 0.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
          cursor: "pointer",
          "&:hover": { borderColor: "text.disabled" },
        }}
      >
        {value.length === 0 ? (
          <Typography sx={{ fontSize: 13, color: "text.disabled", flex: 1 }}>
            Select values...
          </Typography>
        ) : (
          <>
            {value.slice(0, 2).map((v) => (
              <Chip
                key={v}
                label={labelOf(v)}
                size="small"
                onDelete={(e) => {
                  e.stopPropagation();
                  onChange(value.filter((x) => x !== v));
                }}
                deleteIcon={<Iconify icon="solar:close-linear" width={12} />}
                sx={{
                  maxWidth: 80,
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />
            ))}
            {value.length > 2 && (
              <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
                +{value.length - 2}
              </Typography>
            )}
          </>
        )}
        <Iconify
          icon={
            anchorEl
              ? "solar:alt-arrow-up-linear"
              : "solar:alt-arrow-down-linear"
          }
          width={14}
          sx={{ color: "text.disabled", ml: "auto", flexShrink: 0 }}
        />
      </Box>

      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={() => {
          setAnchorEl(null);
          setSearch("");
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: { sx: { width: 320, borderRadius: "8px", mt: 0.5 } },
        }}
      >
        <Box sx={{ p: 1 }}>
          <TextField
            size="small"
            fullWidth
            placeholder="Search values..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="solar:magnifier-linear"
                    width={14}
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
              sx: { fontSize: 12, height: 30 },
            }}
          />
          <Typography
            sx={{ fontSize: 12, color: "text.disabled", mt: 0.5, px: 0.25 }}
          >
            {single
              ? "Select a value"
              : "Select one or more values (multi-select)"}
          </Typography>
        </Box>
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            maxHeight: 280,
            overflow: "auto",
          }}
        >
          {/* Select all matching */}
          {!single && search && filtered.length > 0 && (
            <Box
              onClick={() => {
                const allFiltered = filtered.filter((o) => !value.includes(o));
                if (allFiltered.length > 0)
                  onChange([...value, ...allFiltered]);
                else onChange(value.filter((v) => !filtered.includes(v)));
              }}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                px: 1.5,
                py: 0.75,
                cursor: "pointer",
                bgcolor: "action.hover",
                borderBottom: "1px solid",
                borderColor: "divider",
                "&:hover": { bgcolor: "action.selected" },
              }}
            >
              <Checkbox
                size="small"
                checked={filtered.every((o) => value.includes(o))}
                tabIndex={-1}
                disableRipple
                sx={{ p: 0, flexShrink: 0 }}
              />
              <Typography
                sx={{ fontSize: 13, color: "text.primary", fontWeight: 500 }}
              >
                Select all matching in list ({filtered.length})
              </Typography>
            </Box>
          )}

          {/* Specify custom value. Suppressed for label-mapped fields: there the
              choices are opaque keys, so a typed string is never a valid value. */}
          {search && !choiceLabels && !choices.includes(search) && (
            <Box
              onClick={() => {
                if (!value.includes(search)) {
                  onChange([...value, search]);
                  setSearch("");
                }
              }}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                px: 1.5,
                py: 0.75,
                cursor: "pointer",
                borderBottom: "1px solid",
                borderColor: "divider",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Checkbox
                size="small"
                checked={value.includes(search)}
                tabIndex={-1}
                disableRipple
                inputProps={{ "aria-hidden": true }}
                sx={{ p: 0, flexShrink: 0 }}
              />
              <Typography sx={{ fontSize: 12 }}>
                Specify: <strong>{search}</strong>
              </Typography>
            </Box>
          )}

          {filtered.length === 0 && !search && (
            <Typography
              sx={{
                p: 1.5,
                textAlign: "center",
                fontSize: 12,
                color: "text.disabled",
              }}
            >
              No values found
            </Typography>
          )}
          {filtered.map((opt) => {
            const isSelected = value.includes(opt);
            return (
              <Box
                key={opt}
                onClick={() => toggle(opt)}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  px: 1.5,
                  py: 0.75,
                  cursor: "pointer",
                  bgcolor: isSelected ? "action.selected" : "transparent",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                {single ? (
                  <Radio
                    size="small"
                    checked={isSelected}
                    tabIndex={-1}
                    disableRipple
                    inputProps={{ "aria-hidden": true }}
                    sx={{ p: 0, flexShrink: 0 }}
                  />
                ) : (
                  <Checkbox
                    size="small"
                    checked={isSelected}
                    tabIndex={-1}
                    disableRipple
                    inputProps={{ "aria-hidden": true }}
                    sx={{ p: 0, flexShrink: 0 }}
                  />
                )}
                <Typography
                  noWrap
                  sx={{
                    fontSize: 12,
                    flex: 1,
                    fontWeight: isSelected ? 600 : 400,
                  }}
                >
                  {labelOf(opt)}
                </Typography>
              </Box>
            );
          })}
        </Box>
        {value.length > 0 && (
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              px: 1.5,
              py: 0.75,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
              {value.length} selected
            </Typography>
            <Button
              size="small"
              onClick={() => onChange([])}
              sx={{
                minWidth: 0,
                color: "text.secondary",
              }}
            >
              Clear
            </Button>
          </Box>
        )}
      </Popover>
    </>
  );
}

function FilterRow({
  filter,
  index,
  filterFields,
  fieldMap,
  onChange,
  onRemove,
  takenSingleFields,
}) {
  const fieldDef = fieldMap[filter.field] || filterFields[0];
  const operators = getOperators(fieldDef);

  return (
    <Stack direction="row" alignItems="center" gap={0.5}>
      <Select
        size="small"
        value={filter.field}
        onChange={(e) => {
          const newField = fieldMap[e.target.value];
          onChange(index, {
            field: e.target.value,
            operator: newField?.type === "enum" ? "is" : "contains",
            value: newField?.type === "enum" ? [] : "",
          });
        }}
        sx={{ minWidth: 100, fontSize: 13, height: 30 }}
      >
        {filterFields.map((f) => (
          <MenuItem
            key={f.value}
            value={f.value}
            disabled={
              f.value !== filter.field && takenSingleFields?.has(f.value)
            }
          >
            {f.label}
          </MenuItem>
        ))}
      </Select>

      <Select
        size="small"
        value={filter.operator}
        onChange={(e) =>
          onChange(index, { ...filter, operator: e.target.value })
        }
        sx={{ minWidth: 110, fontSize: 13, height: 30 }}
      >
        {operators.map((op) => (
          <MenuItem key={op.value} value={op.value}>
            {op.label}
          </MenuItem>
        ))}
      </Select>

      {fieldDef.type === "enum" ? (
        <EnumValuePicker
          choices={fieldDef.choices || []}
          choiceLabels={fieldDef.choiceLabels}
          single={fieldDef.single}
          value={
            Array.isArray(filter.value)
              ? filter.value
              : filter.value
                ? [filter.value]
                : []
          }
          onChange={(newVal) => onChange(index, { ...filter, value: newVal })}
        />
      ) : (
        <TextField
          size="small"
          placeholder="Enter value"
          value={filter.value}
          onChange={(e) =>
            onChange(index, { ...filter, value: e.target.value })
          }
          sx={{
            minWidth: 100,
            "& .MuiInputBase-root": { fontSize: 13, height: 30 },
          }}
        />
      )}

      <IconButton size="small" onClick={() => onRemove(index)}>
        <Iconify icon="solar:close-linear" width={14} />
      </IconButton>
    </Stack>
  );
}

FilterRow.propTypes = {
  filter: PropTypes.object.isRequired,
  index: PropTypes.number.isRequired,
  filterFields: PropTypes.array.isRequired,
  fieldMap: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};

/**
 * @param {Array} filterFields — field definitions
 * @param {Object} fieldMap — { fieldValue: fieldDef }
 * @param {Function} onApply — called with array of tokens
 * @param {Array} initialTokens
 * @param {Array} [valueOptions] — dynamic value options for the current field (from parent)
 * @param {string} [activeField] — notifies parent which field is selected (for value fetching)
 * @param {Function} [onFieldChange] — called when field changes (so parent can fetch values)
 */
// Field-type families used to pick the right HTML input type when a range op
// (between / not_between) is active. Matches TraceFilterPanel's NUMERIC_TYPES /
// DATE_TYPES sets — duplicated here so QueryInput stays self-contained.
const RANGE_NUMERIC_TYPES = new Set([
  "number",
  "float",
  "integer",
  "int",
  "decimal",
  "double",
  "numeric",
  "long",
]);
const RANGE_DATE_TYPES = new Set(["date", "datetime", "timestamp"]);
// Numeric ranges stay type="text": type="number" swallows invalid keystrokes
// with no feedback (same rationale as the Basic-tab inputs, TH-5195).
// Date fields edit a local date + time, like the Basic tab: a date-only box
// can't show a stored 'yyyy-MM-ddTHH:mm' value, and a bare day is later read
// as UTC midnight.
const rangeInputTypeFor = (fieldType) => {
  if (RANGE_DATE_TYPES.has(fieldType)) return "datetime-local";
  return "text";
};

// A day without a time ('yyyy-MM-dd') opens as that whole day: 00:00, or 23:59
// when it is a range's upper bound.
const DATE_ONLY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const toDateTimeInputValue = (value, { endOfDay = false } = {}) => {
  const str = value === null || value === undefined ? "" : String(value).trim();
  if (!DATE_ONLY_PATTERN.test(str)) return str;
  return `${str}T${endOfDay ? "23:59" : "00:00"}`;
};

// A date + time input reads "" while it is only partly filled in; Backspace
// there edits the input instead of leaving the editor.
const isClearedInput = (target) =>
  target.value === "" && !target.validity?.badInput;

const isValidRangeNumericInput = (v) =>
  v === "" || /^-?\d*\.?\d*$/.test(String(v).trim());

// Complete (fully-typed) number. Unlike the partial check above, this rejects
// mid-type states ("-", ".", "1.5.6"); used to gate commit/flush so a partial
// value never becomes an applied token (TH-5195). Empty passes — an empty
// bound is handled by the non-empty checks, not this.
const isCompleteNumericInput = (v) => {
  const str = String(v ?? "").trim();
  if (str === "") return true;
  if (!/^-?(\d+\.?\d*|\.\d+)$/.test(str)) return false;
  return Number.isFinite(parseFloat(str));
};

const QUERY_PAGINATION_THRESHOLD_PX = 40;

// Inline editors that replace the query box while a range or a date value is
// being entered.
const INLINE_EDITOR_SX = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: 0.5,
  width: "100%",
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  px: 1.5,
  py: 0.5,
  minHeight: 40,
  "&:focus-within": { borderColor: "primary.main" },
};
const INLINE_FIELD_SX = {
  flex: 1,
  minWidth: 100,
  "& .MuiOutlinedInput-root": { height: 28, fontSize: 12 },
  "& .MuiInputBase-input": { p: "4px 8px" },
};

// Category chips read "Scenario", not "scenario"; the chip itself can't restyle text.
const sentenceCase = (str) => str.charAt(0).toUpperCase() + str.slice(1);

// A value as the user picked it: the label of its `{ value, label }` choice or
// its `choiceLabels` entry, else the value itself.
const choiceLabelFor = (fieldDef, value) => {
  const choice = Array.isArray(fieldDef?.choices)
    ? fieldDef.choices.find(
        (c) =>
          c !== null && typeof c === "object" && (c.value ?? c.label) === value,
      )
    : undefined;
  if (choice) return String(choice.label ?? choice.value ?? "");
  return String(fieldDef?.choiceLabels?.[value] ?? value);
};

// While a chip holding several values is edited the box reads "a, b"; the
// value being typed is the text after the last comma.
const lastListPart = (text) => text.slice(text.lastIndexOf(",") + 1).trim();

// One value of that list as the box shows it: a text box drops line breaks.
const listItemText = (value) => String(value ?? "").replace(/[\r\n]/g, "");

const QueryInput = forwardRef(function QueryInput(
  {
    filterFields,
    fieldMap,
    onApply,
    initialTokens = [],
    valueOptions = [],
    valueLoading = false,
    valueLoadingMore = false,
    valueLoadError = false,
    fieldLoading = false,
    fieldLoadingMore = false,
    fieldLoadError = false,
    onFieldChange,
    onFieldSearchChange,
    onLoadMoreFields,
    onValueSearchChange,
    onLoadMoreValues,
    hasMoreFields = false,
    hasMoreValues = false,
    getOperators: getOperatorsProp,
  },
  ref,
) {
  const [tokens, setTokens] = useState(initialTokens);
  const [partialField, setPartialField] = useState(null);
  const [partialOp, setPartialOp] = useState(null);
  const [partialValueTypes, setPartialValueTypes] = useState([]);
  const [partialOriginalValue, setPartialOriginalValue] = useState(undefined);
  const [inputValue, setInputValue] = useState("");
  const [rangeFrom, setRangeFrom] = useState("");
  const [rangeTo, setRangeTo] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const inputRef = useRef(null);
  const rangeErrorId = useId();
  const bottomEdgeLatchedRef = useRef(false);
  const paginationRequestRef = useRef(null);
  const initialTokensKey = useMemo(
    () => JSON.stringify(initialTokens || []),
    [initialTokens],
  );

  useEffect(() => {
    let parsedTokens = [];
    try {
      const parsed = JSON.parse(initialTokensKey);
      parsedTokens = Array.isArray(parsed) ? parsed : [];
    } catch (_e) {
      parsedTokens = [];
    }
    setTokens(parsedTokens);
    setPartialField(null);
    setPartialOp(null);
    setPartialValueTypes([]);
    setPartialOriginalValue(undefined);
    setInputValue("");
    setRangeFrom("");
    setRangeTo("");
  }, [initialTokensKey]);

  const phase = !partialField ? "field" : !partialOp ? "operator" : "value";

  useEffect(() => {
    // A field/operator/search transition is a new list and may start a new
    // explicit scroll gesture. Page arrivals alone do not touch this latch:
    // browsers often emit more momentum/resize events while still pinned to
    // the bottom, and those events must not drain the remaining cursor chain.
    bottomEdgeLatchedRef.current = false;
  }, [phase, partialField, inputValue]);

  useEffect(() => {
    if (!dropdownOpen) bottomEdgeLatchedRef.current = false;
  }, [dropdownOpen]);

  // Resolve the picked operator's definition (so we can read `range: true`).
  const currentOpDef = useMemo(() => {
    if (!partialField || !partialOp) return null;
    const fd = fieldMap[partialField];
    const ops = getOperatorsProp
      ? getOperatorsProp(fd?.type || "string", partialField)
      : getOperators(fd || "string");
    return ops.find((o) => o.value === partialOp);
  }, [partialField, partialOp, fieldMap, getOperatorsProp]);

  const isRangePhase = phase === "value" && Boolean(currentOpDef?.range);
  const rangeInputType = isRangePhase
    ? rangeInputTypeFor(fieldMap[partialField]?.type)
    : "text";
  const isNumericRange =
    isRangePhase && RANGE_NUMERIC_TYPES.has(fieldMap[partialField]?.type);
  const isNumericScalar =
    phase === "value" &&
    !isRangePhase &&
    RANGE_NUMERIC_TYPES.has(fieldMap[partialField]?.type);
  // after / before / on for a date field: one date + time picker, not text.
  const isDateScalar =
    phase === "value" &&
    !isRangePhase &&
    RANGE_DATE_TYPES.has(fieldMap[partialField]?.type);
  const rangeFromInvalid =
    isNumericRange && !isValidRangeNumericInput(rangeFrom);
  const rangeToInvalid = isNumericRange && !isValidRangeNumericInput(rangeTo);
  const hasStaticValueChoices = Boolean(
    phase === "value" && fieldMap[partialField]?.choices?.length,
  );
  const allowsFreeTextValue = Boolean(
    phase === "value" &&
      (!hasStaticValueChoices ||
        fieldMap[partialField]?.allowCustomValue === true),
  );
  const valueFreeSolo =
    phase === "value" && !isRangePhase && allowsFreeTextValue;
  // Editing a chip that holds several values keeps it a list: Enter and a close
  // apply a list, and suggestions follow the value being typed. One value that
  // contains a comma is never split.
  const editingList =
    phase === "value" &&
    !isRangePhase &&
    Array.isArray(partialOriginalValue) &&
    partialOriginalValue.length > 1;
  const searchText = editingList ? lastListPart(inputValue) : inputValue;

  const options = useMemo(() => {
    if (phase === "field")
      return filterFields.map((f) => ({
        id: f.value,
        label: f.label,
        type: "field",
      }));
    if (phase === "operator") {
      const fd = fieldMap[partialField];
      const ops = getOperatorsProp
        ? getOperatorsProp(fd?.type || "string", partialField)
        : getOperators(fd || "string");
      return ops.map((o) => ({
        id: o.value,
        label: o.label,
        type: "operator",
      }));
    }
    if (phase === "value") {
      const fd = fieldMap[partialField];
      // Date values come from the date + time picker, never sampled values.
      if (RANGE_DATE_TYPES.has(fd?.type)) return [];
      // Static choices: gate on choices presence so categorical / thumbs /
      // annotator fields (which carry their real type tag) also pick up.
      // Dynamic values from parent (fetched from CH)
      if (valueOptions.length > 0) {
        return valueOptions.map((o) => {
          const val = typeof o === "string" ? o : o.value ?? o.label;
          const label = typeof o === "string" ? o : o.label ?? o.value;
          return {
            id: val,
            label: String(label ?? ""),
            type: "value",
            storageType: typeof o === "object" ? o.type : undefined,
          };
        });
      }
      if (fd?.choices?.length) {
        return fd.choices.map((choice) => {
          const value =
            typeof choice === "object" ? choice.value ?? choice.label : choice;
          const label =
            typeof choice === "object"
              ? choice.label ?? choice.value
              : fd.choiceLabels?.[choice] ?? choice;
          return { id: value, label: String(label ?? ""), type: "value" };
        });
      }
    }
    return [];
  }, [
    phase,
    partialField,
    filterFields,
    fieldMap,
    valueOptions,
    getOperatorsProp,
  ]);

  const filtered = useMemo(
    () =>
      searchText
        ? options.filter((option) => {
            const query = searchText.toLowerCase();
            const candidates =
              phase === "value" ? [option.id, option.label] : [option.label];
            return candidates.some((candidate) =>
              String(candidate ?? "")
                .toLowerCase()
                .includes(query),
            );
          })
        : options,
    [searchText, options, phase],
  );

  const exactOptionFor = useCallback(
    (text) => {
      const candidate = String(text ?? "")
        .trim()
        .toLowerCase();
      if (!candidate) return null;
      return (
        options.find((option) =>
          [option.id, option.label].some(
            (value) =>
              String(value ?? "")
                .trim()
                .toLowerCase() === candidate,
          ),
        ) ?? null
      );
    },
    [options],
  );
  const exactInputOption = useMemo(
    () => exactOptionFor(inputValue),
    [exactOptionFor, inputValue],
  );

  const partialOriginalDisplayValue = useMemo(
    () =>
      Array.isArray(partialOriginalValue)
        ? partialOriginalValue.join(", ")
        : partialOriginalValue === null || partialOriginalValue === undefined
          ? ""
          : String(partialOriginalValue),
    [partialOriginalValue],
  );
  const originalInputUnchanged =
    partialOriginalValue !== undefined &&
    partialOriginalDisplayValue === inputValue.trim();

  // Reads list text back as values. The chip's values that the edit left
  // whole at the start or the end of the text are kept exactly as they were,
  // each with its own type, even when one holds a comma or spaces. Only the
  // edited stretch is split at commas: each part takes back an edited value
  // it still names (each value once), else the option it names, else its own
  // text where free text is allowed. Null while any part names nothing usable.
  const resolveList = useCallback(
    (text) => {
      const originals = Array.isArray(partialOriginalValue)
        ? partialOriginalValue
        : [];
      const texts = originals.map(listItemText);
      const count = texts.length;
      // Leading values still followed by a comma (or the end of the text).
      let head = 0;
      let headEnd = 0;
      while (head < count) {
        const joined = texts.slice(0, head + 1).join(", ");
        const next = text.charAt(joined.length);
        if (!text.startsWith(joined) || (next !== "" && next !== ",")) break;
        head += 1;
        headEnd = joined.length;
      }
      // Trailing values still led by ", " (or the start of the text), clear of
      // the leading ones.
      let tail = 0;
      let tailStart = text.length;
      while (head + tail < count) {
        const joined = texts.slice(count - tail - 1).join(", ");
        const start = text.length - joined.length;
        const led =
          start === 0
            ? head === 0
            : start - 2 >= headEnd && text.slice(start - 2, start) === ", ";
        if (start < 0 || !text.endsWith(joined) || !led) break;
        tail += 1;
        tailStart = start;
      }
      const value = [];
      const valueTypes = [];
      const keep = (index) => {
        value.push(originals[index]);
        valueTypes.push(partialValueTypes[index]);
      };
      for (let index = 0; index < head; index += 1) keep(index);
      const edited = [];
      for (let index = head; index < count - tail; index += 1) {
        edited.push(index);
      }
      const middle = text.slice(
        head > 0 ? headEnd + 1 : 0,
        tail > 0 ? Math.max(tailStart - 2, 0) : text.length,
      );
      for (const part of middle.split(",")) {
        const typed = part.trim();
        if (!typed) continue;
        const claimed = edited.findIndex(
          (index) => texts[index].trim() === typed,
        );
        const option = claimed === -1 ? exactOptionFor(typed) : null;
        if (claimed !== -1) {
          keep(edited.splice(claimed, 1)[0]);
        } else if (option) {
          value.push(option.id);
          valueTypes.push(option.storageType);
        } else if (
          allowsFreeTextValue &&
          (!isNumericScalar || isCompleteNumericInput(typed))
        ) {
          value.push(typed);
          valueTypes.push(undefined);
        } else {
          return null;
        }
      }
      for (let index = count - tail; index < count; index += 1) keep(index);
      return { value, valueTypes };
    },
    [
      partialOriginalValue,
      partialValueTypes,
      exactOptionFor,
      allowsFreeTextValue,
      isNumericScalar,
    ],
  );

  // What Enter or a close applies from the query box, or null while the text
  // names nothing usable: a fixed-choice field takes listed values only, and a
  // partial number never applies (TH-5195).
  const typedDraft = useMemo(() => {
    if (phase !== "value" || isRangePhase) return null;
    if (editingList) {
      if (originalInputUnchanged) {
        return { value: partialOriginalValue, valueTypes: partialValueTypes };
      }
      const list = resolveList(inputValue);
      return list && list.value.length > 0 ? list : null;
    }
    const typed = inputValue.trim();
    if (!typed) return null;
    if (!allowsFreeTextValue && !exactInputOption) return null;
    if (isNumericScalar && !isCompleteNumericInput(typed)) return null;
    return {
      value: exactInputOption
        ? exactInputOption.id
        : originalInputUnchanged
          ? partialOriginalValue
          : typed,
      valueTypes: exactInputOption?.storageType
        ? [exactInputOption.storageType]
        : originalInputUnchanged
          ? partialValueTypes
          : undefined,
    };
  }, [
    phase,
    isRangePhase,
    editingList,
    originalInputUnchanged,
    partialOriginalValue,
    partialValueTypes,
    resolveList,
    inputValue,
    allowsFreeTextValue,
    exactInputOption,
    isNumericScalar,
  ]);

  const commitFilter = useCallback(
    (field, op, value, valueTypes) => {
      const token = { field, operator: op, value };
      if (Array.isArray(valueTypes) && valueTypes.some(Boolean)) {
        token.valueTypes = valueTypes;
      }
      const updated = [...tokens, token];
      setTokens(updated);
      setPartialField(null);
      setPartialOp(null);
      setPartialValueTypes([]);
      setPartialOriginalValue(undefined);
      setInputValue("");
      setRangeFrom("");
      setRangeTo("");
      onValueSearchChange?.("", field);
      setTimeout(() => {
        inputRef.current?.focus();
        setDropdownOpen(true);
      }, 0);
      onApply(updated);
    },
    [tokens, onApply, onValueSearchChange],
  );

  const commitRange = useCallback(() => {
    if (rangeFrom === "" || rangeTo === "") return;
    if (rangeFromInvalid || rangeToInvalid) return;
    commitFilter(partialField, partialOp, [rangeFrom, rangeTo]);
  }, [
    rangeFrom,
    rangeTo,
    rangeFromInvalid,
    rangeToInvalid,
    partialField,
    partialOp,
    commitFilter,
  ]);

  // Imperative API used by the parent (TraceFilterPanel etc.) when Apply
  // is clicked while a complete partial token is still pending. Without
  // this the user types `field op value` + Apply and the filter silently
  // drops because the token was never committed.
  useImperativeHandle(
    ref,
    () => ({
      // Returns the newly-committed tokens when a complete partial was
      // flushed, otherwise null. Caller uses the return value to rebuild
      // rows directly; no inner onApply needed since the parent panel
      // closes right after Apply.
      flushPartial: () => {
        if (!partialField || !partialOp) return null;
        if (isRangePhase) {
          if (rangeFrom === "" || rangeTo === "") return null;
          if (rangeFromInvalid || rangeToInvalid) return null;
          // partial-regex above still allows "-"/"." — require complete numbers
          if (
            isNumericRange &&
            (!isCompleteNumericInput(rangeFrom) ||
              !isCompleteNumericInput(rangeTo))
          )
            return null;
          const updated = [
            ...tokens,
            {
              field: partialField,
              operator: partialOp,
              value: [rangeFrom, rangeTo],
            },
          ];
          setTokens(updated);
          setPartialField(null);
          setPartialOp(null);
          setPartialValueTypes([]);
          setPartialOriginalValue(undefined);
          setInputValue("");
          setRangeFrom("");
          setRangeTo("");
          return updated;
        }
        // Nothing usable typed (or a partial number, TH-5195): not applied.
        if (!typedDraft) return null;
        const token = {
          field: partialField,
          operator: partialOp,
          value: typedDraft.value,
        };
        if (
          Array.isArray(typedDraft.valueTypes) &&
          typedDraft.valueTypes.some(Boolean)
        ) {
          token.valueTypes = typedDraft.valueTypes;
        }
        const updated = [...tokens, token];
        setTokens(updated);
        setPartialField(null);
        setPartialOp(null);
        setPartialValueTypes([]);
        setPartialOriginalValue(undefined);
        setInputValue("");
        return updated;
      },
    }),
    [
      tokens,
      partialField,
      partialOp,
      isRangePhase,
      rangeFrom,
      rangeTo,
      rangeFromInvalid,
      rangeToInvalid,
      isNumericRange,
      typedDraft,
    ],
  );

  const reopenDropdown = useCallback(() => {
    setTimeout(() => setDropdownOpen(true), 0);
  }, []);

  const opDefFor = useCallback(
    (field, op) => {
      const fd = fieldMap[field];
      const ops = getOperatorsProp
        ? getOperatorsProp(fd?.type || "string", field)
        : getOperators(fd || "string");
      return ops.find((o) => o.value === op);
    },
    [fieldMap, getOperatorsProp],
  );

  const handleSelect = useCallback(
    (_, option) => {
      if (!option) return;
      if (typeof option === "string") {
        const explicitValue = option.trim();
        // A list edit is applied by Enter as a list, never as one literal.
        if (
          editingList ||
          phase !== "value" ||
          !allowsFreeTextValue ||
          !explicitValue ||
          (isNumericScalar && !isCompleteNumericInput(explicitValue))
        ) {
          return;
        }
        commitFilter(partialField, partialOp, explicitValue);
        return;
      }
      if (phase === "field") {
        setPartialField(option.id);
        setPartialValueTypes([]);
        setPartialOriginalValue(undefined);
        setInputValue("");
        onFieldChange?.(option.id);
        reopenDropdown();
      } else if (phase === "operator") {
        if (opDefFor(partialField, option.id)?.noValue) {
          commitFilter(partialField, option.id, "");
          return;
        }
        setPartialOp(option.id);
        setPartialValueTypes([]);
        setPartialOriginalValue(undefined);
        setInputValue("");
        setRangeFrom("");
        setRangeTo("");
        reopenDropdown();
      } else if (phase === "value" && editingList) {
        // A picked suggestion takes the place of the value being typed and the
        // list is applied; if an earlier part names nothing usable, the pick
        // only fills in the text so that part can be fixed.
        const headText = inputValue.slice(0, inputValue.lastIndexOf(",") + 1);
        const head = resolveList(headText);
        if (!head) {
          setInputValue(`${headText}${headText ? " " : ""}${option.id}`);
          return;
        }
        const listed = head.value.includes(option.id);
        commitFilter(
          partialField,
          partialOp,
          listed ? head.value : [...head.value, option.id],
          listed ? head.valueTypes : [...head.valueTypes, option.storageType],
        );
      } else if (phase === "value") {
        commitFilter(
          partialField,
          partialOp,
          option.id,
          option.storageType ? [option.storageType] : undefined,
        );
      }
    },
    [
      phase,
      partialField,
      partialOp,
      commitFilter,
      reopenDropdown,
      onFieldChange,
      opDefFor,
      hasStaticValueChoices,
      allowsFreeTextValue,
      isNumericScalar,
      editingList,
      inputValue,
      resolveList,
    ],
  );

  const editToken = useCallback(
    (index) => {
      const token = tokens[index];
      const updated = tokens.filter((_, i) => i !== index);
      setTokens(updated);
      setPartialField(token.field);
      setPartialOriginalValue(token.value);
      const tokenValueTypes = Array.isArray(token.valueTypes)
        ? token.valueTypes
        : [];
      setPartialValueTypes(tokenValueTypes);
      onFieldChange?.(token.field);
      if (opDefFor(token.field, token.operator)?.noValue) {
        // No-value op: re-pick operator, no value phase.
        setPartialOp(null);
        setRangeFrom("");
        setRangeTo("");
        setInputValue("");
      } else {
        setPartialOp(token.operator);
        const isDateToken = RANGE_DATE_TYPES.has(fieldMap[token.field]?.type);
        // Key off the operator, not the array shape — in/not_in and
        // multi-select values are also 2-element arrays.
        if (
          opDefFor(token.field, token.operator)?.range &&
          Array.isArray(token.value) &&
          token.value.length === 2
        ) {
          setRangeFrom(
            isDateToken
              ? toDateTimeInputValue(token.value[0])
              : token.value[0] ?? "",
          );
          setRangeTo(
            isDateToken
              ? toDateTimeInputValue(token.value[1], { endOfDay: true })
              : token.value[1] ?? "",
          );
          setInputValue("");
        } else {
          setRangeFrom("");
          setRangeTo("");
          const displayValue = Array.isArray(token.value)
            ? token.value.join(", ")
            : token.value === null || token.value === undefined
              ? ""
              : String(token.value);
          setInputValue(
            isDateToken ? toDateTimeInputValue(displayValue) : displayValue,
          );
          // A list searches for the value being typed, not for "a, b".
          onValueSearchChange?.(
            Array.isArray(token.value) && token.value.length > 1
              ? lastListPart(displayValue)
              : displayValue,
            token.field,
          );
        }
      }
      setTimeout(() => setDropdownOpen(true), 0);
      // Keep the parent filter set stable while this token is being edited.
      // Publishing the temporary removal makes controlled parents rebuild
      // initialTokens, which clears this partial editor before it can commit.
      // The completed edit is published by commitFilter/flushPartial instead.
    },
    [tokens, fieldMap, onFieldChange, onValueSearchChange, opDefFor],
  );

  const handleKeyDown = useCallback(
    (e) => {
      if (
        phase === "value" &&
        !isRangePhase &&
        e.key === "Enter" &&
        typedDraft &&
        (allowsFreeTextValue || editingList)
      ) {
        // MUI otherwise commits the first highlighted fuzzy suggestion after
        // this input-level handler returns. Resolve an exact option ourselves
        // so its typed id (including false / 0) wins regardless of ordering;
        // otherwise preserve the user's explicit free-form text (or list).
        e.defaultMuiPrevented = true;
        e.preventDefault();
        commitFilter(
          partialField,
          partialOp,
          typedDraft.value,
          typedDraft.valueTypes,
        );
        return;
      }
      if ((e.key === "Backspace" || e.key === "Delete") && !inputValue) {
        e.preventDefault();
        if (partialOp) {
          setPartialOp(null);
          setPartialValueTypes([]);
          setPartialOriginalValue(undefined);
          setRangeFrom("");
          setRangeTo("");
          setDropdownOpen(true);
        } else if (partialField) {
          setPartialField(null);
          setPartialValueTypes([]);
          setPartialOriginalValue(undefined);
          setDropdownOpen(true);
        } else if (tokens.length > 0) {
          editToken(tokens.length - 1);
        }
      }
    },
    [
      phase,
      isRangePhase,
      inputValue,
      partialField,
      partialOp,
      tokens,
      commitFilter,
      editToken,
      hasStaticValueChoices,
      allowsFreeTextValue,
      editingList,
      typedDraft,
    ],
  );

  const handleDeleteToken = useCallback(
    (index) => {
      const updated = tokens.filter((_, i) => i !== index);
      setTokens(updated);
      setDropdownOpen(true);
      onApply(updated.length > 0 ? updated : []);
    },
    [tokens, onApply],
  );

  const requestNextPage = useCallback(
    (requestedPhase, { retry = false } = {}) => {
      if (paginationRequestRef.current) return false;
      const isFieldRequest = requestedPhase === "field";
      const hasMore = isFieldRequest ? hasMoreFields : hasMoreValues;
      const loading = isFieldRequest ? fieldLoadingMore : valueLoadingMore;
      const failed = isFieldRequest ? fieldLoadError : valueLoadError;
      const requestAction = isFieldRequest
        ? onLoadMoreFields
        : onLoadMoreValues;
      if (
        !hasMore ||
        loading ||
        (!retry && failed) ||
        typeof requestAction !== "function"
      ) {
        return false;
      }

      let requestResult;
      try {
        requestResult = requestAction();
      } catch (_error) {
        return false;
      }
      const request = Promise.resolve(requestResult);
      paginationRequestRef.current = request;
      const clearRequest = () => {
        if (paginationRequestRef.current === request) {
          paginationRequestRef.current = null;
        }
      };
      request.then(clearRequest, clearRequest);
      return true;
    },
    [
      fieldLoadError,
      fieldLoadingMore,
      hasMoreFields,
      hasMoreValues,
      onLoadMoreFields,
      onLoadMoreValues,
      valueLoadError,
      valueLoadingMore,
    ],
  );

  const handleOptionsScroll = useCallback(
    (event) => {
      const { scrollTop, clientHeight, scrollHeight } = event.currentTarget;
      if (
        scrollHeight - scrollTop - clientHeight >
        QUERY_PAGINATION_THRESHOLD_PX
      ) {
        bottomEdgeLatchedRef.current = false;
        return;
      }
      if (bottomEdgeLatchedRef.current) return;
      if (requestNextPage(phase)) {
        bottomEdgeLatchedRef.current = true;
      }
    },
    [phase, requestNextPage],
  );

  const handlePaginationRetry = useCallback(
    (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (requestNextPage(phase, { retry: true })) {
        bottomEdgeLatchedRef.current = true;
      }
    },
    [phase, requestNextPage],
  );

  const inlinePrefix = useMemo(() => {
    const parts = [];
    if (partialField)
      parts.push({
        text: fieldMap[partialField]?.label || partialField,
        color: "primary.main",
      });
    if (partialOp) {
      parts.push({
        text: currentOpDef?.label || partialOp,
        color: "warning.main",
      });
    }
    return parts;
  }, [partialField, partialOp, fieldMap, currentOpDef]);

  const placeholder = isRangePhase
    ? ""
    : phase === "field"
      ? fieldLoading && filterFields.length === 0
        ? "loading fields..."
        : tokens.length
          ? "add filter..."
          : "type to filter — e.g. field → operator → value"
      : phase === "operator"
        ? "pick operator..."
        : valueLoading
          ? "loading values..."
          : allowsFreeTextValue
            ? "type or pick value..."
            : "pick value...";
  const paginationRetryAvailable =
    phase === "field"
      ? fieldLoadError &&
        hasMoreFields &&
        typeof onLoadMoreFields === "function"
      : phase === "value"
        ? valueLoadError &&
          hasMoreValues &&
          typeof onLoadMoreValues === "function"
        : false;

  // Typed text that matches nothing says so and what to do, instead of the
  // suggestions vanishing.
  const typedSearch = searchText.trim();
  const noMatchNoun =
    phase === "field" ? "field" : phase === "operator" ? "operator" : "value";
  const noMatchText = !typedSearch
    ? `No ${noMatchNoun}s to pick from.`
    : phase === "value"
      ? `No value matches "${typedSearch}". Only listed values can be used; clear the text to see them.`
      : `No ${noMatchNoun} matches "${typedSearch}". Clear the text to see every ${noMatchNoun}.`;
  // A half-built filter that a close would not apply says so while it is open.
  const unfinishedStep =
    phase === "operator"
      ? "Pick an operator"
      : allowsFreeTextValue
        ? "Type or pick a value"
        : "Pick a value from the list";
  const unfinishedHint =
    partialField && !typedDraft
      ? `${unfinishedStep} to finish this filter. Unfinished filters are not applied.`
      : undefined;

  // Chips read with the labels the user picked; a list reads "a, b" and only a
  // range keeps "from – to".
  const tokenChipLabel = (token) => {
    const fieldDef = fieldMap[token.field];
    const opDef = opDefFor(token.field, token.operator);
    const values = Array.isArray(token.value) ? token.value : [token.value];
    const value =
      Array.isArray(token.value) && opDef?.range
        ? token.value.join(" – ")
        : values.map((v) => choiceLabelFor(fieldDef, v)).join(", ");
    return `${fieldDef?.label || token.field} ${opDef?.label || token.operator} ${value}`;
  };

  // Shared chip/prefix render — used by both the Autocomplete renderInput
  // startAdornment and the range / date editor Boxes below.
  const tokenChips = tokens.map((token, idx) => (
    <Chip
      key={idx}
      label={tokenChipLabel(token)}
      size="small"
      onClick={() => editToken(idx)}
      onDelete={() => handleDeleteToken(idx)}
      deleteIcon={<Iconify icon="solar:close-linear" width={12} />}
      sx={{
        mr: 0.25,
        bgcolor: (theme) => alpha(theme.palette.primary.main, 0.08),
        color: "primary.main",
        border: "1px solid",
        borderColor: (theme) => alpha(theme.palette.primary.main, 0.2),
        cursor: "pointer",
        "&:hover": {
          bgcolor: (theme) => alpha(theme.palette.primary.main, 0.16),
          borderColor: (theme) => alpha(theme.palette.primary.main, 0.4),
        },
        "& .MuiChip-deleteIcon": {
          color: "primary.main",
          "&:hover": { color: "primary.dark" },
        },
      }}
    />
  ));

  const prefixChips = inlinePrefix.map((p, i) => (
    <Box
      key={i}
      component="span"
      sx={{
        fontSize: 13,
        fontWeight: 600,
        color: p.color,
        mr: 0.5,
        whiteSpace: "nowrap",
      }}
    >
      {p.text}
    </Box>
  ));

  if (isDateScalar) {
    const dateValue = inputValue.trim();
    const commitDate = () => {
      if (!dateValue) return;
      commitFilter(
        partialField,
        partialOp,
        originalInputUnchanged ? partialOriginalValue : dateValue,
        originalInputUnchanged ? partialValueTypes : undefined,
      );
    };
    const onDateKeyDown = (e) => {
      if (e.key === "Enter" && dateValue) {
        e.preventDefault();
        commitDate();
      }
      if (
        (e.key === "Backspace" || e.key === "Delete") &&
        isClearedInput(e.target)
      ) {
        e.preventDefault();
        setPartialOp(null);
        setPartialValueTypes([]);
        setPartialOriginalValue(undefined);
        setInputValue("");
      }
    };

    return (
      <Box sx={INLINE_EDITOR_SX}>
        {tokenChips}
        {prefixChips}
        <TextField
          size="small"
          type="datetime-local"
          value={inputValue}
          onChange={(e) => {
            const next = e.target.value.trim();
            if (next !== inputValue) {
              setPartialValueTypes([]);
              setPartialOriginalValue(undefined);
            }
            setInputValue(next);
          }}
          autoFocus
          onKeyDown={onDateKeyDown}
          sx={INLINE_FIELD_SX}
          inputProps={{ "aria-label": "Date and time" }}
        />
        {/* A span keeps the tooltip working on a disabled button. */}
        <Tooltip title="Apply date (Enter)">
          <span>
            <IconButton
              size="small"
              aria-label="Apply date"
              onClick={commitDate}
              disabled={!dateValue}
            >
              <Iconify icon="solar:check-linear" width={16} />
            </IconButton>
          </span>
        </Tooltip>
      </Box>
    );
  }

  if (isRangePhase) {
    // Cancel leaves the range editor for the query box. A chip that was being
    // edited comes back unchanged instead of being dropped.
    const cancelRange = () => {
      if (partialOriginalValue !== undefined) {
        const token = {
          field: partialField,
          operator: partialOp,
          value: partialOriginalValue,
        };
        if (partialValueTypes.some(Boolean)) {
          token.valueTypes = partialValueTypes;
        }
        setTokens([...tokens, token]);
      }
      setPartialField(null);
      setPartialOp(null);
      setPartialValueTypes([]);
      setPartialOriginalValue(undefined);
      setInputValue("");
      setRangeFrom("");
      setRangeTo("");
      setTimeout(() => {
        inputRef.current?.focus();
        setDropdownOpen(true);
      }, 0);
    };
    const rangeBoundInputProps = (invalid) =>
      isNumericRange
        ? {
            inputMode: "decimal",
            "aria-describedby": invalid ? rangeErrorId : undefined,
          }
        : undefined;
    const onRangeKeyDown = (e) => {
      if (e.key === "Escape") {
        // Only the range is cancelled; the filter panel stays open.
        e.preventDefault();
        e.stopPropagation();
        cancelRange();
        return;
      }
      if (e.key === "Enter" && rangeFrom !== "" && rangeTo !== "") {
        e.preventDefault();
        commitRange();
      }
      if (
        (e.key === "Backspace" || e.key === "Delete") &&
        isClearedInput(e.target)
      ) {
        e.preventDefault();
        setPartialOp(null);
        setRangeFrom("");
        setRangeTo("");
      }
    };

    return (
      <Box sx={INLINE_EDITOR_SX}>
        {tokenChips}
        {prefixChips}
        <TextField
          size="small"
          type={rangeInputType}
          value={rangeFrom}
          error={rangeFromInvalid}
          onChange={(e) => setRangeFrom(e.target.value.trim())}
          placeholder="from"
          autoFocus
          onKeyDown={onRangeKeyDown}
          sx={INLINE_FIELD_SX}
          inputProps={rangeBoundInputProps(rangeFromInvalid)}
        />
        <Box
          component="span"
          sx={{ fontSize: 12, color: "text.secondary", px: 0.25 }}
        >
          and
        </Box>
        <TextField
          size="small"
          type={rangeInputType}
          value={rangeTo}
          error={rangeToInvalid}
          onChange={(e) => setRangeTo(e.target.value.trim())}
          placeholder="to"
          onKeyDown={onRangeKeyDown}
          sx={INLINE_FIELD_SX}
          inputProps={rangeBoundInputProps(rangeToInvalid)}
        />
        {/* A span keeps the tooltip working on a disabled button. */}
        <Tooltip title="Apply range (Enter)">
          <span>
            <IconButton
              size="small"
              aria-label="Apply range"
              onClick={commitRange}
              disabled={
                rangeFrom === "" ||
                rangeTo === "" ||
                rangeFromInvalid ||
                rangeToInvalid
              }
            >
              <Iconify icon="solar:check-linear" width={16} />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title="Cancel (Esc)">
          <IconButton
            size="small"
            aria-label="Cancel range"
            onClick={cancelRange}
          >
            <Iconify icon="solar:close-linear" width={16} />
          </IconButton>
        </Tooltip>
        {(rangeFromInvalid || rangeToInvalid) && (
          <Box
            component="span"
            id={rangeErrorId}
            role="alert"
            sx={{ width: "100%", fontSize: 12, color: "error.main" }}
          >
            Enter a number, like 10 or 2.5.
          </Box>
        )}
      </Box>
    );
  }

  return (
    <Autocomplete
      size="small"
      freeSolo={valueFreeSolo}
      options={filtered}
      filterOptions={(availableOptions) => availableOptions}
      getOptionLabel={(o) => (typeof o === "string" ? o : o.label)}
      inputValue={inputValue}
      onInputChange={(_, v, reason) => {
        if (reason !== "reset") {
          // A list edit keeps the chip's values to read the text back against.
          if (v !== inputValue && !editingList) {
            setPartialValueTypes([]);
            setPartialOriginalValue(undefined);
          }
          setInputValue(v);
          if (phase === "field") onFieldSearchChange?.(v);
          else if (phase === "value")
            onValueSearchChange?.(
              editingList ? lastListPart(v) : v,
              partialField,
            );
        }
      }}
      onChange={handleSelect}
      open={
        !isRangePhase &&
        dropdownOpen &&
        focused &&
        (phase === "field" ||
          filtered.length > 0 ||
          // Typed text that matches nothing opens the "no match" row; a
          // free-text value needs none, Enter applies it.
          (Boolean(typedSearch) && !valueFreeSolo))
      }
      noOptionsText={noMatchText}
      onOpen={() => setDropdownOpen(true)}
      onClose={() => setDropdownOpen(false)}
      loading={phase === "field" ? fieldLoading : valueLoading}
      autoHighlight
      clearOnBlur={false}
      disableClearable
      value={null}
      ListboxProps={{
        "data-query-field-options-list": phase === "field" ? "" : undefined,
        "data-query-value-options-list": phase === "value" ? "" : undefined,
        onScroll: handleOptionsScroll,
      }}
      slotProps={{
        popper: { sx: { zIndex: 1500 } },
        paper: {
          sx: {
            fontSize: 13,
            mt: 0.5,
            borderRadius: "8px",
          },
        },
      }}
      renderOption={(props, option) => {
        const { key, ...rest } = props;
        const isField = option.type === "field";
        const isOperator = option.type === "operator";
        const isValue = option.type === "value";
        const fieldDef = isField ? fieldMap[option.id] : null;
        return (
          <Box
            component="li"
            key={key}
            {...rest}
            sx={{
              ...rest.sx,
              fontSize: 13,
              py: 0.5,
              px: 1.5,
              display: "flex",
              alignItems: "center",
              gap: 1,
            }}
          >
            {isField && (
              <Iconify
                icon={
                  fieldDef?.type === "enum"
                    ? "solar:list-linear"
                    : "solar:notes-linear"
                }
                width={14}
                sx={{ color: "text.disabled", flexShrink: 0 }}
              />
            )}
            {isOperator && (
              <Iconify
                icon="solar:code-linear"
                width={14}
                sx={{ color: "warning.main", flexShrink: 0 }}
              />
            )}
            {isValue && (
              <Iconify
                icon="solar:tag-linear"
                width={14}
                sx={{ color: "text.disabled", flexShrink: 0 }}
              />
            )}
            <Box
              sx={{
                flex: 1,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {option.label}
            </Box>
            {isField && (
              <Chip
                label={sentenceCase(
                  fieldDef?.category || fieldDef?.type || "text",
                )}
                size="small"
                variant="outlined"
                sx={{
                  flexShrink: 0,
                }}
              />
            )}
          </Box>
        );
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          inputRef={inputRef}
          placeholder={placeholder}
          helperText={
            phase === "field" && fieldLoadError
              ? "More fields could not be loaded. Retained matches remain available."
              : phase === "value" && valueLoadError
                ? "More values could not be loaded. Loaded matches remain available."
                : unfinishedHint
          }
          FormHelperTextProps={
            (phase === "field" && fieldLoadError) ||
            (phase === "value" && valueLoadError)
              ? { role: "status" }
              : undefined
          }
          onFocus={() => {
            setFocused(true);
            setDropdownOpen(true);
          }}
          onBlur={() => setFocused(false)}
          onKeyDown={handleKeyDown}
          InputProps={{
            ...params.InputProps,
            startAdornment: (
              <>
                {tokenChips}
                {prefixChips}
              </>
            ),
            endAdornment: paginationRetryAvailable ? (
              <>
                <IconButton
                  size="small"
                  aria-label={`Retry loading ${phase === "field" ? "fields" : "values"}`}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={handlePaginationRetry}
                  disabled={
                    paginationRequestRef.current !== null ||
                    (phase === "field" ? fieldLoadingMore : valueLoadingMore)
                  }
                  sx={{ mr: 0.5 }}
                >
                  <Iconify icon="solar:refresh-linear" width={16} />
                </IconButton>
                {params.InputProps.endAdornment}
              </>
            ) : (phase === "field" && (fieldLoading || fieldLoadingMore)) ||
              (phase === "value" && (valueLoading || valueLoadingMore)) ? (
              <CircularProgress size={14} sx={{ mr: 1 }} />
            ) : (
              params.InputProps.endAdornment
            ),
            sx: {
              ...params.InputProps.sx,
              fontSize: 13,
              fontFamily: "monospace",
              flexWrap: "wrap",
              gap: 0.25,
            },
          }}
        />
      )}
    />
  );
});

QueryInput.propTypes = {
  filterFields: PropTypes.array.isRequired,
  fieldMap: PropTypes.object.isRequired,
  onApply: PropTypes.func.isRequired,
  initialTokens: PropTypes.array,
  valueOptions: PropTypes.array,
  valueLoading: PropTypes.bool,
  valueLoadingMore: PropTypes.bool,
  valueLoadError: PropTypes.bool,
  fieldLoading: PropTypes.bool,
  fieldLoadingMore: PropTypes.bool,
  fieldLoadError: PropTypes.bool,
  onFieldChange: PropTypes.func,
  onFieldSearchChange: PropTypes.func,
  onLoadMoreFields: PropTypes.func,
  onValueSearchChange: PropTypes.func,
  onLoadMoreValues: PropTypes.func,
  hasMoreFields: PropTypes.bool,
  hasMoreValues: PropTypes.bool,
  getOperators: PropTypes.func,
};

export { QueryInput };
export default QueryInput;
