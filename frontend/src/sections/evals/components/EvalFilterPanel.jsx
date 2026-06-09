/* eslint-disable react/prop-types */
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  IconButton,
  InputAdornment,
  MenuItem,
  Popover,
  Select,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Iconify from "src/components/iconify";
import { useAIFilter } from "src/hooks/use-ai-filter";

// ---------------------------------------------------------------------------
// Filter field definitions — shared across all modes
// ---------------------------------------------------------------------------
const FILTER_FIELDS = [
  { value: "name", label: "Name", type: "string" },
  {
    value: "eval_type",
    label: "Eval Type",
    type: "enum",
    choices: ["llm", "code", "agent"],
  },
  {
    value: "output_type",
    label: "Output Type",
    type: "enum",
    choices: ["pass_fail", "percentage", "deterministic"],
  },
  {
    value: "template_type",
    label: "Type",
    type: "enum",
    choices: ["single", "composite"],
  },
  { value: "owner", label: "Owner", type: "enum", choices: ["user", "system"] },
  { value: "tags", label: "Tags", type: "string" },
];

const FIELD_MAP = Object.fromEntries(FILTER_FIELDS.map((f) => [f.value, f]));
const FIELD_ALIASES = {
  type: "template_type",
  "eval type": "eval_type",
  "output type": "output_type",
  "created by": "owner",
  creator: "owner",
};

const STRING_OPERATORS = [
  { value: "contains", label: "Contains" },
  { value: "equals", label: "Equals" },
  { value: "starts_with", label: "Starts with" },
  { value: "not_contains", label: "Does not contain" },
];

const ENUM_OPERATORS = [
  { value: "is", label: "Is" },
  { value: "is_not", label: "Is not" },
];

function getOperators(fieldType) {
  return fieldType === "enum" ? ENUM_OPERATORS : STRING_OPERATORS;
}

// All operator labels for NLP matching
const ALL_OPERATORS = [
  { value: "is", label: "is", aliases: ["=", "equals", "=="] },
  { value: "is_not", label: "is not", aliases: ["!=", "not", "isn't"] },
  {
    value: "contains",
    label: "contains",
    aliases: ["has", "includes", "like", "matches"],
  },
  {
    value: "not_contains",
    label: "does not contain",
    aliases: ["!contains", "excludes"],
  },
  { value: "starts_with", label: "starts with", aliases: ["begins"] },
];

// ---------------------------------------------------------------------------
// AI Filter — NLP parser (no LLM dependency, runs locally)
// ---------------------------------------------------------------------------
function parseNaturalLanguage(query) {
  const q = query.toLowerCase().trim();
  if (!q) return [];

  const rows = [];

  // Pattern: "show me X evals", "find X", "X evals", "evals that are X"
  const patterns = [
    // Direct field:value patterns
    {
      regex: /(?:eval[_ ]?type|type of eval)\s+(?:is|=|:)\s*(\w+)/i,
      field: "eval_type",
      op: "is",
    },
    {
      regex: /(?:output[_ ]?type)\s+(?:is|=|:)\s*(\w+)/i,
      field: "output_type",
      op: "is",
    },
    {
      regex: /(?:owner|created by|creator)\s+(?:is|=|:)\s*(\w+)/i,
      field: "owner",
      op: "is",
    },
    {
      regex:
        /(?:name)\s+(?:contains|has|like|includes|=|:)\s+['""]?(.+?)['""]?\s*(?:and|$)/i,
      field: "name",
      op: "contains",
    },
    {
      regex: /(?:name)\s+(?:is|equals|=)\s+['""]?(.+?)['""]?\s*(?:and|$)/i,
      field: "name",
      op: "equals",
    },

    // Keyword-based detection
    {
      regex: /\b(llm|llm[- ]as[- ]a[- ]judge)\b/i,
      field: "eval_type",
      op: "is",
      value: "llm",
    },
    { regex: /\bcode\s*eval/i, field: "eval_type", op: "is", value: "code" },
    { regex: /\bagent\s*eval/i, field: "eval_type", op: "is", value: "agent" },
    {
      regex: /\b(pass[/ ]?fail|pass or fail|boolean)\b/i,
      field: "output_type",
      op: "is",
      value: "pass_fail",
    },
    {
      regex: /\b(percentage|percent|score[- ]based)\b/i,
      field: "output_type",
      op: "is",
      value: "percentage",
    },
    {
      regex: /\b(deterministic|choice|categorical)\b/i,
      field: "output_type",
      op: "is",
      value: "deterministic",
    },
    {
      regex: /\b(system|built[- ]?in|default|pre[- ]?built)\b/i,
      field: "owner",
      op: "is",
      value: "system",
    },
    {
      regex: /\b(user|custom|my|created by me)\b/i,
      field: "owner",
      op: "is",
      value: "user",
    },
    {
      regex: /\bcomposite\b/i,
      field: "template_type",
      op: "is",
      value: "composite",
    },
    { regex: /\bsingle\b/i, field: "template_type", op: "is", value: "single" },
  ];

  const usedFields = new Set();

  for (const p of patterns) {
    const match = q.match(p.regex);
    if (match && !usedFields.has(p.field)) {
      const value = p.value || match[1]?.trim();
      if (value) {
        // Validate enum values
        const fieldDef = FIELD_MAP[p.field];
        if (fieldDef?.type === "enum") {
          const normalizedVal = value.toLowerCase();
          const validChoice = fieldDef.choices.find(
            (c) => c.toLowerCase() === normalizedVal,
          );
          if (validChoice) {
            rows.push({ field: p.field, operator: p.op, value: validChoice });
            usedFields.add(p.field);
          }
        } else {
          rows.push({ field: p.field, operator: p.op, value });
          usedFields.add(p.field);
        }
      }
    }
  }

  // Fallback: if nothing matched, treat entire query as name search
  if (rows.length === 0) {
    rows.push({ field: "name", operator: "contains", value: query.trim() });
  }

  return rows;
}

// ---------------------------------------------------------------------------
// Query Input — single-line inline token builder
//
// Type in one input. Autocomplete suggests field → operator → value in sequence.
// Completed clauses render as colored inline chips in the same input.
// Backspace removes the last token. Fully keyboard-driven.
// ---------------------------------------------------------------------------
function QueryInput({ onApply, initialTokens = [] }) {
  // Each token: { field, operator, value } — a complete filter
  const [tokens, setTokens] = useState(initialTokens);
  // Partial state for the clause being built
  const [partialField, setPartialField] = useState(null);
  const [partialOp, setPartialOp] = useState(null);
  const [inputValue, setInputValue] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [focused, setFocused] = useState(false);

  const phase = !partialField ? "field" : !partialOp ? "operator" : "value";

  const options = useMemo(() => {
    if (phase === "field") {
      return FILTER_FIELDS.map((f) => ({
        id: f.value,
        label: f.label,
        type: "field",
      }));
    }
    if (phase === "operator") {
      const fieldDef = FIELD_MAP[partialField];
      return getOperators(fieldDef?.type || "string").map((o) => ({
        id: o.value,
        label: o.label,
        type: "operator",
      }));
    }
    if (phase === "value") {
      const fieldDef = FIELD_MAP[partialField];
      if (fieldDef?.type === "enum") {
        return fieldDef.choices.map((c) => ({
          id: c,
          label: c,
          type: "value",
        }));
      }
    }
    return [];
  }, [phase, partialField]);

  const filtered = useMemo(() => {
    if (!inputValue) return options;
    const q = inputValue.toLowerCase();
    return options.filter((o) => o.label.toLowerCase().includes(q));
  }, [options, inputValue]);

  const commitFilter = useCallback(
    (field, op, value) => {
      const updated = [...tokens, { field, operator: op, value }];
      setTokens(updated);
      setPartialField(null);
      setPartialOp(null);
      setInputValue("");
      setTimeout(() => setDropdownOpen(true), 0);
      onApply(updated);
    },
    [tokens, onApply],
  );

  // Re-open dropdown after a selection — needs setTimeout because
  // MUI Autocomplete fires onClose after onChange, which would
  // immediately close the dropdown we just opened.
  const reopenDropdown = useCallback(() => {
    setTimeout(() => setDropdownOpen(true), 0);
  }, []);

  const handleSelect = useCallback(
    (_, option) => {
      if (!option || typeof option === "string") return;
      if (phase === "field") {
        setPartialField(option.id);
        setInputValue("");
        reopenDropdown();
      } else if (phase === "operator") {
        setPartialOp(option.id);
        setInputValue("");
        reopenDropdown();
      } else if (phase === "value") {
        commitFilter(partialField, partialOp, option.id);
      }
    },
    [phase, partialField, partialOp, commitFilter, reopenDropdown],
  );

  // Edit a token — remove it from the list and load it into partial state
  const editToken = useCallback(
    (index) => {
      const token = tokens[index];
      const updated = tokens.filter((_, i) => i !== index);
      setTokens(updated);
      setPartialField(token.field);
      setPartialOp(token.operator);
      setInputValue(token.value);
      setTimeout(() => setDropdownOpen(true), 0);
      onApply(updated.length > 0 ? updated : []);
    },
    [tokens, onApply],
  );

  const handleKeyDown = useCallback(
    (e) => {
      // Enter on free-text value
      if (
        phase === "value" &&
        e.key === "Enter" &&
        inputValue.trim() &&
        filtered.length === 0
      ) {
        e.preventDefault();
        commitFilter(partialField, partialOp, inputValue.trim());
        return;
      }
      // Backspace on empty — undo last partial step or pop last chip into edit
      if ((e.key === "Backspace" || e.key === "Delete") && !inputValue) {
        e.preventDefault();
        if (partialOp) {
          setPartialOp(null);
          setDropdownOpen(true);
        } else if (partialField) {
          setPartialField(null);
          setDropdownOpen(true);
        } else if (tokens.length > 0) {
          // Pop last chip back into edit mode
          editToken(tokens.length - 1);
        }
      }
    },
    [
      phase,
      inputValue,
      partialField,
      partialOp,
      tokens,
      filtered,
      commitFilter,
      editToken,
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

  // Build the inline prefix showing partial clause being typed
  const inlinePrefix = useMemo(() => {
    const parts = [];
    if (partialField)
      parts.push({
        text: FIELD_MAP[partialField]?.label || partialField,
        color: "primary.main",
      });
    if (partialOp) {
      const opDef = [...STRING_OPERATORS, ...ENUM_OPERATORS].find(
        (o) => o.value === partialOp,
      );
      parts.push({ text: opDef?.label || partialOp, color: "warning.main" });
    }
    return parts;
  }, [partialField, partialOp]);

  const placeholder =
    phase === "field"
      ? tokens.length
        ? "add filter..."
        : "type to filter — e.g. Eval Type → is → llm"
      : phase === "operator"
        ? "pick operator..."
        : FIELD_MAP[partialField]?.type === "enum"
          ? "pick value..."
          : "type value...";

  return (
    <Autocomplete
      size="small"
      freeSolo={phase === "value" && FIELD_MAP[partialField]?.type !== "enum"}
      options={filtered}
      getOptionLabel={(o) => (typeof o === "string" ? o : o.label)}
      inputValue={inputValue}
      onInputChange={(_, v, reason) => {
        if (reason !== "reset") setInputValue(v);
      }}
      onChange={handleSelect}
      open={dropdownOpen && focused && filtered.length > 0}
      onOpen={() => setDropdownOpen(true)}
      onClose={() => setDropdownOpen(false)}
      autoHighlight
      clearOnBlur={false}
      disableClearable
      value={null}
      slotProps={{
        popper: { style: { zIndex: 1500 }, placement: "bottom-start" },
      }}
      renderOption={(props, option) => {
        const { key, ...rest } = props;
        const colorMap = {
          field: "primary.main",
          operator: "warning.main",
          value: "success.main",
        };
        return (
          <Box
            component="li"
            key={key}
            {...rest}
            sx={{
              ...rest.sx,
              fontSize: "13px",
              py: 0.5,
              gap: 1,
              display: "flex",
              alignItems: "center",
            }}
          >
            <Box
              sx={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                backgroundColor: colorMap[option.type] || "text.disabled",
                flexShrink: 0,
              }}
            />
            <span style={{ fontFamily: "monospace" }}>{option.label}</span>
          </Box>
        );
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          placeholder={placeholder}
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
                {/* Completed filter chips */}
                {tokens.map((t, i) => (
                  <Chip
                    key={i}
                    size="small"
                    onClick={() => editToken(i)}
                    onDelete={() => handleDeleteToken(i)}
                    label={
                      <span
                        style={{ fontFamily: "monospace", fontSize: "12px" }}
                      >
                        <span style={{ fontWeight: 600 }}>
                          {FIELD_MAP[t.field]?.label || t.field}
                        </span>{" "}
                        <span style={{ opacity: 0.6 }}>{t.operator}</span>{" "}
                        <span style={{ fontWeight: 500 }}>{t.value}</span>
                      </span>
                    }
                    sx={{
                      height: 22,
                      mr: 0.5,
                      borderRadius: "4px",
                      cursor: "pointer",
                      "&:hover": { borderColor: "primary.main" },
                    }}
                  />
                ))}
                {/* Partial clause tokens (field, operator typed but value pending) */}
                {inlinePrefix.map((p, i) => (
                  <Box
                    key={i}
                    component="span"
                    sx={{
                      fontFamily: "monospace",
                      fontSize: "13px",
                      fontWeight: 600,
                      color: p.color,
                      mr: 0.5,
                      whiteSpace: "nowrap",
                    }}
                  >
                    {p.text}
                  </Box>
                ))}
              </>
            ),
            sx: {
              ...params.InputProps.sx,
              fontSize: "13px",
              fontFamily: "monospace",
              flexWrap: "wrap",
              gap: 0.25,
            },
          }}
        />
      )}
    />
  );
}

QueryInput.propTypes = {
  onApply: PropTypes.func.isRequired,
  initialTokens: PropTypes.array,
};

// ---------------------------------------------------------------------------
// Single filter row (Basic mode)
// ---------------------------------------------------------------------------
function FilterRow({ filter, index, onChange, onRemove, availableFields }) {
  const fieldDef = FIELD_MAP[filter.field] || FILTER_FIELDS[0];
  const operators = getOperators(fieldDef.type);

  return (
    <Stack direction="row" alignItems="center" gap={0.5}>
      <Select
        size="small"
        value={filter.field}
        onChange={(e) => {
          const newField = FIELD_MAP[e.target.value];
          onChange(index, {
            field: e.target.value,
            operator: newField?.type === "enum" ? "is" : "contains",
            value: "",
          });
        }}
        sx={{ minWidth: 100, fontSize: "13px", height: 30 }}
      >
        {availableFields.map((f) => (
          <MenuItem key={f.value} value={f.value} sx={{ fontSize: "13px" }}>
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
        sx={{ minWidth: 110, fontSize: "13px", height: 30 }}
      >
        {operators.map((op) => (
          <MenuItem key={op.value} value={op.value} sx={{ fontSize: "13px" }}>
            {op.label}
          </MenuItem>
        ))}
      </Select>

      {fieldDef.type === "enum" ? (
        <Autocomplete
          multiple
          size="small"
          options={fieldDef.choices || []}
          value={
            Array.isArray(filter.value)
              ? filter.value
              : filter.value
                ? [filter.value]
                : []
          }
          onChange={(_, newVal) =>
            onChange(index, { ...filter, value: newVal })
          }
          disableCloseOnSelect
          openOnFocus
          slotProps={{ popper: { style: { zIndex: 1500 } } }}
          renderTags={(selected, getTagProps) =>
            selected.map((val, i) => {
              const { key, ...rest } = getTagProps({ index: i });
              return (
                <Chip
                  key={key}
                  {...rest}
                  label={val}
                  size="small"
                  sx={{ height: 20, fontSize: "11px" }}
                />
              );
            })
          }
          renderInput={(params) => (
            <TextField
              {...params}
              placeholder={
                !filter.value ||
                (Array.isArray(filter.value) && filter.value.length === 0)
                  ? "Select..."
                  : ""
              }
              sx={{
                minWidth: 120,
                "& .MuiInputBase-root": {
                  fontSize: "13px",
                  minHeight: 30,
                  py: 0,
                  flexWrap: "wrap",
                  gap: 0.25,
                },
              }}
            />
          )}
          renderOption={(props, option, { selected }) => {
            const { key, ...rest } = props;
            return (
              <Box
                component="li"
                key={key}
                {...rest}
                sx={{ ...rest.sx, fontSize: "13px", py: 0.25 }}
              >
                <Iconify
                  icon={
                    selected
                      ? "mdi:checkbox-marked"
                      : "mdi:checkbox-blank-outline"
                  }
                  width={16}
                  sx={{
                    mr: 1,
                    color: selected ? "primary.main" : "text.disabled",
                  }}
                />
                {option}
              </Box>
            );
          }}
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
            "& .MuiInputBase-root": { fontSize: "13px", height: 30 },
          }}
        />
      )}

      <IconButton size="small" onClick={() => onRemove(index)} sx={{ p: 0.25 }}>
        <Iconify icon="mdi:close" width={14} />
      </IconButton>
    </Stack>
  );
}

FilterRow.propTypes = {
  filter: PropTypes.object.isRequired,
  index: PropTypes.number.isRequired,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  availableFields: PropTypes.array.isRequired,
};

// ---------------------------------------------------------------------------
// Helper: convert filter rows to API format
// ---------------------------------------------------------------------------
/**
 * Convert filter rows to the API format.
 *
 * The backend API uses:
 *   - `search` (top-level) for name filtering
 *   - `owner_filter` (top-level) for owner: "all" | "user" | "system"
 *   - `filters.eval_type` for eval type list
 *   - `filters.output_type` for output type list
 *   - `filters.tags` for tag list
 *
 * We pack everything into a single object and the EvalsListView
 * unpacks `search` and `owner` into the correct API params.
 */
function rowsToApiFilters(rows) {
  const result = {};
  const evalTypes = [];
  const outputTypes = [];

  for (const row of rows) {
    // value can be a string or an array (multi-select enums)
    const val = row.value;
    const isEmpty = !val || (Array.isArray(val) && val.length === 0);
    if (isEmpty) continue;

    const values = Array.isArray(val) ? val : [val];

    if (row.field === "eval_type") {
      if (row.operator === "is") evalTypes.push(...values);
    } else if (row.field === "output_type") {
      if (row.operator === "is") outputTypes.push(...values);
    } else if (row.field === "owner") {
      result.owner = values[0];
    } else if (row.field === "template_type") {
      result.template_type = values[0];
    } else if (row.field === "name") {
      result.search = values[0];
    } else if (row.field === "tags") {
      if (!result.tags) result.tags = [];
      // Tag values arrive as a free-text string from the popover UI but as
      // an array from chip selections. Split comma-separated strings so a
      // round-trip through the panel preserves each individual tag value
      // (otherwise chips-activated tags collapse into a single joined
      // string and stop matching).
      const flattened = values.flatMap((v) =>
        typeof v === "string"
          ? v
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean)
          : [v],
      );
      result.tags.push(...flattened);
    }
  }

  if (evalTypes.length) result.eval_type = evalTypes;
  if (outputTypes.length) result.output_type = outputTypes;

  return Object.keys(result).length > 0 ? result : null;
}

// ---------------------------------------------------------------------------
// AI Filter schema for evals (sent to the LLM for context)
// ---------------------------------------------------------------------------
const AI_FILTER_SCHEMA = FILTER_FIELDS.map((f) => ({
  field: f.value,
  label: f.label,
  type: f.type,
  operators:
    f.type === "enum"
      ? ["is", "is_not"]
      : ["contains", "equals", "starts_with", "not_contains"],
  ...(f.choices ? { choices: f.choices } : {}),
}));

// ---------------------------------------------------------------------------
// Main filter panel
// ---------------------------------------------------------------------------
const EvalFilterPanel = ({
  anchorEl,
  open,
  onClose,
  onApply,
  currentFilters,
  lockedFilters,
}) => {
  const [activeTab, setActiveTab] = useState("basic");
  const [aiQuery, setAiQuery] = useState("");
  const {
    parseQuery: aiParseQuery,
    loading: aiLoading,
    error: aiError,
  } = useAIFilter(AI_FILTER_SCHEMA);

  const lockedFields = useMemo(
    () => Object.keys(lockedFilters || {}),
    [lockedFilters],
  );
  const availableFields = useMemo(
    () => FILTER_FIELDS.filter((f) => !lockedFields.includes(f.value)),
    [lockedFields],
  );

  // Build the UI rows from the API-shaped `currentFilters` object. Each
  // enum field collapses into a single row with an *array* value — the
  // enum FilterRow uses a multi-select Autocomplete and expects one row
  // per field, not per value. Creating one row per value produced the
  // "split" layout and made the rows harder to edit.
  const buildInitialRows = useCallback(() => {
    const initial = [];
    if (currentFilters?.eval_type?.length) {
      initial.push({
        field: "eval_type",
        operator: "is",
        value: [...currentFilters.eval_type],
      });
    }

    if (currentFilters?.output_type?.length) {
      initial.push({
        field: "output_type",
        operator: "is",
        value: [...currentFilters.output_type],
      });
    }
    if (currentFilters?.template_type) {
      initial.push({
        field: "template_type",
        operator: "is",
        value: [currentFilters.template_type],
      });
    }
    if (currentFilters?.owner) {
      initial.push({
        field: "owner",
        operator: "is",
        value: [currentFilters.owner],
      });
    }
    if (currentFilters?.tags?.length) {
      initial.push({
        field: "tags",
        operator: "contains",
        value: currentFilters.tags.join(", "),
      });
    }
    if (currentFilters?.search) {
      initial.push({
        field: "name",
        operator: "contains",
        value: currentFilters.search,
      });
    }
    const visible = initial.filter((r) => !lockedFields.includes(r.field));
    return visible.length > 0
      ? visible
      : [{ field: "name", operator: "contains", value: "" }];
  }, [currentFilters, lockedFields]);

  const [rows, setRows] = useState(buildInitialRows);

  // Re-initialize rows when the panel opens. We intentionally only watch
  // `open` here — re-running on every `currentFilters` change would fight
  // the auto-apply effect below (which calls `onApply` which updates
  // `currentFilters` which would re-reset the rows).
  useEffect(() => {
    if (!open) return;
    setRows(buildInitialRows());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Auto-apply filters on every change (debounced for text inputs)
  const applyTimerRef = useRef(null);
  useEffect(() => {
    if (!open) return;
    if (applyTimerRef.current) clearTimeout(applyTimerRef.current);
    applyTimerRef.current = setTimeout(() => {
      onApply(rowsToApiFilters(rows));
    }, 400);
    return () => {
      if (applyTimerRef.current) clearTimeout(applyTimerRef.current);
    };
  }, [rows, open]); // intentionally exclude onApply to avoid loops

  const handleAddRow = useCallback(() => {
    setRows((prev) => [
      ...prev,
      { field: "name", operator: "contains", value: "" },
    ]);
  }, []);

  const handleUpdateRow = useCallback((index, newRow) => {
    setRows((prev) => prev.map((r, i) => (i === index ? newRow : r)));
  }, []);

  const handleRemoveRow = useCallback((index) => {
    setRows((prev) => {
      const next = prev.filter((_, i) => i !== index);
      return next.length === 0
        ? [{ field: "name", operator: "contains", value: "" }]
        : next;
    });
  }, []);

  const handleApplyFromNlp = useCallback(
    (nlpRows) => {
      // Sync to Basic tab rows so user can see/edit them there too
      setRows(nlpRows);
      // Auto-apply immediately
      onApply(rowsToApiFilters(nlpRows));
      // Don't close — user can keep adding or switch to Basic to edit
    },
    [onApply],
  );

  const handleAiFilter = useCallback(async () => {
    if (!aiQuery.trim()) return;

    // Try LLM-backed AI filter first
    const aiFilters = await aiParseQuery(aiQuery);

    let parsed;
    if (aiFilters.length > 0) {
      parsed = aiFilters;
    } else {
      // Fallback to local NLP parser
      parsed = parseNaturalLanguage(aiQuery);
    }

    setRows(parsed);
    setAiQuery("");
    onApply(rowsToApiFilters(parsed));
    onClose();
  }, [aiQuery, aiParseQuery, onApply, onClose]);

  const handleClear = useCallback(() => {
    setRows([{ field: "name", operator: "contains", value: "" }]);
    onApply(null);
    onClose();
  }, [onApply, onClose]);

  const activeFilterCount = useMemo(
    () =>
      rows.filter((r) => {
        if (Array.isArray(r.value)) return r.value.length > 0;
        return !!r.value;
      }).length,
    [rows],
  );

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{
        paper: {
          sx: {
            width: 420,
            p: 1,
            borderRadius: "8px",
            boxShadow: "1px 1px 12px 10px rgba(0,0,0,0.04)",
          },
        },
      }}
    >
      <Stack spacing={1}>
        {/* AI filter input */}
        <TextField
          size="small"
          placeholder={
            aiLoading
              ? "Parsing with AI..."
              : "Ask AI — e.g. 'show me LLM pass/fail evals'"
          }
          value={aiQuery}
          onChange={(e) => setAiQuery(e.target.value)}
          disabled={aiLoading}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleAiFilter();
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon={aiLoading ? "mdi:loading" : "mdi:creation"}
                  width={16}
                  sx={{
                    color: "primary.main",
                    ...(aiLoading
                      ? {
                          animation: "spin 1s linear infinite",
                          "@keyframes spin": {
                            from: { transform: "rotate(0deg)" },
                            to: { transform: "rotate(360deg)" },
                          },
                        }
                      : {}),
                  }}
                />
              </InputAdornment>
            ),
            endAdornment:
              aiQuery.trim() && !aiLoading ? (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    onClick={handleAiFilter}
                    sx={{ p: 0.25 }}
                  >
                    <Iconify icon="mdi:arrow-right" width={16} />
                  </IconButton>
                </InputAdornment>
              ) : null,
            sx: { fontSize: "13px", height: 32 },
          }}
          fullWidth
        />
        {aiError && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontSize: "11px", px: 0.5 }}
          >
            AI unavailable, using local parser
          </Typography>
        )}

        {/* Tabs */}
        <Tabs
          value={activeTab}
          onChange={(_, v) => setActiveTab(v)}
          sx={{
            minHeight: 28,
            borderBottom: "1px solid",
            borderColor: "divider",
            "& .MuiTab-root": {
              minHeight: 28,
              py: 0.5,
              px: 1,
              textTransform: "none",
              fontSize: "13px",
              fontWeight: 500,
              minWidth: 0,
            },
          }}
        >
          <Tab value="basic" label="Basic" />
          <Tab value="nlp" label="Query" />
        </Tabs>

        {activeTab === "basic" ? (
          <>
            {/* Section label */}
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                fontSize: "11px",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                px: 0.5,
              }}
            >
              Basic Filter
            </Typography>

            {/* Filter rows */}
            <Stack spacing={0.75}>
              {rows.map((row, i) => (
                <FilterRow
                  key={i}
                  filter={row}
                  index={i}
                  onChange={handleUpdateRow}
                  onRemove={handleRemoveRow}
                  availableFields={availableFields}
                />
              ))}
            </Stack>

            {/* Action buttons */}
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
            >
              <Button
                size="small"
                startIcon={<Iconify icon="mingcute:add-line" width={14} />}
                onClick={handleAddRow}
                sx={{
                  textTransform: "none",
                  fontSize: "12px",
                  fontWeight: 500,
                }}
              >
                Add filter
              </Button>
              {activeFilterCount > 0 && (
                <Button
                  size="small"
                  onClick={handleClear}
                  sx={{
                    textTransform: "none",
                    fontSize: "12px",
                    color: "text.secondary",
                  }}
                >
                  Clear all
                </Button>
              )}
            </Stack>
          </>
        ) : (
          <>
            {/* Query builder mode */}
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                fontSize: "11px",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                px: 0.5,
              }}
            >
              Query Builder
            </Typography>

            <QueryInput
              onApply={handleApplyFromNlp}
              initialTokens={rows.filter((r) => r.value)}
            />
          </>
        )}
      </Stack>
    </Popover>
  );
};

EvalFilterPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
  currentFilters: PropTypes.object,
  lockedFilters: PropTypes.object,
};

export default EvalFilterPanel;
