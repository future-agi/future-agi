import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Collapse,
  FormControlLabel,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { MAX_ATTRIBUTES, attributeError } from "./insightsLenses";

const TYPES = [
  { value: "boolean", label: "Yes / no" },
  { value: "number", label: "Number" },
  { value: "string", label: "Text" },
];

const emptyAttribute = () => ({
  key: "",
  type: "boolean",
  description: "",
  filter_by: false,
});

// `compact` wraps each attribute onto two rows for a narrow drawer.
export function AttributesEditor({ attributes, onChange, disabled, compact }) {
  const update = (index, patch) =>
    onChange(
      attributes.map((attribute, i) =>
        i === index
          ? {
              ...attribute,
              ...patch,
              ...(patch.type && patch.type !== "boolean"
                ? { filter_by: false }
                : {}),
            }
          : attribute,
      ),
    );
  return (
    <Stack spacing={1.25}>
      {attributes.map((attribute, index) => {
        const error = attributeError(attribute, attributes);
        return (
          <Stack
            key={index}
            direction="row"
            alignItems="flex-start"
            useFlexGap
            flexWrap={compact ? "wrap" : "nowrap"}
            columnGap={1}
          >
            <TextField
              size="small"
              label="Key"
              placeholder="user_frustrated"
              value={attribute.key}
              onChange={(event) =>
                update(index, {
                  key: event.target.value.slice(0, 60).replace(/\s+/g, "_"),
                })
              }
              error={Boolean(error)}
              helperText={error || " "}
              disabled={disabled}
              sx={{ width: compact ? 180 : 200, flexShrink: 0 }}
              inputProps={{ "aria-label": `Attribute ${index + 1} key` }}
            />
            <TextField
              size="small"
              select
              label="Type"
              value={attribute.type}
              onChange={(event) => update(index, { type: event.target.value })}
              disabled={disabled}
              sx={{ width: 120, flexShrink: 0 }}
            >
              {TYPES.map((type) => (
                <MenuItem key={type.value} value={type.value}>
                  {type.label}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              label="What it means"
              placeholder="The user sounds frustrated or repeats themselves"
              value={attribute.description}
              onChange={(event) =>
                update(index, { description: event.target.value.slice(0, 300) })
              }
              disabled={disabled}
              helperText=" "
              sx={
                compact
                  ? { order: 5, flex: "1 1 100%", mt: -0.5 }
                  : { flex: 1, minWidth: 0 }
              }
            />
            <Tooltip
              describeChild
              arrow
              title={
                attribute.type === "boolean"
                  ? "Leave out every item where this is false, before grouping"
                  : "Only yes/no attributes can filter"
              }
            >
              <FormControlLabel
                sx={{ mr: 0, mt: 0.5, flexShrink: 0 }}
                control={
                  <Checkbox
                    size="small"
                    checked={Boolean(attribute.filter_by)}
                    disabled={disabled || attribute.type !== "boolean"}
                    onChange={(event) =>
                      update(index, { filter_by: event.target.checked })
                    }
                  />
                }
                label={
                  <Typography variant="caption">
                    Only include when true
                  </Typography>
                }
              />
            </Tooltip>
            <IconButton
              size="small"
              aria-label={`Remove attribute ${attribute.key || index + 1}`}
              onClick={() => onChange(attributes.filter((_, i) => i !== index))}
              disabled={disabled}
              sx={{ mt: 0.5 }}
            >
              <Iconify icon="mdi:close" width={16} />
            </IconButton>
          </Stack>
        );
      })}
      <Box>
        <Button
          size="small"
          color="inherit"
          startIcon={<Iconify icon="mdi:plus" width={14} />}
          onClick={() => onChange([...attributes, emptyAttribute()])}
          disabled={disabled || attributes.length >= MAX_ATTRIBUTES}
        >
          Add attribute
        </Button>
        <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
          {attributes.length >= MAX_ATTRIBUTES
            ? `Up to ${MAX_ATTRIBUTES} attributes.`
            : "Facts read from every item, then compared across categories."}
        </Typography>
      </Box>
    </Stack>
  );
}

AttributesEditor.propTypes = {
  attributes: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  compact: PropTypes.bool,
};

/** Step 3: optional name, agent description, instructions and attributes. */
export default function NewReportTune({
  open,
  onToggle,
  draft,
  onChange,
  namePlaceholder,
  disabled,
  draftAction,
  draftNote,
}) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Button
          color="inherit"
          onClick={onToggle}
          aria-expanded={open}
          startIcon={
            <Iconify
              icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
              width={18}
            />
          }
          sx={{ px: 0.5, fontWeight: 600 }}
        >
          Tune the report
        </Button>
        <Typography variant="caption" color="text.secondary">
          Optional
        </Typography>
        <Box sx={{ flex: 1 }} />
        {draftAction}
      </Stack>
      {draftNote}
      <Collapse in={open} unmountOnExit>
        <Stack spacing={2} sx={{ pt: 2 }}>
          <TextField
            size="small"
            label="Report name"
            placeholder={namePlaceholder}
            value={draft.name}
            onChange={(event) =>
              onChange({ name: event.target.value.slice(0, 255) })
            }
            disabled={disabled}
            InputLabelProps={{ shrink: true }}
          />
          <TextField
            size="small"
            label="What the agent does"
            placeholder="A support assistant for an online store that answers order, refund and product questions"
            value={draft.agentDescription}
            onChange={(event) =>
              onChange({ agentDescription: event.target.value.slice(0, 500) })
            }
            disabled={disabled}
            multiline
            minRows={2}
            InputLabelProps={{ shrink: true }}
            helperText="Helps the report tell the agent's mistakes from the user's."
          />
          <TextField
            size="small"
            label="Extra instructions"
            placeholder="Ignore greetings. Treat a handoff to a human as a failure."
            value={draft.instructions}
            onChange={(event) =>
              onChange({ instructions: event.target.value.slice(0, 1000) })
            }
            disabled={disabled}
            multiline
            minRows={2}
            InputLabelProps={{ shrink: true }}
          />
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 0.25 }}>
              Attributes
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mb: 1.5 }}
            >
              Each attribute is read from every item and summarised per
              category. A yes/no attribute can also decide which items are
              included.
            </Typography>
            <AttributesEditor
              attributes={draft.attributes}
              onChange={(attributes) => onChange({ attributes })}
              disabled={disabled}
            />
          </Box>
        </Stack>
      </Collapse>
    </Box>
  );
}

NewReportTune.propTypes = {
  open: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
  draft: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  namePlaceholder: PropTypes.string,
  disabled: PropTypes.bool,
  draftAction: PropTypes.node,
  draftNote: PropTypes.node,
};
