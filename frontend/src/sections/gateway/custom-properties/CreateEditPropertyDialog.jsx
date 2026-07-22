import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  FormControlLabel,
  Switch,
  Typography,
  Chip,
  Box,
} from "@mui/material";
import PropTypes from "prop-types";
import {
  buildCustomPropertyPayload,
  getDefaultValueError,
} from "./customPropertyForm";

const PROPERTY_TYPES = [
  { value: "string", label: "String" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "Boolean" },
  { value: "enum", label: "Enum" },
];

const EMPTY_FORM = {
  name: "",
  description: "",
  property_type: "string",
  required: false,
  allowed_values: [],
  default_value: "",
};

const CreateEditPropertyDialog = ({
  open,
  onClose,
  onSubmit,
  property,
  isPending,
}) => {
  const [form, setForm] = useState(EMPTY_FORM);
  const [newEnumValue, setNewEnumValue] = useState("");

  const isEdit = Boolean(property);

  useEffect(() => {
    if (property) {
      setForm({
        name: property.name || "",
        description: property.description || "",
        property_type: property.property_type || "string",
        required: property.required || false,
        allowed_values: property.allowed_values || [],
        default_value:
          property.default_value != null ? String(property.default_value) : "",
      });
    } else {
      setForm(EMPTY_FORM);
    }
    setNewEnumValue("");
  }, [property, open]);

  const handleField = (field) => (e) =>
    setForm((f) => ({ ...f, [field]: e.target.value }));

  const addEnumValue = () => {
    if (
      newEnumValue.trim() &&
      !form.allowed_values.includes(newEnumValue.trim())
    ) {
      setForm((f) => ({
        ...f,
        allowed_values: [...f.allowed_values, newEnumValue.trim()],
      }));
      setNewEnumValue("");
    }
  };

  const removeEnumValue = (val) =>
    setForm((f) => ({
      ...f,
      allowed_values: f.allowed_values.filter((v) => v !== val),
    }));

  const handleSubmit = () => {
    const payload = buildCustomPropertyPayload(form);
    if (isEdit) payload.id = property.id;
    onSubmit(payload);
  };

  const defaultValueError = getDefaultValueError(form);
  const isValid =
    form.name.trim() &&
    (form.property_type !== "enum" || form.allowed_values.length > 0) &&
    !defaultValueError;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {isEdit ? "Edit Property Schema" : "Create Property Schema"}
      </DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <TextField
            label="Property Name"
            value={form.name}
            onChange={handleField("name")}
            fullWidth
            required
            disabled={isEdit}
          />

          <TextField
            label="Description"
            value={form.description}
            onChange={handleField("description")}
            fullWidth
            multiline
            rows={2}
          />

          <FormControl fullWidth>
            <InputLabel>Type</InputLabel>
            <Select
              value={form.property_type}
              onChange={handleField("property_type")}
              label="Type"
            >
              {PROPERTY_TYPES.map((t) => (
                <MenuItem key={t.value} value={t.value}>
                  {t.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <FormControlLabel
            control={
              <Switch
                checked={form.required}
                onChange={(e) =>
                  setForm((f) => ({ ...f, required: e.target.checked }))
                }
              />
            }
            label="Required"
          />

          {form.property_type === "enum" && (
            <Box>
              <Typography variant="subtitle2" mb={1}>
                Allowed Values
              </Typography>
              <Stack direction="row" spacing={1} mb={1}>
                <TextField
                  size="small"
                  value={newEnumValue}
                  onChange={(e) => setNewEnumValue(e.target.value)}
                  placeholder="Add enum value"
                  onKeyDown={(e) =>
                    e.key === "Enter" && (e.preventDefault(), addEnumValue())
                  }
                />
                <Button size="small" variant="outlined" onClick={addEnumValue}>
                  Add
                </Button>
              </Stack>
              <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                {form.allowed_values.map((val) => (
                  <Chip
                    key={val}
                    label={val}
                    size="small"
                    onDelete={() => removeEnumValue(val)}
                  />
                ))}
              </Stack>
            </Box>
          )}

          <TextField
            label="Default Value (optional)"
            value={form.default_value}
            onChange={handleField("default_value")}
            error={Boolean(defaultValueError)}
            helperText={defaultValueError}
            fullWidth
            placeholder={
              form.property_type === "boolean"
                ? "true or false"
                : form.property_type === "number"
                  ? "0"
                  : form.property_type === "enum"
                    ? "Select from allowed values"
                    : "Default string value"
            }
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={!isValid || isPending}
        >
          {isPending ? "Saving..." : isEdit ? "Update" : "Create"}
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
