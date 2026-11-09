import PropTypes from "prop-types";
import { useId, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useExactTraceAttributeProperties } from "src/sections/projects/LLMTracing/useExactTraceAttributeProperties";
import { useDashboardFilterValues } from "src/hooks/useDashboards";
import { useDebounce } from "src/hooks/use-debounce";
import { FILTER_VALUE_SEARCH_DEBOUNCE_MS } from "src/config/runtime_limits";
import {
  getAttributeLookupMessage,
  getFilterValueReadMessage,
} from "src/utils/queryReadState";
import { samplingFieldLabel } from "./sampling_selection_summary";
import { samplingMatcherValue } from "./simpleSamplingPolicy";

const sourceLabels = {
  span: "Span attribute",
  metadata: "Metadata",
  resource: "Resource attribute",
};
const valueOf = (option) =>
  option && typeof option === "object" && Object.hasOwn(option, "value")
    ? option.value
    : option;
const valueIdentity = (value) => `${typeof value}:${JSON.stringify(value)}`;
const displayValue = (value) =>
  value && typeof value === "object" ? JSON.stringify(value) : String(value);
const scalarTypes = new Set(["string", "number", "boolean"]);
const isScalarField = (field) =>
  (field.attributeTypes?.length ? field.attributeTypes : [field.type]).every(
    (type) => scalarTypes.has(type),
  );
// Catalog `type` describes the attribute, not just the returned member. An
// array can suggest a scalar member that sampling's JSON_VALUE cannot match.
const scalarValueOption = (option) => {
  const value = valueOf(option);
  const type = option?.type ?? typeof value;
  if (!scalarTypes.has(type)) return [];
  let raw = value;
  if (type === "number" && typeof value === "string" && value.trim())
    raw = Number(value);
  if (type === "boolean" && (value === "true" || value === "false"))
    raw = value === "true";
  return typeof raw === type && (type !== "number" || Number.isFinite(raw))
    ? [{ raw, type }]
    : [];
};

function FieldBrowser({ project, onSelect, onClose, label }) {
  const [search, setSearch] = useState("");
  const typeHelpId = useId();
  const inventory = useExactTraceAttributeProperties({
    projectId: project,
    source: "spans",
    search,
    enabled: Boolean(project),
  });
  const message = getAttributeLookupMessage(inventory.queryReadState);
  return (
    <Dialog
      open
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="sampling-field-title"
    >
      <DialogTitle id="sampling-field-title">
        Choose {label.toLowerCase()}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ pt: 1 }}>
          <TextField
            autoFocus
            fullWidth
            label="Find an attribute"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Typography variant="caption" color="text.secondary">
            Existing span attributes in this project. For metadata or resource
            fields, use Enter field manually.
          </Typography>
          <Typography id={typeHelpId} variant="caption" color="text.secondary">
            Sampling matches only scalar string, number and boolean fields.
            Arrays and objects are unavailable.
          </Typography>
          {inventory.isLoading && (
            <Typography role="status" variant="body2">
              Loading fields…
            </Typography>
          )}
          {message && (
            <Typography role="status" variant="caption" color="text.secondary">
              {message}
            </Typography>
          )}
          <Box
            role="group"
            aria-label="Existing fields"
            sx={{ maxHeight: 320, overflow: "auto" }}
          >
            {(inventory.data || []).map((field) => (
              <Button
                key={field.id}
                fullWidth
                disabled={!isScalarField(field)}
                aria-describedby={
                  !isScalarField(field) ? typeHelpId : undefined
                }
                onClick={() => onSelect({ source: "span", key: field.id })}
                sx={{
                  justifyContent: "flex-start",
                  textAlign: "left",
                  whiteSpace: "normal",
                  overflowWrap: "anywhere",
                }}
              >
                {field.name || field.id}
              </Button>
            ))}
          </Box>
          {!inventory.isLoading &&
            !inventory.isError &&
            !inventory.data?.length && (
              <Typography variant="body2">
                No fields found in the loaded inventory. Search for an exact
                key, load more, or enter a field manually.
              </Typography>
            )}
          {(inventory.isError || inventory.exactSearchError) && (
            <Button
              disabled={inventory.isFetching}
              onClick={() => inventory.refetch()?.catch?.(() => undefined)}
            >
              Retry fields
            </Button>
          )}
          {inventory.hasNextPage && (
            <Button
              disabled={inventory.isFetching}
              onClick={() =>
                inventory.fetchNextPage()?.catch?.(() => undefined)
              }
            >
              {inventory.isFetchNextPageError
                ? "Retry more fields"
                : "Load more fields"}
            </Button>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
      </DialogActions>
    </Dialog>
  );
}
FieldBrowser.propTypes = {
  project: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
  label: PropTypes.string.isRequired,
};

export function SamplingFieldPicker({
  project,
  value,
  onChange,
  label = "Group field",
  sources = ["span", "metadata", "resource"],
  helperText,
}) {
  const [open, setOpen] = useState(false);
  const field = value || { source: "span", key: "" };
  return (
    <Stack spacing={1} sx={{ minWidth: 0, flex: 1 }}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
      >
        <Typography variant="body2" sx={{ flex: 1, overflowWrap: "anywhere" }}>
          {label}: {field.key ? samplingFieldLabel(field) : "Not selected"}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          disabled={!project}
          onClick={() => setOpen(true)}
          aria-label={`Choose ${label.toLowerCase()}`}
        >
          Choose field
        </Button>
      </Stack>
      {helperText && (
        <Typography variant="caption" color="text.secondary">
          {helperText}
        </Typography>
      )}
      <Box
        component="details"
        sx={{
          "& > summary": {
            cursor: "pointer",
            fontSize: 12,
            color: "text.secondary",
          },
        }}
      >
        <summary>Enter field manually</summary>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          sx={{ mt: 1 }}
        >
          {sources.length > 1 && (
            <TextField
              select
              label={`${label} location`}
              value={field.source}
              onChange={(e) =>
                onChange({ source: e.target.value, key: field.key })
              }
              sx={{ minWidth: 150 }}
            >
              {sources.map((source) => (
                <MenuItem key={source} value={source}>
                  {sourceLabels[source]}
                </MenuItem>
              ))}
            </TextField>
          )}
          <TextField
            label={`${label} key`}
            value={field.key}
            fullWidth
            onChange={(e) =>
              onChange({ source: field.source, key: e.target.value })
            }
          />
        </Stack>
      </Box>
      {open && (
        <FieldBrowser
          project={project}
          label={label}
          onClose={() => setOpen(false)}
          onSelect={(next) => {
            onChange(next);
            setOpen(false);
          }}
        />
      )}
    </Stack>
  );
}
SamplingFieldPicker.propTypes = {
  project: PropTypes.string,
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  label: PropTypes.string,
  sources: PropTypes.array,
  helperText: PropTypes.string,
};

function ValuesBrowser({
  project,
  field,
  values,
  onApply,
  onClose,
  distinctGroups = false,
}) {
  // Wrap selected JSON values so a saved object containing its own `value`
  // key is never confused with a discovery-endpoint option envelope.
  const wrap = (raw) => ({ raw, type: typeof raw });
  const [selected, setSelected] = useState(() => values.map(wrap));
  const duplicateGroups =
    distinctGroups &&
    new Set(selected.map((option) => samplingMatcherValue(option.raw))).size !==
      selected.length;
  const [search, setSearch] = useState("");
  const settledSearch = useDebounce(search, FILTER_VALUE_SEARCH_DEBOUNCE_MS);
  // Only span attributes use this existing catalog adapter. Metadata/resource
  // identities are never guessed or flattened into a different backend source.
  const discoverable = field.source === "span" && Boolean(field.key && project);
  const inventory = useDashboardFilterValues({
    metricName: field.key,
    metricType: "custom_attribute",
    projectIds: project ? [project] : [],
    source: "traces",
    search: settledSearch,
    searchGesture: search,
    enabled: discoverable,
  });
  const message = discoverable
    ? getFilterValueReadMessage(inventory.queryReadState)
    : "Value suggestions are not available for this field location. Enter each exact value and press Enter.";
  return (
    <Dialog
      open
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="sampling-values-title"
    >
      <DialogTitle id="sampling-values-title">Choose group values</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ pt: 1 }}>
          <Typography variant="body2">{samplingFieldLabel(field)}</Typography>
          <Autocomplete
            multiple
            freeSolo
            disableCloseOnSelect
            filterSelectedOptions
            options={
              discoverable
                ? (inventory.data || []).flatMap(scalarValueOption)
                : []
            }
            value={selected}
            inputValue={search}
            onInputChange={(_, next) => setSearch(next)}
            onChange={(_, next) =>
              setSelected(
                next.map((option) =>
                  typeof option === "string" ? wrap(option) : option,
                ),
              )
            }
            getOptionLabel={(option) =>
              typeof option === "string" ? option : displayValue(option.raw)
            }
            filterOptions={(options) => options}
            isOptionEqualToValue={(a, b) =>
              valueIdentity(a.raw) === valueIdentity(b.raw)
            }
            loading={discoverable && inventory.isFetching}
            renderTags={(items, getTagProps) =>
              items.map((option, index) => (
                <Chip
                  {...getTagProps({ index })}
                  key={`${valueIdentity(option.raw)}:${index}`}
                  label={`${displayValue(option.raw)}${distinctGroups ? ` (${option.type === "string" ? "text" : option.type})` : ""}`}
                />
              ))
            }
            renderOption={(props, option) => (
              <li {...props} key={valueIdentity(option.raw)}>
                {displayValue(option.raw)}{" "}
                <Typography
                  component="span"
                  variant="caption"
                  color="text.secondary"
                  sx={{ ml: 1 }}
                >
                  {option.type}
                </Typography>
              </li>
            )}
            renderInput={(params) => (
              <TextField
                {...params}
                autoFocus
                label="Find or enter values"
                error={duplicateGroups}
                helperText={
                  duplicateGroups
                    ? "Some values match the same group. Keep one value per group before setting counts or shares."
                    : "Pick existing values, or type one exact value and press Enter. Commas remain part of a value."
                }
              />
            )}
          />
          {message && (
            <Typography role="status" variant="caption" color="text.secondary">
              {message}
            </Typography>
          )}
          {discoverable && inventory.isError && (
            <Alert severity="warning">
              Could not load values. Retry or enter exact values manually.
            </Alert>
          )}
          {discoverable && inventory.isError && (
            <Button
              disabled={inventory.isFetching}
              onClick={() => inventory.refetch()?.catch?.(() => undefined)}
            >
              Retry values
            </Button>
          )}
          {discoverable && inventory.hasNextPage && (
            <Button
              disabled={inventory.isFetching}
              onClick={() =>
                inventory.fetchNextPage()?.catch?.(() => undefined)
              }
            >
              {inventory.isFetchNextPageError
                ? "Retry more values"
                : "Load more values"}
            </Button>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={duplicateGroups}
          onClick={() => onApply(selected.map((option) => option.raw))}
        >
          Use values
        </Button>
      </DialogActions>
    </Dialog>
  );
}
ValuesBrowser.propTypes = {
  project: PropTypes.string,
  field: PropTypes.object.isRequired,
  values: PropTypes.array.isRequired,
  onApply: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
  distinctGroups: PropTypes.bool,
};

export function SamplingValuePicker({
  project,
  predicate,
  onChange,
  distinctGroups = false,
}) {
  const [open, setOpen] = useState(false);
  return (
    <Stack spacing={1}>
      <Stack
        direction="row"
        spacing={1}
        flexWrap="wrap"
        useFlexGap
        alignItems="center"
        role="group"
        aria-label="Group values"
      >
        {predicate.values.map((value, i) => (
          <Chip
            key={`${valueIdentity(value)}:${i}`}
            label={displayValue(value)}
            onDelete={() =>
              onChange(predicate.values.filter((_, index) => index !== i))
            }
          />
        ))}
        <Button
          size="small"
          variant="outlined"
          disabled={!predicate.key || !project}
          onClick={() => setOpen(true)}
        >
          Choose values
        </Button>
      </Stack>
      {open && (
        <ValuesBrowser
          project={project}
          field={predicate}
          distinctGroups={distinctGroups}
          values={predicate.values}
          onClose={() => setOpen(false)}
          onApply={(values) => {
            onChange(values);
            setOpen(false);
          }}
        />
      )}
    </Stack>
  );
}
SamplingValuePicker.propTypes = {
  project: PropTypes.string,
  predicate: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  distinctGroups: PropTypes.bool,
};
