// Parameter controls from a ParamSchema (R4-SPEC §6, D8; lane Ra MUST 5):
// `model` → gateway dropdown with price · `prompt` → editor dialog with a
// line count · `int` / `float` → field + slider · `bool` → switch · `enum`
// → select · `string` → text. Writes `config.params.values`. Used in
// section 3 and in the Rerun dialog (`original` marks changed fields with
// the old value; `disabled` names undeclared fields with the reason).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Select,
  Slider,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { fmtCost } from "src/sections/common/format/fmt";
import { useRunSourceCatalog } from "src/api/develop/r4";
import { clampParam, paramDefaults } from "./sourceModel";

const lineCount = (s) => String(s ?? "").split("\n").length;
const show = (v) =>
  typeof v === "string"
    ? v.length > 32
      ? `${v.slice(0, 30)}…`
      : v
    : JSON.stringify(v);

function PromptField({ p, value, onChange, disabled }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value ?? "");
  const n = lineCount(value);
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        disabled={disabled}
        onClick={() => {
          setDraft(value ?? "");
          setOpen(true);
        }}
        sx={{
          typography: "s2",
          color: "text.primary",
          borderColor: "divider",
          justifyContent: "flex-start",
        }}
      >
        {`Edit prompt · ${n} ${n === 1 ? "line" : "lines"}`}
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle typography="m3">{p.label ?? p.name}</DialogTitle>
        <DialogContent>
          <TextField
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            multiline
            minRows={6}
            maxRows={18}
            fullWidth
            autoFocus
            inputProps={{
              "aria-label": p.label ?? p.name,
              style: { fontFamily: "monospace", fontSize: 13 },
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() => {
              onChange(draft);
              setOpen(false);
            }}
          >
            Save prompt
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
PromptField.propTypes = {
  p: PropTypes.object.isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

function NumberField({ p, value, onChange, disabled }) {
  const step = p.step ?? (p.type === "int" ? 1 : 0.1);
  const hasRange = Number.isFinite(p.min) && Number.isFinite(p.max);
  return (
    <Stack direction="row" alignItems="center" gap={1.5} sx={{ minWidth: 0 }}>
      <TextField
        size="small"
        type="number"
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) =>
          onChange(
            e.target.value === "" ? p.default : clampParam(p, e.target.value),
          )
        }
        inputProps={{
          step,
          min: p.min,
          max: p.max,
          "aria-label": p.label ?? p.name,
          style: { textAlign: "right" },
        }}
        sx={{ width: 92, flexShrink: 0 }}
      />
      {hasRange ? (
        <Slider
          size="small"
          value={Number(value ?? p.default ?? 0)}
          min={p.min}
          max={p.max}
          step={step}
          disabled={disabled}
          onChange={(_, v) => onChange(clampParam(p, v))}
          aria-label={`${p.label ?? p.name} slider`}
          sx={{ flex: 1, minWidth: 80 }}
        />
      ) : null}
    </Stack>
  );
}
NumberField.propTypes = {
  p: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

function ModelField({ p, value, onChange, disabled }) {
  const { data } = useRunSourceCatalog();
  const models = (data?.models ?? []).filter(
    (m) => !m.type || m.type === "llm",
  );
  const ids = [
    ...new Set(
      [...(p.options ?? []), ...models.map((m) => m.id), value].filter(Boolean),
    ),
  ];
  const byId = Object.fromEntries(models.map((m) => [m.id, m]));
  const price = (id) => {
    const m = byId[id];
    return Number.isFinite(m?.input_per_1k)
      ? `${fmtCost(m.input_per_1k * 1000)} / 1M in`
      : null;
  };
  return (
    <Select
      size="small"
      value={value ?? ""}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      inputProps={{ "aria-label": p.label ?? p.name }}
      sx={{ minWidth: 220, height: 32 }}
      renderValue={(v) => v}
    >
      {ids.map((id) => (
        <MenuItem key={id} value={id} sx={{ gap: 1 }}>
          <Typography typography="s2" sx={{ flex: 1 }}>
            {id}
          </Typography>
          {price(id) ? (
            <Typography typography="s3" color="text.secondary">
              {price(id)}
            </Typography>
          ) : null}
        </MenuItem>
      ))}
    </Select>
  );
}
ModelField.propTypes = {
  p: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

function Control({ p, value, onChange, disabled }) {
  if (p.type === "model")
    return (
      <ModelField p={p} value={value} onChange={onChange} disabled={disabled} />
    );
  if (p.type === "prompt")
    return (
      <PromptField
        p={p}
        value={value}
        onChange={onChange}
        disabled={disabled}
      />
    );
  if (p.type === "int" || p.type === "float")
    return (
      <NumberField
        p={p}
        value={value}
        onChange={onChange}
        disabled={disabled}
      />
    );
  if (p.type === "bool")
    return (
      <Switch
        size="small"
        checked={Boolean(value)}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        inputProps={{ "aria-label": p.label ?? p.name }}
      />
    );
  if (p.type === "enum")
    return (
      <Select
        size="small"
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        sx={{ minWidth: 160, height: 32 }}
        inputProps={{ "aria-label": p.label ?? p.name }}
      >
        {(p.options ?? []).map((o) => (
          <MenuItem key={String(o)} value={o}>
            {String(o)}
          </MenuItem>
        ))}
      </Select>
    );
  return (
    <TextField
      size="small"
      value={value ?? ""}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      inputProps={{ "aria-label": p.label ?? p.name }}
      sx={{ minWidth: 220 }}
    />
  );
}
Control.propTypes = {
  p: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

export default function ParamControls({
  schema = [],
  values = {},
  onChange,
  original,
  disabled = {},
  showReset = true,
}) {
  const set = (name, v) => onChange({ ...values, [name]: v });
  const defaults = paramDefaults(schema);
  const isDefault = schema.every(
    (p) => JSON.stringify(values[p.name]) === JSON.stringify(defaults[p.name]),
  );
  return (
    <Stack spacing={1} data-testid="param-controls">
      {schema.map((p) => {
        const reason = disabled[p.name];
        const old = original?.[p.name];
        const changed =
          original && JSON.stringify(old) !== JSON.stringify(values[p.name]);
        return (
          <Box
            key={p.name}
            data-param={p.name}
            data-changed={changed ? "1" : undefined}
            sx={{
              display: "grid",
              gridTemplateColumns: "150px minmax(0, 1fr)",
              alignItems: "center",
              gap: 1.5,
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              <CustomTooltip
                show={Boolean(p.help)}
                title={p.help ?? ""}
                size="small"
                placement="top"
              >
                <Typography
                  typography="s2"
                  fontWeight="fontWeightMedium"
                  noWrap
                >
                  {p.label ?? p.name}
                </Typography>
              </CustomTooltip>
              <Typography
                typography="s3"
                color="text.disabled"
                noWrap
                sx={{ fontFamily: "monospace" }}
              >
                {p.name}
              </Typography>
            </Box>
            <Stack
              direction="row"
              alignItems="center"
              gap={1}
              sx={{ minWidth: 0 }}
            >
              <CustomTooltip
                show={Boolean(reason)}
                title={reason ?? ""}
                size="small"
                placement="top"
              >
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Control
                    p={p}
                    value={values[p.name]}
                    onChange={(v) => set(p.name, v)}
                    disabled={Boolean(reason)}
                  />
                </Box>
              </CustomTooltip>
              {changed ? (
                <Typography
                  typography="s3"
                  sx={{ color: "warning.dark", flexShrink: 0 }}
                  noWrap
                >
                  {`changed · was ${show(old)}`}
                </Typography>
              ) : null}
            </Stack>
          </Box>
        );
      })}
      {showReset && schema.length ? (
        <Box>
          <Button
            size="small"
            disabled={isDefault}
            onClick={() => onChange(defaults)}
            sx={{ typography: "s2", px: 0.5, minWidth: 0 }}
          >
            Reset to code defaults
          </Button>
        </Box>
      ) : null}
    </Stack>
  );
}

ParamControls.propTypes = {
  schema: PropTypes.array,
  values: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  original: PropTypes.object,
  disabled: PropTypes.object,
  showReset: PropTypes.bool,
};
