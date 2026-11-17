// W3-2 B4 (D22): the Form view of a field with a declared schema. Inputs
// are typed by the schema (enum → select, boolean → switch, number /
// integer → number, string → text, object → a group, array → a list); every
// edit is checked with the one validator (Values/schemaValidate.js) and a
// valid value goes to `onCommit` (the Row view turns it into a pending
// update_cell change).
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { validateValue } from "src/sections/common/Values/schemaValidate";
import { formatPath } from "src/sections/common/Values/faPath";

const JSON_TYPES = new Set(["json", "array", "object", "conversation"]);

const typeOfSchema = (schema, value) => {
  const t = Array.isArray(schema?.type)
    ? schema.type.find((x) => x !== "null")
    : schema?.type;
  if (t) return t;
  if (Array.isArray(schema?.enum)) return "enum";
  if (schema?.properties) return "object";
  if (schema?.items) return "array";
  if (Array.isArray(value)) return "array";
  if (value && typeof value === "object") return "object";
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  return "string";
};

/** The stored cell value as the schema's shape (JSON text parsed). Pure. */
export function formValueOf(raw, schema, dataType) {
  const t = typeOfSchema(schema, raw);
  if (typeof raw === "string") {
    const s = raw.trim();
    if ((t === "object" || t === "array" || JSON_TYPES.has(dataType)) && s) {
      try {
        return JSON.parse(s);
      } catch {
        return raw;
      }
    }
    if ((t === "number" || t === "integer") && /^-?(\d+\.?\d*|\.\d+)$/.test(s))
      return Number(s);
    if (t === "boolean" && (s === "true" || s === "false")) return s === "true";
  }
  return raw;
}

/** The form's value as the cell stores it (JSON columns as JSON text). Pure. */
export function cellValueOf(value, dataType, raw) {
  if (value !== null && typeof value === "object") return JSON.stringify(value);
  if (
    JSON_TYPES.has(dataType) &&
    typeof raw === "string" &&
    raw.trim().startsWith('"')
  )
    return JSON.stringify(value);
  if (typeof raw === "string" && value !== null && value !== undefined)
    return String(value);
  return value;
}

/** A starting value for a new list item / missing key. Pure. */
export function emptyFor(schema) {
  const t = typeOfSchema(schema, undefined);
  if (Array.isArray(schema?.enum)) return schema.enum[0] ?? "";
  if (t === "boolean") return false;
  if (t === "number" || t === "integer") return 0;
  if (t === "object") return {};
  if (t === "array") return [];
  return "";
}

const setAt = (root, steps, next) => {
  if (!steps.length) return next;
  const [head, ...rest] = steps;
  const key = head.type === "index" ? head.index : head.key;
  const base = Array.isArray(root) ? [...root] : { ...(root ?? {}) };
  base[key] = setAt(base[key], rest, next);
  return base;
};

const labelOf = (steps, name) => formatPath(name, steps) || name;

function TextLeaf({ value, number, onCommit, disabled, label }) {
  const [text, setText] = useState(value == null ? "" : String(value));
  useEffect(() => setText(value == null ? "" : String(value)), [value]);
  const commit = () => {
    if (number) {
      const t = text.trim();
      onCommit(t === "" ? null : Number(t));
    } else onCommit(text);
  };
  return (
    <TextField
      size="small"
      fullWidth
      type={number ? "number" : "text"}
      value={text}
      disabled={disabled}
      multiline={!number && text.length > 60}
      maxRows={8}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          commit();
        }
      }}
      inputProps={{ "aria-label": label }}
      sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
    />
  );
}

TextLeaf.propTypes = {
  value: PropTypes.any,
  number: PropTypes.bool,
  onCommit: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  label: PropTypes.string,
};

function Field({ schema, value, steps, name, onChange, disabled }) {
  const t = Array.isArray(schema?.enum) ? "enum" : typeOfSchema(schema, value);
  const label = labelOf(steps, name);
  if (t === "enum") {
    const options = schema.enum;
    const known = options.some((o) => o === value);
    return (
      <Select
        size="small"
        fullWidth
        value={known ? JSON.stringify(value) : "__current__"}
        disabled={disabled}
        onChange={(e) =>
          e.target.value !== "__current__" &&
          onChange(steps, JSON.parse(e.target.value))
        }
        inputProps={{ "aria-label": label }}
        SelectDisplayProps={{ "data-testid": `form-select-${label}` }}
        sx={{ typography: "s2" }}
      >
        {!known && (
          <MenuItem value="__current__" disabled>
            {value == null || value === "" ? "empty" : String(value)} (not
            allowed)
          </MenuItem>
        )}
        {options.map((o) => (
          <MenuItem key={JSON.stringify(o)} value={JSON.stringify(o)}>
            {String(o)}
          </MenuItem>
        ))}
      </Select>
    );
  }
  if (t === "boolean")
    return (
      <Switch
        size="small"
        checked={value === true}
        disabled={disabled}
        onChange={(e) => onChange(steps, e.target.checked)}
        inputProps={{ "aria-label": label }}
      />
    );
  if (t === "number" || t === "integer")
    return (
      <TextLeaf
        value={value}
        number
        disabled={disabled}
        label={label}
        onCommit={(v) => onChange(steps, v)}
      />
    );
  if (t === "object") {
    const props = schema?.properties ?? {};
    const keys = [
      ...Object.keys(props),
      ...Object.keys(value && typeof value === "object" ? value : {}).filter(
        (k) => !(k in props),
      ),
    ];
    return (
      <Stack
        gap={1}
        sx={{
          pl: steps.length ? 1.25 : 0,
          borderLeft: steps.length ? "2px solid" : "none",
          borderColor: "divider",
        }}
      >
        {keys.map((k) => (
          <Row
            key={k}
            label={k}
            required={(schema?.required ?? []).includes(k)}
          >
            <Field
              schema={props[k] ?? {}}
              value={value?.[k]}
              steps={[...steps, { type: "key", key: k }]}
              name={name}
              onChange={onChange}
              disabled={disabled}
            />
          </Row>
        ))}
        {!keys.length && (
          <Typography typography="s2" color="text.disabled">
            No fields
          </Typography>
        )}
      </Stack>
    );
  }
  if (t === "array") {
    const list = Array.isArray(value) ? value : [];
    return (
      <Stack gap={0.75}>
        {list.map((item, i) => (
          <Stack key={i} direction="row" gap={0.5} alignItems="flex-start">
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ width: 22, pt: 1, flexShrink: 0 }}
            >
              {i + 1}
            </Typography>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Field
                schema={schema?.items ?? {}}
                value={item}
                steps={[...steps, { type: "index", index: i }]}
                name={name}
                onChange={onChange}
                disabled={disabled}
              />
            </Box>
            <IconButton
              size="small"
              disabled={disabled}
              aria-label={`Remove ${labelOf([...steps, { type: "index", index: i }], name)}`}
              onClick={() =>
                onChange(
                  steps,
                  list.filter((_, j) => j !== i),
                )
              }
            >
              <Iconify icon="mdi:close" width={16} />
            </IconButton>
          </Stack>
        ))}
        <Button
          size="small"
          variant="text"
          disabled={disabled}
          startIcon={<Iconify icon="mdi:plus" width={16} />}
          onClick={() => onChange(steps, [...list, emptyFor(schema?.items)])}
          sx={{ alignSelf: "flex-start", typography: "s2" }}
        >
          Add item
        </Button>
      </Stack>
    );
  }
  return (
    <TextLeaf
      value={value}
      disabled={disabled}
      label={label}
      onCommit={(v) => onChange(steps, v)}
    />
  );
}

Field.propTypes = {
  schema: PropTypes.object,
  value: PropTypes.any,
  steps: PropTypes.array.isRequired,
  name: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

function Row({ label, required, children }) {
  return (
    <Stack direction="row" gap={1.25} alignItems="flex-start">
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ width: 130, flexShrink: 0, pt: 0.875, overflowWrap: "anywhere" }}
      >
        {label}
        {required ? " *" : ""}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
    </Stack>
  );
}

Row.propTypes = {
  label: PropTypes.string,
  required: PropTypes.bool,
  children: PropTypes.node,
};

/**
 * SchemaForm — a field's value as inputs typed by its declared schema.
 * @param {{name: string, schema: object, value: *, dataType?: string,
 *   editable?: boolean, onCommit?: Function}} props  `onCommit(cellValue)`
 *   gets the value as the cell stores it, only when it passes the schema
 */
const SchemaForm = ({
  name,
  schema,
  value,
  dataType,
  editable = false,
  onCommit,
}) => {
  const parsed = useMemo(
    () => formValueOf(value, schema, dataType),
    [value, schema, dataType],
  );
  const [draft, setDraft] = useState(parsed);
  useEffect(() => setDraft(parsed), [parsed]);
  const issues = useMemo(
    () => validateValue(draft, schema, [{ type: "key", key: name }]),
    [draft, schema, name],
  );
  const change = (steps, next) => {
    const nextValue = setAt(draft, steps, next);
    setDraft(nextValue);
    const problems = validateValue(nextValue, schema, [
      { type: "key", key: name },
    ]);
    if (
      !problems.length &&
      JSON.stringify(nextValue) !== JSON.stringify(parsed)
    )
      onCommit?.(cellValueOf(nextValue, dataType, value));
  };
  return (
    <Stack gap={1} data-testid={`schema-form-${name}`}>
      <Field
        schema={schema}
        value={draft}
        steps={[]}
        name={name}
        onChange={change}
        disabled={!editable || !onCommit}
      />
      {issues.length > 0 && (
        <Stack gap={0.25} role="alert" data-testid="schema-form-issues">
          {issues.slice(0, 5).map((i) => (
            <Typography
              key={`${i.path}:${i.message}`}
              typography="s3"
              color="error.main"
            >
              {i.message}
            </Typography>
          ))}
          <Typography typography="s3" color="text.secondary">
            Fix these to add the change.
          </Typography>
        </Stack>
      )}
      {!editable && (
        <Typography typography="s3" color="text.secondary">
          Read-only here
        </Typography>
      )}
    </Stack>
  );
};

SchemaForm.propTypes = {
  name: PropTypes.string.isRequired,
  schema: PropTypes.object.isRequired,
  value: PropTypes.any,
  dataType: PropTypes.string,
  editable: PropTypes.bool,
  onCommit: PropTypes.func,
};

export default SchemaForm;
