import React, { useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormHelperText,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import { isSurfaceShown } from "src/launch/launchSwitch";
import GenerationSources from "./GenerationSources";

// Use the dropdown's optional propTypes contract, not required destructured keys.
const GenerationModelDropdown =
  /** @type {React.ComponentType<PropTypes.InferProps<typeof CustomModelDropdown.propTypes>>} */ (
    CustomModelDropdown
  );

const path = (id, action) => `/model-hub/develops/${id}/${action}/`;
const supported = new Set([
  "text",
  "integer",
  "float",
  "boolean",
  "json",
  "array",
  "datetime",
]);
// "a", "a and b", "a, b and c".
const listText = (items) =>
  items.length > 1
    ? `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`
    : items[0];

export default function ImmediateGenerateRows({
  request,
  onClose,
  onBusyChange,
}) {
  const { datasetId } = request;
  const sourcesEnabled = isSurfaceShown("synthetic.extras");
  const { refreshGrid } = useDevelopDetailContext();
  const queryClient = useQueryClient();
  const hintId = useId();
  const [form, setForm] = useState({
    n: 10,
    description: "",
    objective: "",
    patterns: "",
    model: "",
    kbId: "",
    referenceRowIds: [],
  });
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const submitted = useRef(false);
  const query = useQuery({
    queryKey: ["immediate-generation", datasetId],
    queryFn: async () => {
      const [table, config] = await Promise.all([
        axios.get(path(datasetId, "get-dataset-table"), {
          params: { current_page_index: 0, page_size: 1 },
        }),
        axios.get(path(datasetId, "synthetic-config"), {
          params: { generation_mode: "append" },
        }),
      ]);
      const savedConfig = config.data.result.data;
      return {
        table: table.data.result,
        config: savedConfig.generation_mode === "append" ? savedConfig : null,
      };
    },
    retry: false,
  });
  useEffect(() => {
    const config = query.data?.config;
    setSaved(
      config && (sourcesEnabled || (!config.reference_row_ids && !config.kb_id))
        ? config
        : null,
    );
    if (config)
      setForm({
        n: config.num_rows,
        model: config.model,
        ...config.dataset,
        kbId: sourcesEnabled ? config.kb_id ?? "" : "",
        referenceRowIds: sourcesEnabled ? config.reference_row_ids ?? [] : [],
      });
  }, [query.data, sourcesEnabled]);
  const referenceIds = request.rowIds?.length
    ? request.rowIds
    : form.referenceRowIds;
  const columns = (query.data?.table?.column_config ?? []).filter(
    (column) =>
      ![
        "experiment",
        "experiment_evaluation",
        "experiment_evaluation_tags",
      ].includes(column.origin_type),
  );
  const schemaSupported =
    columns.length > 0 &&
    columns.every((column) => supported.has(column.data_type));
  const count = Number(form.n);
  // The append writer has no default model and refuses a blank model or
  // description, so the button names whichever of these is still missing.
  const missing = [
    !(Number.isInteger(count) && count >= 1 && count <= 100) &&
      "enter 1 to 100 rows",
    !form.description.trim() && "add a description",
    !form.model && "choose a model",
  ].filter(Boolean);
  const loaded = !query.isPending && !query.error && schemaSupported;
  const hint =
    loaded && missing.length ? `To generate rows, ${listText(missing)}.` : "";
  const ready =
    loaded &&
    missing.length === 0 &&
    (sourcesEnabled || !referenceIds.length) &&
    referenceIds.length <= 50 &&
    (!form.kbId ||
      ["context", "citation"].every((name) =>
        columns.some(
          (column) => column.name === name && column.data_type === "text",
        ),
      ));
  const set = (field) => (event) =>
    setForm((value) => ({ ...value, [field]: event.target.value }));
  const submit = async (regenerate = false) => {
    if (submitted.current || (regenerate ? !saved : !ready)) return;
    submitted.current = true;
    setBusy(true);
    onBusyChange?.(true);
    setError("");
    setSuccess("");
    const config = regenerate
      ? saved
      : {
          generation_mode: "append",
          ...(sourcesEnabled && referenceIds.length
            ? { reference_row_ids: referenceIds }
            : {}),
          ...(sourcesEnabled && form.kbId ? { kb_id: form.kbId } : {}),
          model: form.model,
          num_rows: count,
          columns: columns.map((column) => ({
            name: column.name,
            data_type: column.data_type,
            description:
              saved?.columns?.find((item) => item.name === column.name)
                ?.description ?? column.name,
            property: [],
          })),
          dataset: {
            name: query.data.table.metadata.dataset_name,
            description: form.description,
            objective: form.objective,
            patterns: form.patterns,
          },
        };
    try {
      if (regenerate)
        await axios.put(path(datasetId, "update-synthetic-config"), {
          ...config,
          regenerate: true,
        });
      else await axios.post(path(datasetId, "add_synthetic_data"), config);
      setSaved(config);
      setSuccess(
        `${config.num_rows} generated rows saved. Existing rows are unchanged.`,
      );
      refreshGrid?.();
      await queryClient.invalidateQueries({
        predicate: (entry) =>
          JSON.stringify(entry.queryKey).includes(datasetId),
      });
    } catch (failure) {
      setError(
        getRequestErrorMessage(
          failure,
          "Couldn't generate rows. Please try again.",
        ),
      );
    } finally {
      submitted.current = false;
      setBusy(false);
      onBusyChange?.(false);
    }
  };
  return (
    <>
      <DialogTitle id="immediate-generate-title">Generate rows</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <Typography variant="body2" color="text.secondary">
            Generate into the existing columns. Rows are saved immediately after
            the full batch is validated.
          </Typography>
          {query.isPending && <Typography>Loading configuration…</Typography>}
          {query.error && (
            <Alert severity="error">
              {getRequestErrorMessage(
                query.error,
                "Couldn't load this dataset.",
              )}
              <Button onClick={() => query.refetch()}>Retry</Button>
            </Alert>
          )}
          {!query.isPending && !query.error && !schemaSupported && (
            <Alert severity="warning">
              This dataset has columns that do not support row generation.
            </Alert>
          )}
          {sourcesEnabled && (
            <GenerationSources
              datasetId={datasetId}
              rowIds={referenceIds}
              kbId={form.kbId}
              onChange={(kbId) => setForm((value) => ({ ...value, kbId }))}
              disabled={busy}
            />
          )}
          <TextField
            label="How many"
            type="number"
            value={form.n}
            onChange={set("n")}
            disabled={busy}
            inputProps={{ min: 1, max: 100 }}
            helperText="1 to 100 rows"
          />
          <TextField
            label="Description"
            multiline
            minRows={2}
            value={form.description}
            onChange={set("description")}
            disabled={busy}
            helperText="Required. Describe the rows to generate."
          />
          <TextField
            label="Objective"
            value={form.objective}
            onChange={set("objective")}
            disabled={busy}
          />
          <TextField
            label="Patterns"
            value={form.patterns}
            onChange={set("patterns")}
            disabled={busy}
          />
          <Stack>
            <GenerationModelDropdown
              value={form.model}
              onChange={(event) =>
                setForm((value) => ({
                  ...value,
                  model: event.target.value.model_name,
                }))
              }
              buttonTitle="Model"
              searchDropdown
              disabledClick={busy}
              extraParams={{ model_type: "llm" }}
            />
            <FormHelperText variant="outlined">
              Required. This model writes the new rows.
            </FormHelperText>
          </Stack>
          {saved && (
            <Typography variant="body2">
              Re-generate appends another batch using the saved configuration,
              not unsaved changes above.
            </Typography>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          {success && <Alert severity="success">{success}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ flexWrap: "wrap", rowGap: 1 }}>
        {hint && (
          <Typography
            id={hintId}
            variant="body2"
            color="text.secondary"
            sx={{ flexBasis: "100%", textAlign: "right" }}
          >
            {hint}
          </Typography>
        )}
        <Button onClick={onClose} disabled={busy}>
          Close
        </Button>
        {saved && (
          <Button
            onClick={() => submit(true)}
            disabled={busy || !schemaSupported}
          >
            Re-generate with saved configuration
          </Button>
        )}
        <LoadingButton
          variant="contained"
          loading={busy}
          disabled={!ready}
          aria-describedby={hint ? hintId : undefined}
          onClick={() => submit(false)}
        >
          Generate and save
        </LoadingButton>
      </DialogActions>
    </>
  );
}
ImmediateGenerateRows.propTypes = {
  request: PropTypes.shape({
    datasetId: PropTypes.string.isRequired,
    rowIds: PropTypes.array,
  }).isRequired,
  onClose: PropTypes.func.isRequired,
  onBusyChange: PropTypes.func,
};
