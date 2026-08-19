import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, TextField, InputAdornment, IconButton,
  Switch, Button,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { FIELD } from "../../_mock/agentTypes";

/**
 * Renders one field from an agent type's schema.
 *
 * Agent types differ so much that hand-writing a form per type would guarantee
 * they drift apart. The schema lives with the type; this renders whatever it
 * declares, so adding a new agent kind is a data change, not a new screen.
 */
export default function DynamicField({ field, value, onChange, values }) {
  const [reveal, setReveal] = useState(false);

  // Conditional visibility (e.g. hide the token field when auth is "none").
  if (field.dependsOn) {
    const dep = values?.[field.dependsOn.key];
    if (field.dependsOn.not != null && dep === field.dependsOn.not) return null;
    if (field.dependsOn.eq != null && dep !== field.dependsOn.eq) return null;
  }

  const label = (
    <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mb: 0.625 }}>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>{field.label}</Typography>
      {field.required && <Box component="span" sx={{ color: "error.main", typography: "s2" }}>*</Box>}
    </Stack>
  );

  const help = field.help && (
    <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.625 }}>{field.help}</Typography>
  );

  switch (field.type) {
    case FIELD.SWITCH:
      return (
        <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={2}>
          <Box>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>{field.label}</Typography>
            {field.help && (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{field.help}</Typography>
            )}
          </Box>
          <Switch
            size="small"
            checked={value ?? field.default ?? false}
            onChange={(e) => onChange(e.target.checked)}
          />
        </Stack>
      );

    case FIELD.SELECT:
      return (
        <Box>
          {label}
          <TextField
            select
            fullWidth
            size="small"
            SelectProps={{ native: true }}
            value={value ?? ""}
            onChange={(e) => onChange(e.target.value)}
            sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
          >
            <option value="">Select…</option>
            {field.options.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </TextField>
          {help}
        </Box>
      );

    case FIELD.RADIO_CARD:
      return (
        <Box>
          {label}
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            {field.options.map((o) => {
              const selected = value === o.value;
              return (
                <Box
                  key={o.value}
                  onClick={() => onChange(o.value)}
                  sx={{
                    flex: 1, p: 1.5, borderRadius: 1, cursor: "pointer",
                    border: "1px solid",
                    borderColor: selected ? "primary.main" : "divider",
                    bgcolor: (t) => selected ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.14 : 0.06) : "transparent",
                    transition: "border-color .15s ease, background-color .15s ease",
                    "&:hover": { borderColor: selected ? "primary.main" : "text.subtitle" },
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.25 }}>
                    <Iconify
                      icon={selected ? "solar:check-circle-bold" : "solar:record-circle-linear"}
                      width={15}
                      sx={{ color: selected ? "primary.main" : "text.subtitle" }}
                    />
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>{o.label}</Typography>
                  </Stack>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{o.desc}</Typography>
                </Box>
              );
            })}
          </Stack>
          {help}
        </Box>
      );

    case FIELD.TEXTAREA:
      return (
        <Box>
          {label}
          <TextField
            fullWidth multiline minRows={3} size="small"
            value={value ?? ""}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value)}
            sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
          />
          {help}
        </Box>
      );

    case FIELD.SECRET:
      return (
        <Box>
          {label}
          <TextField
            fullWidth size="small"
            type={reveal ? "text" : "password"}
            value={value ?? ""}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value)}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton size="small" onClick={() => setReveal((r) => !r)}>
                    <Iconify
                      icon={reveal ? "solar:eye-closed-bold" : "solar:eye-bold"}
                      width={15}
                      sx={{ color: "text.subtitle" }}
                    />
                  </IconButton>
                </InputAdornment>
              ),
            }}
            sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
          />
          {help}
        </Box>
      );

    case FIELD.KEYVALUE:
      return <KeyValueField value={value} onChange={onChange} label={label} help={help} />;

    default:
      return (
        <Box>
          {label}
          <TextField
            fullWidth size="small"
            value={value ?? ""}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value)}
            sx={{
              "& .MuiInputBase-root": {
                typography: "s2",
                ...(field.type === FIELD.URL && {
                  fontFamily: "ui-monospace, Menlo, monospace",
                }),
              },
            }}
          />
          {help}
        </Box>
      );
  }
}

DynamicField.propTypes = {
  field: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  values: PropTypes.object,
};

function KeyValueField({ value, onChange, label, help }) {
  const pairs = value?.length ? value : [{ k: "", v: "" }];
  const update = (i, patch) => {
    const next = pairs.map((p, idx) => (idx === i ? { ...p, ...patch } : p));
    onChange(next);
  };
  return (
    <Box>
      {label}
      <Stack spacing={0.75}>
        {pairs.map((p, i) => (
          <Stack key={i} direction="row" spacing={0.75} alignItems="center">
            <TextField
              size="small" placeholder="Key" value={p.k}
              onChange={(e) => update(i, { k: e.target.value })}
              sx={{ flex: 1, "& .MuiInputBase-root": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
            />
            <TextField
              size="small" placeholder="Value" value={p.v}
              onChange={(e) => update(i, { v: e.target.value })}
              sx={{ flex: 1.4, "& .MuiInputBase-root": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
            />
            <IconButton
              size="small"
              onClick={() => onChange(pairs.filter((_, idx) => idx !== i))}
              disabled={pairs.length === 1}
            >
              <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Stack>
        ))}
      </Stack>
      <Button
        size="small"
        startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
        onClick={() => onChange([...pairs, { k: "", v: "" }])}
        sx={{ mt: 0.75, typography: "s3", color: "text.secondary" }}
      >
        Add row
      </Button>
      {help}
    </Box>
  );
}
KeyValueField.propTypes = {
  value: PropTypes.array, onChange: PropTypes.func,
  label: PropTypes.node, help: PropTypes.node,
};
