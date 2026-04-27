/* eslint-disable react/prop-types */
/**
 * FilterPanel — Reusable filter popover with AI, Basic, and Query modes.
 *
 * Usage:
 *   <FilterPanel
 *     anchorEl={anchorEl}
 *     open={open}
 *     onClose={onClose}
 *     filterFields={[
 *       { value: "status", label: "Status", type: "enum", choices: ["OK", "ERROR"] },
 *       { value: "model", label: "Model", type: "string" },
 *     ]}
 *     currentFilters={filters}
 *     onApply={setFilters}
 *     aiPlaceholder="e.g. 'show traces with errors'"
 *   />
 */
import {
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
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
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
// Operators
// ---------------------------------------------------------------------------
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

function getOperators(fieldType) {
  return fieldType === "enum" ? ENUM_OPERATORS : STRING_OPERATORS;
}

// ---------------------------------------------------------------------------
// NLP parser — local, no LLM dependency
// ---------------------------------------------------------------------------
function parseNaturalLanguage(query, filterFields, fieldMap) {
  const q = query.toLowerCase().trim();
  if (!q) return [];

  const rows = [];
  const usedFields = new Set();

  // Try each field for a match
  for (const field of filterFields) {
    const label = field.label.toLowerCase();
    const val = field.value.toLowerCase();

    // Pattern: "field is/= value"
    const regex = new RegExp(
      `(?:${label}|${val})\\s+(?:is|=|:)\\s+['""]?(.+?)['""]?(?:\\s+and|$)`,
      "i",
    );
    const match = q.match(regex);
    if (match && !usedFields.has(field.value)) {
      const matchedValue = match[1].trim();
      if (field.type === "enum") {
        const validChoice = field.choices?.find(
          (c) => c.toLowerCase() === matchedValue.toLowerCase(),
        );
        if (validChoice) {
          rows.push({ field: field.value, operator: "is", value: validChoice });
          usedFields.add(field.value);
        }
      } else {
        rows.push({
          field: field.value,
          operator: "contains",
          value: matchedValue,
        });
        usedFields.add(field.value);
      }
    }
  }

  // Keyword-based enum matching
  for (const field of filterFields) {
    if (
      field.type === "enum" &&
      field.choices &&
      !usedFields.has(field.value)
    ) {
      for (const choice of field.choices) {
        if (q.includes(choice.toLowerCase())) {
          rows.push({ field: field.value, operator: "is", value: choice });
          usedFields.add(field.value);
          break;
        }
      }
    }
  }

  // Fallback: first string field search
  if (rows.length === 0) {
    const nameField =
      filterFields.find((f) => f.type === "string") || filterFields[0];
    rows.push({
      field: nameField.value,
      operator: "contains",
      value: query.trim(),
    });
  }

  return rows;
}

// ---------------------------------------------------------------------------
// EnumValuePicker — checkbox multi-select popover (matches trace filter design)
// ---------------------------------------------------------------------------
function EnumValuePicker({ choices, value = [], onChange }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    if (!search) return choices;
    const q = search.toLowerCase();
    return choices.filter((c) => c.toLowerCase().includes(q));
  }, [choices, search]);

  const toggle = useCallback(
    (val) => {
      onChange(
        value.includes(val) ? value.filter((v) => v !== val) : [...value, val],
      );
    },
    [value, onChange],
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
                label={v}
                size="small"
                onDelete={(e) => {
                  e.stopPropagation();
                  onChange(value.filter((x) => x !== v));
                }}
                deleteIcon={<Iconify icon="mdi:close" width={10} />}
                sx={{
                  height: 20,
                  fontSize: 10,
                  maxWidth: 80,
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />
            ))}
            {value.length > 2 && (
              <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
                +{value.length - 2}
              </Typography>
            )}
          </>
        )}
        <Iconify
          icon={anchorEl ? "mdi:chevron-up" : "mdi:chevron-down"}
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
          paper: { sx: { width: 260, borderRadius: "8px", mt: 0.5 } },
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
                    icon="mdi:magnify"
                    width={14}
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
              sx: { fontSize: 12, height: 30 },
            }}
          />
          <Typography
            sx={{ fontSize: 10, color: "text.disabled", mt: 0.5, px: 0.25 }}
          >
            Select one or more values (multi-select)
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
          {search && filtered.length > 0 && (
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
              <Iconify
                icon={
                  filtered.every((o) => value.includes(o))
                    ? "mdi:checkbox-intermediate"
                    : "mdi:checkbox-blank-outline"
                }
                width={18}
                sx={{ color: "primary.main", flexShrink: 0 }}
              />
              <Typography
                sx={{ fontSize: 12, color: "primary.main", fontWeight: 600 }}
              >
                Select all matching in list ({filtered.length})
              </Typography>
            </Box>
          )}

          {/* Specify custom value */}
          {search && !choices.includes(search) && (
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
              <Iconify
                icon={
                  value.includes(search)
                    ? "mdi:checkbox-marked"
                    : "mdi:checkbox-blank-outline"
                }
                width={18}
                sx={{
                  color: value.includes(search)
                    ? "primary.main"
                    : "text.secondary",
                  flexShrink: 0,
                }}
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
                <Iconify
                  icon={
                    isSelected
                      ? "mdi:checkbox-marked"
                      : "mdi:checkbox-blank-outline"
                  }
                  width={18}
                  sx={{
                    color: isSelected ? "primary.main" : "text.secondary",
                    flexShrink: 0,
                  }}
                />
                <Typography
                  noWrap
                  sx={{
                    fontSize: 12,
                    flex: 1,
                    fontWeight: isSelected ? 600 : 400,
                  }}
                >
                  {opt}
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
            <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
              {value.length} selected
            </Typography>
            <Button
              size="small"
              onClick={() => onChange([])}
              sx={{
                textTransform: "none",
                fontSize: 11,
                p: 0,
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

// ---------------------------------------------------------------------------
// FilterRow — single row: field → operator → value
// ---------------------------------------------------------------------------
function FilterRow({
  filter,
  index,
  filterFields,
  fieldMap,
  onChange,
  onRemove,
}) {
  const fieldDef = fieldMap[filter.field] || filterFields[0];
  const operators = getOperators(fieldDef.type);

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
          <MenuItem key={f.value} value={f.value} sx={{ fontSize: 13 }}>
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
          <MenuItem key={op.value} value={op.value} sx={{ fontSize: 13 }}>
            {op.label}
          </MenuItem>
        ))}
      </Select>

      {fieldDef.type === "enum" ? (
        <EnumValuePicker
          choices={fieldDef.choices || []}
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

      <IconButton size="small" onClick={() => onRemove(index)} sx={{ p: 0.25 }}>
        <Iconify icon="mdi:close" width={14} />
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

// ---------------------------------------------------------------------------
// QueryInput — inline token builder (field → operator → value)
// ---------------------------------------------------------------------------
/**
 * @param {Array} filterFields — field definitions
 * @param {Object} fieldMap — { fieldValue: fieldDef }
 * @param {Function} onApply — called with array of tokens
 * @param {Array} initialTokens
 * @param {Array} [valueOptions] — dynamic value options for the current field (from parent)
 * @param {string} [activeField] — notifies parent which field is selected (for value fetching)
 * @param {Function} [onFieldChange] — called when field changes (so parent can fetch values)
 */
function QueryInput({
  filterFields,
  fieldMap,
  onApply,
  initialTokens = [],
  valueOptions = [],
  valueLoading = false,
  onFieldChange,
}) {
  const [tokens, setTokens] = useState(initialTokens);
  const [partialField, setPartialField] = useState(null);
  const [partialOp, setPartialOp] = useState(null);
  const [inputValue, setInputValue] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [focused, setFocused] = useState(false);
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
    setInputValue("");
  }, [initialTokensKey]);

  const phase = !partialField ? "field" : !partialOp ? "operator" : "value";

  const options = useMemo(() => {
    if (phase === "field")
      return filterFields.map((f) => ({
        id: f.value,
        label: f.label,
        type: "field",
      }));
    if (phase === "operator") {
      const fd = fieldMap[partialField];
      return getOperators(fd?.type || "string").map((o) => ({
        id: o.value,
        label: o.label,
        type: "operator",
      }));
    }
    if (phase === "value") {
      const fd = fieldMap[partialField];
      // Static enum choices
      if (fd?.type === "enum" && fd.choices?.length) {
        return fd.choices.map((c) => ({ id: c, label: c, type: "value" }));
      }
      // Dynamic values from parent (fetched from CH)
      if (valueOptions.length > 0) {
        return valueOptions.map((o) => {
          const val = typeof o === "string" ? o : o.value || o.label;
          const label = typeof o === "string" ? o : o.label || o.value;
          return { id: val, label, type: "value" };
        });
      }
    }
    return [];
  }, [phase, partialField, filterFields, fieldMap, valueOptions]);

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

  const reopenDropdown = useCallback(() => {
    setTimeout(() => setDropdownOpen(true), 0);
  }, []);

  const handleSelect = useCallback(
    (_, option) => {
      if (!option || typeof option === "string") return;
      if (phase === "field") {
        setPartialField(option.id);
        setInputValue("");
        onFieldChange?.(option.id);
        reopenDropdown();
      } else if (phase === "operator") {
        setPartialOp(option.id);
        setInputValue("");
        reopenDropdown();
      } else if (phase === "value") {
        commitFilter(partialField, partialOp, option.id);
      }
    },
    [
      phase,
      partialField,
      partialOp,
      commitFilter,
      reopenDropdown,
      onFieldChange,
    ],
  );

  const editToken = useCallback(
    (index) => {
      const token = tokens[index];
      const updated = tokens.filter((_, i) => i !== index);
      setTokens(updated);
      setPartialField(token.field);
      setPartialOp(token.operator);
      setInputValue(typeof token.value === "string" ? token.value : "");
      setTimeout(() => setDropdownOpen(true), 0);
      onApply(updated.length > 0 ? updated : []);
    },
    [tokens, onApply],
  );

  const handleKeyDown = useCallback(
    (e) => {
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
      if ((e.key === "Backspace" || e.key === "Delete") && !inputValue) {
        e.preventDefault();
        if (partialOp) {
          setPartialOp(null);
          setDropdownOpen(true);
        } else if (partialField) {
          setPartialField(null);
          setDropdownOpen(true);
        } else if (tokens.length > 0) {
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

  const inlinePrefix = useMemo(() => {
    const parts = [];
    if (partialField)
      parts.push({
        text: fieldMap[partialField]?.label || partialField,
        color: "primary.main",
      });
    if (partialOp) {
      const opDef = [...STRING_OPERATORS, ...ENUM_OPERATORS].find(
        (o) => o.value === partialOp,
      );
      parts.push({ text: opDef?.label || partialOp, color: "warning.main" });
    }
    return parts;
  }, [partialField, partialOp, fieldMap]);

  const placeholder =
    phase === "field"
      ? tokens.length
        ? "add filter..."
        : "type to filter — e.g. field → operator → value"
      : phase === "operator"
        ? "pick operator..."
        : valueLoading
          ? "loading values..."
          : fieldMap[partialField]?.type === "enum"
            ? "pick value..."
            : "type or pick value...";

  return (
    <Autocomplete
      size="small"
      freeSolo={phase === "value" && fieldMap[partialField]?.type !== "enum"}
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
        popper: { sx: { zIndex: 1500 } },
        paper: {
          sx: {
            fontSize: 13,
            mt: 0.5,
            borderRadius: "6px",
            boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
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
                    ? "mdi:format-list-bulleted"
                    : "mdi:text-short"
                }
                width={14}
                sx={{ color: "text.disabled", flexShrink: 0 }}
              />
            )}
            {isOperator && (
              <Iconify
                icon="mdi:code-tags"
                width={14}
                sx={{ color: "warning.main", flexShrink: 0 }}
              />
            )}
            {isValue && (
              <Iconify
                icon="mdi:checkbox-blank-outline"
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
                label={fieldDef?.category || fieldDef?.type || "text"}
                size="small"
                variant="outlined"
                sx={{
                  height: 16,
                  fontSize: 9,
                  flexShrink: 0,
                  textTransform: "capitalize",
                }}
              />
            )}
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
                {tokens.map((token, idx) => (
                  <Chip
                    key={idx}
                    label={`${fieldMap[token.field]?.label || token.field} ${token.operator} ${token.value}`}
                    size="small"
                    onClick={() => editToken(idx)}
                    onDelete={() => handleDeleteToken(idx)}
                    deleteIcon={<Iconify icon="mdi:close" width={10} />}
                    sx={{
                      height: 22,
                      fontSize: 11,
                      mr: 0.25,
                      bgcolor: (theme) =>
                        alpha(theme.palette.primary.main, 0.08),
                      color: "primary.main",
                      border: "1px solid",
                      borderColor: (theme) =>
                        alpha(theme.palette.primary.main, 0.2),
                      cursor: "pointer",
                      transition: (theme) =>
                        theme.transitions.create(
                          ["background-color", "border-color"],
                          { duration: theme.transitions.duration.shortest },
                        ),
                      "&:hover": {
                        bgcolor: (theme) =>
                          alpha(theme.palette.primary.main, 0.16),
                        borderColor: (theme) =>
                          alpha(theme.palette.primary.main, 0.4),
                      },
                      "& .MuiChip-deleteIcon": {
                        color: "primary.main",
                        "&:hover": { color: "primary.dark" },
                      },
                    }}
                  />
                ))}
                {inlinePrefix.map((p, i) => (
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
                ))}
              </>
            ),
            endAdornment:
              valueLoading && phase === "value" ? (
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
}

QueryInput.propTypes = {
  filterFields: PropTypes.array.isRequired,
  fieldMap: PropTypes.object.isRequired,
  onApply: PropTypes.func.isRequired,
  initialTokens: PropTypes.array,
  valueOptions: PropTypes.array,
  valueLoading: PropTypes.bool,
  onFieldChange: PropTypes.func,
};

// ---------------------------------------------------------------------------
// FilterPanel — main component
// ---------------------------------------------------------------------------
const FilterPanel = ({
  anchorEl,
  open,
  onClose,
  filterFields,
  currentFilters,
  onApply,
  aiPlaceholder = "Ask AI — e.g. 'show me items with errors'",
  width = 420,
  // Optional smart-mode wiring. When the caller passes a `projectId`,
  // the AI filter call goes through the agentic backend (`mode=smart`),
  // which fetches real CH values and grounds the LLM's answer. Without
  // a `projectId` the panel falls back to the legacy build_filters path.
  projectId,
  source = "traces",
}) => {
  const fieldMap = useMemo(
    () => Object.fromEntries(filterFields.map((f) => [f.value, f])),
    [filterFields],
  );

  const [activeTab, setActiveTab] = useState("basic");
  const [aiQuery, setAiQuery] = useState("");

  const aiSchema = useMemo(
    () =>
      filterFields.map((f) => ({
        field: f.value,
        label: f.label,
        type: f.type,
        operators:
          f.type === "enum"
            ? ["is", "is_not"]
            : ["contains", "equals", "starts_with", "not_contains"],
        ...(f.choices ? { choices: f.choices } : {}),
        // choiceLabels is an optional {value: humanLabel} map. We forward
        // it as choice_labels so the AI filter can resolve human labels
        // (e.g. user types "Chat") back to the canonical API value
        // (e.g. "text"). See PersonaListView.simulation_type for the
        // canonical example.
        ...(f.choiceLabels ? { choice_labels: f.choiceLabels } : {}),
      })),
    [filterFields],
  );

  const {
    parseQuery: aiParseQuery,
    loading: aiLoading,
    error: aiError,
  } = useAIFilter(aiSchema);

  const defaultRow = useMemo(() => {
    const first = filterFields[0];
    return {
      field: first?.value || "",
      operator: first?.type === "enum" ? "is" : "contains",
      value: "",
    };
  }, [filterFields]);

  const [rows, setRows] = useState([{ ...defaultRow }]);
  const applyTimerRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    if (
      currentFilters &&
      typeof currentFilters === "object" &&
      !Array.isArray(currentFilters)
    ) {
      // Convert object-style filters to rows
      const initial = [];
      for (const [key, val] of Object.entries(currentFilters)) {
        const isNeg = key.endsWith("_not");
        const field = isNeg ? key.slice(0, -4) : key;
        if (Array.isArray(val)) {
          const op = isNeg ? "is_not" : (fieldMap[field]?.type === "enum" ? "is" : "contains");
          val.forEach((v) =>
            initial.push({ field, operator: op, value: v }),
          );
        } else if (val) {
          initial.push({ field, operator: isNeg ? "not_equals" : "contains", value: val });
        }
      }
      if (initial.length > 0) setRows(initial);
      else setRows([{ ...defaultRow }]);
    } else if (Array.isArray(currentFilters) && currentFilters.length > 0) {
      setRows([...currentFilters]);
    } else {
      setRows([{ ...defaultRow }]);
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-apply on row changes (debounced)
  useEffect(() => {
    if (!open) return;
    if (applyTimerRef.current) clearTimeout(applyTimerRef.current);
    applyTimerRef.current = setTimeout(() => {
      const result = {};
      for (const row of rows) {
        const val = row.value;
        const isEmpty = !val || (Array.isArray(val) && val.length === 0);
        if (isEmpty) continue;
        const values = Array.isArray(val) ? val : [val];
        const isNeg = row.operator === "is_not" || row.operator === "not_equals";
        const key = isNeg ? `${row.field}_not` : row.field;
        if (!result[key]) result[key] = [];
        result[key].push(...values);
      }
      onApply(Object.keys(result).length > 0 ? result : null);
    }, 400);
    return () => {
      if (applyTimerRef.current) clearTimeout(applyTimerRef.current);
    };
  }, [rows, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleAddRow = useCallback(() => {
    setRows((prev) => [...prev, { ...defaultRow }]);
  }, [defaultRow]);

  const handleUpdateRow = useCallback((index, newRow) => {
    setRows((prev) => prev.map((r, i) => (i === index ? newRow : r)));
  }, []);

  const handleRemoveRow = useCallback(
    (index) => {
      setRows((prev) => {
        const next = prev.filter((_, i) => i !== index);
        return next.length === 0 ? [{ ...defaultRow }] : next;
      });
    },
    [defaultRow],
  );

  const handleAiFilter = useCallback(async () => {
    if (!aiQuery.trim()) return;
    const aiFilters = await aiParseQuery(
      aiQuery,
      projectId ? { smart: true, projectId, source } : undefined,
    );
    const parsed =
      aiFilters.length > 0
        ? aiFilters
        : parseNaturalLanguage(aiQuery, filterFields, fieldMap);
    setRows(parsed);
    setAiQuery("");
  }, [aiQuery, aiParseQuery, filterFields, fieldMap, projectId, source]);

  const handleClear = useCallback(() => {
    setRows([{ ...defaultRow }]);
    onApply(null);
    onClose();
  }, [defaultRow, onApply, onClose]);

  const handleApplyFromNlp = useCallback((nlpRows) => {
    setRows(nlpRows);
  }, []);

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
            width,
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
          placeholder={aiLoading ? "Parsing with AI..." : aiPlaceholder}
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
            sx: { fontSize: 13, height: 32 },
          }}
          fullWidth
        />
        {aiError && (
          <Typography
            variant="caption"
            sx={{ fontSize: 11, color: "text.secondary", px: 0.5 }}
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
              fontSize: 13,
              fontWeight: 500,
              minWidth: 0,
            },
          }}
        >
          <Tab value="basic" label="Basic" />
          <Tab value="query" label="Query" />
        </Tabs>

        {activeTab === "basic" ? (
          <>
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                fontSize: 11,
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                px: 0.5,
              }}
            >
              Basic Filter
            </Typography>
            <Stack spacing={0.75}>
              {rows.map((row, i) => (
                <FilterRow
                  key={i}
                  filter={row}
                  index={i}
                  filterFields={filterFields}
                  fieldMap={fieldMap}
                  onChange={handleUpdateRow}
                  onRemove={handleRemoveRow}
                />
              ))}
            </Stack>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
            >
              <Button
                size="small"
                startIcon={<Iconify icon="mingcute:add-line" width={14} />}
                onClick={handleAddRow}
                sx={{ textTransform: "none", fontSize: 12, fontWeight: 500 }}
              >
                Add filter
              </Button>
              {activeFilterCount > 0 && (
                <Button
                  size="small"
                  onClick={handleClear}
                  sx={{
                    textTransform: "none",
                    fontSize: 12,
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
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                fontSize: 11,
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                px: 0.5,
              }}
            >
              Query Builder
            </Typography>
            <QueryInput
              filterFields={filterFields}
              fieldMap={fieldMap}
              onApply={handleApplyFromNlp}
              initialTokens={rows.filter((r) => {
                if (Array.isArray(r?.value)) return r.value.length > 0;
                return Boolean(r?.value);
              })}
            />
            <Typography
              variant="caption"
              sx={{ fontSize: 10, color: "text.disabled", px: 0.5 }}
            >
              Type property → pick operator → pick/type value. Backspace to
              undo. Click chip to edit.
            </Typography>
          </>
        )}
      </Stack>
    </Popover>
  );
};

FilterPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  filterFields: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      type: PropTypes.oneOf(["string", "enum"]).isRequired,
      choices: PropTypes.arrayOf(PropTypes.string),
    }),
  ).isRequired,
  currentFilters: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
  onApply: PropTypes.func.isRequired,
  aiPlaceholder: PropTypes.string,
  width: PropTypes.number,
  projectId: PropTypes.string,
  source: PropTypes.string,
};

export { QueryInput };
export default FilterPanel;
