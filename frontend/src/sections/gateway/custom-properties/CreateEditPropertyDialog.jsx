import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import {
  buildCustomPropertyPayload,
  hasStoredDefault,
  RULES_CHECKED_BY_VALIDATE_API,
  typeRefusal,
} from "./customPropertyForm";
import { reservedReason } from "./reservedNames";

const PROPERTY_TYPES = [
  { value: "string", label: "Text" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "Boolean" },
  { value: "enum", label: "One of a list" },
];

const EMPTY_FORM = {
  name: "",
  description: "",
  property_type: "string",
  required: false,
  allowed_values: [],
  clearStoredDefault: false,
};

// Add or edit a custom property: its name, description and type, and the
// two rules the Validate API checks, Required and Allowed values. A Default
// has no reader anywhere, so there is no Default field: one stored before is
// shown read-only as not applied, is kept by an edit that does not touch it,
// and can be removed on purpose.
const CreateEditPropertyDialog = ({
  open,
  onClose,
  onSubmit,
  property,
  isPending,
}) => {
  const [form, setForm] = useState(EMPTY_FORM);
  const [newValue, setNewValue] = useState("");
  const isEdit = Boolean(property);

  useEffect(() => {
    if (property) {
      setForm({
        name: property.name || "",
        description: property.description || "",
        property_type: property.property_type || "string",
        required: Boolean(property.required),
        allowed_values: property.allowed_values || [],
        clearStoredDefault: false,
      });
    } else {
      setForm(EMPTY_FORM);
    }
    setNewValue("");
  }, [property, open]);

  const field = (name) => (event) =>
    setForm((f) => ({ ...f, [name]: event.target.value }));

  const addValue = () => {
    const value = newValue.trim();
    if (!value || form.allowed_values.includes(value)) return;
    setForm((f) => ({ ...f, allowed_values: [...f.allowed_values, value] }));
    setNewValue("");
  };

  const removeValue = (value) =>
    setForm((f) => ({
      ...f,
      allowed_values: f.allowed_values.filter((v) => v !== value),
    }));

  const submit = () =>
    onSubmit(buildCustomPropertyPayload(form, isEdit ? property.id : null));

  const showStoredDefault =
    isEdit && hasStoredDefault(property) && !form.clearStoredDefault;
  const keptDefault = showStoredDefault ? property.default_value : null;

  // A name the gateway reserves is refused on create; an existing one (from
  // before this check) can still be edited, and says why it is not recorded.
  const reserved = reservedReason(form.name);
  const isValid =
    form.name.trim() &&
    !(reserved && !isEdit) &&
    (form.property_type !== "enum" || form.allowed_values.length > 0);
  const types = PROPERTY_TYPES.map((t) => ({
    ...t,
    // A type a kept stored default does not fit would be refused at save:
    // not offered, with why (removing the stored default lifts it).
    refusal:
      isEdit && t.value !== property.property_type
        ? typeRefusal(t.value, keptDefault)
        : "",
  }));
  const someRefused = types.some((t) => t.refusal);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEdit ? "Edit property" : "Add property"}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <TextField
            label="Name"
            value={form.name}
            onChange={field("name")}
            fullWidth
            required
            size="small"
            disabled={isEdit}
            error={Boolean(reserved)}
            helperText={
              reserved ||
              "The key your requests send, for example tenant or feature."
            }
          />
          <TextField
            label="Description"
            value={form.description}
            onChange={field("description")}
            fullWidth
            size="small"
            multiline
            rows={2}
          />
          <TextField
            select
            label="Type"
            value={form.property_type}
            onChange={field("property_type")}
            fullWidth
            size="small"
            SelectProps={{
              renderValue: (value) =>
                types.find((t) => t.value === value)?.label || value,
            }}
            helperText={
              someRefused
                ? "A type the stored default does not fit cannot be saved. Remove the stored default to choose it."
                : undefined
            }
          >
            {types.map((t) => (
              <MenuItem key={t.value} value={t.value} disabled={!!t.refusal}>
                {t.refusal ? (
                  <Stack>
                    <Typography variant="body2">{t.label}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {t.refusal}
                    </Typography>
                  </Stack>
                ) : (
                  t.label
                )}
              </MenuItem>
            ))}
          </TextField>

          <Box>
            <FormControlLabel
              control={
                <Switch
                  checked={form.required}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, required: e.target.checked }))
                  }
                />
              }
              label="Required by the Validate API"
            />
            <Typography typography="s2" color="text.secondary">
              {RULES_CHECKED_BY_VALIDATE_API}
            </Typography>
          </Box>

          {form.property_type === "enum" && (
            <Box>
              <Typography typography="s1" fontWeight="fontWeightMedium" mb={1}>
                Allowed values, checked by the Validate API
              </Typography>
              <Stack direction="row" spacing={1} mb={1}>
                <TextField
                  size="small"
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  placeholder="Add a value"
                  inputProps={{ "aria-label": "Allowed value" }}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter") return;
                    e.preventDefault();
                    addValue();
                  }}
                />
                <Button size="small" variant="outlined" onClick={addValue}>
                  Add
                </Button>
              </Stack>
              <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                {form.allowed_values.map((value) => (
                  <Chip
                    variant="outlined"
                    key={String(value)}
                    label={String(value)}
                    size="small"
                    onDelete={() => removeValue(value)}
                  />
                ))}
              </Stack>
            </Box>
          )}

          {showStoredDefault && (
            <Alert
              severity="info"
              variant="outlined"
              action={
                <Button
                  size="small"
                  onClick={() =>
                    setForm((f) => ({ ...f, clearStoredDefault: true }))
                  }
                >
                  Remove stored default
                </Button>
              }
            >
              Stored default: {String(property.default_value)}. Not applied: the
              gateway and the request logs never fill in a default.
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={submit}
          disabled={!isValid || isPending}
        >
          {isPending ? "Saving…" : isEdit ? "Save" : "Create"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CreateEditPropertyDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onSubmit: PropTypes.func.isRequired,
  property: PropTypes.object,
  isPending: PropTypes.bool,
};

export default CreateEditPropertyDialog;
