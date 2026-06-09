import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Drawer,
  FormControl,
  FormControlLabel,
  IconButton,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { Controller, useForm } from "react-hook-form";
import {
  useCreateAnnotationLabel,
  useUpdateAnnotationLabel,
} from "src/api/annotation-labels/annotation-labels";
import CategoricalSettings from "./settings/categorical-settings";
import NumericSettings from "./settings/numeric-settings";
import TextSettings from "./settings/text-settings";
import StarSettings from "./settings/star-settings";
import LabelInput from "../queues/annotate/label-input";

const LABEL_TYPES = [
  {
    value: "categorical",
    label: "Categorical",
    description: "Predefined options to choose from",
  },
  {
    value: "numeric",
    label: "Numeric",
    description: "Score within a range",
  },
  {
    value: "text",
    label: "Text",
    description: "Free-text feedback",
  },
  {
    value: "star",
    label: "Star Rating",
    description: "Star-based rating",
  },
  {
    value: "thumbs_up_down",
    label: "Thumbs Up/Down",
    description: "Binary feedback",
  },
];

const DEFAULT_SETTINGS = {
  categorical: {
    rule_prompt: "",
    multi_choice: false,
    options: [{ label: "" }, { label: "" }],
    auto_annotate: false,
    strategy: null,
  },
  numeric: { min: 0, max: 10, step_size: 1, display_type: "slider" },
  text: {
    placeholder: "Enter your feedback...",
    max_length: 500,
    min_length: 0,
  },
  star: { no_of_stars: 5 },
  thumbs_up_down: {},
};

CreateLabelDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  editLabel: PropTypes.object,
  onCreated: PropTypes.func,
};

export default function CreateLabelDrawer({
  open,
  onClose,
  editLabel,
  onCreated,
}) {
  const isEdit = editLabel && editLabel.id && !editLabel._isDuplicate;
  const { mutate: createLabel, isPending: isCreating } =
    useCreateAnnotationLabel();
  const { mutate: updateLabel, isPending: isUpdating } =
    useUpdateAnnotationLabel();
  const isPending = isCreating || isUpdating;

  const [previewValue, setPreviewValue] = useState({});
  const [previewNotes, setPreviewNotes] = useState("");

  const { control, handleSubmit, watch, reset, setValue } = useForm({
    defaultValues: {
      name: "",
      description: "",
      type: "categorical",
      settings: DEFAULT_SETTINGS.categorical,
      allow_notes: false,
    },
  });

  const selectedType = watch("type");
  const watchedName = watch("name");
  const watchedDescription = watch("description");
  const watchedSettings = watch("settings");
  const watchedAllowNotes = watch("allow_notes");

  const initializedLabelIdRef = useRef(null);
  useEffect(() => {
    if (open && editLabel) {
      const labelKey = editLabel.id || editLabel.name;
      if (initializedLabelIdRef.current !== labelKey) {
        initializedLabelIdRef.current = labelKey;
        reset({
          name: editLabel.name || "",
          description: editLabel.description || "",
          type: editLabel.type || "categorical",
          settings:
            editLabel.settings || DEFAULT_SETTINGS[editLabel.type] || {},
          allow_notes: editLabel.allow_notes ?? editLabel.allowNotes ?? false,
        });
        setPreviewValue({});
        setPreviewNotes("");
      }
    } else if (open) {
      initializedLabelIdRef.current = null;
      reset({
        name: "",
        description: "",
        type: "categorical",
        settings: DEFAULT_SETTINGS.categorical,
        allow_notes: false,
      });
      setPreviewValue({});
      setPreviewNotes("");
    }
    if (!open) {
      initializedLabelIdRef.current = null;
    }
  }, [open, editLabel, reset]);

  const handleTypeChange = (newType) => {
    setValue("type", newType);
    setValue("settings", DEFAULT_SETTINGS[newType] || {});
    setPreviewValue({});
  };

  const onSubmit = (formData) => {
    const payload = {
      name: formData.name,
      type: formData.type,
      description: formData.description || "",
      settings: formData.settings,
      allow_notes: formData.allow_notes,
    };

    if (isEdit) {
      updateLabel(
        { id: editLabel.id, ...payload },
        { onSuccess: () => onClose() },
      );
    } else {
      createLabel(payload, {
        onSuccess: (response) => {
          onCreated?.(response?.data?.result || response?.data);
          onClose();
        },
      });
    }
  };

  // Build a preview label object from current form state
  const previewLabel = {
    name: watchedName || "Label Name",
    description: watchedDescription || "",
    type: selectedType,
    settings: buildPreviewSettings(selectedType, watchedSettings),
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: { xs: "100%", md: "60vw" },
          minWidth: 850,
          height: "100vh",
          borderRadius: "0 !important",
        },
      }}
    >
      <Box
        component="form"
        onSubmit={(e) => {
          e.stopPropagation();
          handleSubmit(onSubmit)(e);
        }}
        sx={{ display: "flex", flexDirection: "column", height: "100%" }}
      >
        {/* Header */}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{
            px: 3,
            py: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography variant="h6">
            {isEdit ? "Edit Label" : "Create Label"}
          </Typography>
          <IconButton onClick={onClose} size="small">
            <Iconify icon="mingcute:close-line" />
          </IconButton>
        </Stack>

        {/* Body — two columns */}
        <Box sx={{ display: "flex", flex: 1, overflow: "hidden" }}>
          {/* Left: Live Preview */}
          <Box
            sx={{
              width: 340,
              minWidth: 300,
              borderRight: "1px solid",
              borderColor: "divider",
              overflow: "auto",
              bgcolor: "background.paper",
              px: 3,
              py: 3,
            }}
          >
            <Typography
              variant="overline"
              color="text.secondary"
              sx={{ mb: 2, display: "block" }}
            >
              Preview
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mb: 2, display: "block" }}
            >
              This is how the label will appear to annotators.
            </Typography>

            <LabelInput
              label={previewLabel}
              value={previewValue}
              onChange={setPreviewValue}
            />

            {watchedAllowNotes && (
              <TextField
                multiline
                minRows={2}
                maxRows={4}
                fullWidth
                size="small"
                placeholder="Add notes..."
                value={previewNotes}
                onChange={(e) => setPreviewNotes(e.target.value)}
                sx={{ mt: 2 }}
              />
            )}
          </Box>

          {/* Right: Form */}
          <Box sx={{ flex: 1, overflow: "auto", px: 3, py: 2 }}>
            <Stack spacing={2.5}>
              {/* Name */}
              <Controller
                name="name"
                control={control}
                rules={{ required: "Name is required" }}
                render={({ field, fieldState }) => (
                  <TextField
                    {...field}
                    label="Name"
                    placeholder="e.g. Relevance, Tone, Accuracy"
                    fullWidth
                    size="small"
                    required
                    error={!!fieldState.error}
                    helperText={fieldState.error?.message}
                    inputProps={{ maxLength: 255 }}
                  />
                )}
              />

              {/* Description */}
              <Controller
                name="description"
                control={control}
                render={({ field }) => (
                  <TextField
                    {...field}
                    label="Description"
                    placeholder="Describe what this label measures or evaluates"
                    fullWidth
                    multiline
                    rows={2}
                  />
                )}
              />

              {/* Type */}
              <FormControl>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  Type {isEdit && "(cannot be changed)"}
                </Typography>
                <Controller
                  name="type"
                  control={control}
                  render={({ field }) => (
                    <RadioGroup
                      {...field}
                      onChange={(e) => {
                        if (!isEdit) {
                          handleTypeChange(e.target.value);
                        }
                      }}
                    >
                      {LABEL_TYPES.map((lt) => (
                        <FormControlLabel
                          key={lt.value}
                          value={lt.value}
                          control={<Radio disabled={isEdit} size="small" />}
                          label={
                            <Box>
                              <Typography
                                variant="body2"
                                fontWeight={500}
                                color="text.primary"
                              >
                                {lt.label}
                              </Typography>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                              >
                                {lt.description}
                              </Typography>
                            </Box>
                          }
                          sx={{ mb: 0.5, alignItems: "flex-start" }}
                        />
                      ))}
                    </RadioGroup>
                  )}
                />
              </FormControl>

              {/* Type-specific settings */}
              {selectedType === "categorical" && (
                <CategoricalSettings control={control} />
              )}
              {selectedType === "numeric" && (
                <NumericSettings control={control} />
              )}
              {selectedType === "text" && <TextSettings control={control} />}
              {selectedType === "star" && <StarSettings control={control} />}
              {selectedType === "thumbs_up_down" && (
                <Typography variant="body2" color="text.secondary">
                  No additional configuration needed.
                </Typography>
              )}

              {/* Allow Notes */}
              <Controller
                name="allow_notes"
                control={control}
                render={({ field }) => (
                  <FormControlLabel
                    control={
                      <Checkbox
                        checked={field.value || false}
                        onChange={(e) => field.onChange(e.target.checked)}
                      />
                    }
                    label={
                      <Box>
                        <Typography variant="body2">Allow notes</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Annotators can add free-text notes alongside this
                          label
                        </Typography>
                      </Box>
                    }
                    sx={{ alignItems: "flex-start" }}
                  />
                )}
              />
            </Stack>
          </Box>
        </Box>

        {/* Footer */}
        <Stack
          direction="row"
          spacing={2}
          justifyContent="flex-end"
          sx={{ px: 3, py: 2, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Button
            variant="outlined"
            color="primary"
            onClick={onClose}
            disabled={isPending}
            sx={{ minWidth: 160 }}
          >
            Cancel
          </Button>
          <Button
            color="primary"
            type="submit"
            variant="contained"
            disabled={isPending}
            sx={{ minWidth: 160 }}
          >
            {isEdit ? "Save" : "Create"}
          </Button>
        </Stack>
      </Box>
    </Drawer>
  );
}

/**
 * Build a settings object suitable for the LabelInput preview.
 * Maps form field names to what LabelInput expects.
 */
function buildPreviewSettings(type, settings) {
  if (!settings) return {};

  if (type === "categorical") {
    const options = (settings.options || [])
      .map((o) => (typeof o === "string" ? o : o.label))
      .filter(Boolean);
    return {
      options: options.length > 0 ? options : ["Option 1", "Option 2"],
      multi_choice: settings.multi_choice || false,
    };
  }

  if (type === "numeric") {
    return {
      min: settings.min ?? 0,
      max: settings.max ?? 10,
      step: settings.step_size ?? settings.step ?? 1,
    };
  }

  if (type === "text") {
    return {
      placeholder: settings.placeholder || "Enter text...",
      max_length: settings.max_length,
      min_length: settings.min_length,
    };
  }

  if (type === "star") {
    return { no_of_stars: settings.no_of_stars || 5 };
  }

  return settings;
}
