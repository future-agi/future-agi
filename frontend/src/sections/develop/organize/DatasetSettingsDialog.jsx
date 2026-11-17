import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import axios, { endpoints } from "src/utils/axios";
import { notify } from "src/sections/common/ux/toast";
import { useDatasetDetails } from "./useDatasetDetails";
import { isSurfaceShown } from "src/launch/launchSwitch";

const TAG_PATTERN = /^[a-z0-9][a-z0-9._-]{0,29}$/;

export function datasetDetailsError(error) {
  const detail = error?.response?.data?.result ?? error?.result;
  return typeof detail === "string"
    ? detail
    : "Couldn't save dataset details. Try again.";
}

function SettingsForm({ datasetId, details, onClose, canEdit }) {
  // Keep legacy rename; only the DSX description/tag controls follow this hold.
  const extendedDetails = isSurfaceShown("dataset.settings");
  const [name, setName] = useState(details.name);
  const [description, setDescription] = useState(details.description);
  const [tags, setTags] = useState(details.tags);
  const [tagInput, setTagInput] = useState("");
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (body) =>
      axios.put(endpoints.develop.updateDataset(datasetId), body),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["datasets", "details"] }),
        queryClient.invalidateQueries({ queryKey: ["datasets", "list"] }),
        queryClient.invalidateQueries({
          queryKey: ["develop", "dataset-list"],
        }),
        queryClient.invalidateQueries({
          queryKey: ["develop", "dataset-name-list"],
        }),
      ]);
      notify("Dataset details saved", {
        variant: "success",
        key: "dataset-settings",
      });
      onClose();
    },
  });
  // Include an unfinished tag on Save rather than silently dropping the draft.
  const draft = tagInput.trim();
  const submittedTags =
    draft && !tags.includes(draft) ? [...tags, draft] : tags;
  const tagsValid =
    !extendedDetails ||
    (submittedTags.length <= 20 &&
      submittedTags.every((tag) => TAG_PATTERN.test(tag)));
  const nameValid = Boolean(name.trim()) && name.trim().length <= 2000;
  const descriptionValid = !extendedDetails || description.length <= 500;
  const disabled = !canEdit || mutation.isPending;
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (disabled || !nameValid || !tagsValid || !descriptionValid) return;
        mutation.mutate({
          dataset_name: name.trim(),
          ...(extendedDetails ? { description, tags: submittedTags } : {}),
        });
      }}
    >
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {!canEdit && (
            <Alert severity="info">
              Viewers can&apos;t change dataset settings.
            </Alert>
          )}
          {mutation.isError && (
            <Alert severity="error">
              {datasetDetailsError(mutation.error)}
            </Alert>
          )}
          <TextField
            autoFocus
            label="Dataset name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={disabled}
            required
            fullWidth
            error={!nameValid}
            helperText={
              !nameValid ? "Enter a name of up to 2000 characters." : undefined
            }
            inputProps={{ maxLength: 2000 }}
          />
          {extendedDetails && (
            <>
              <TextField
                label="Description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                disabled={disabled}
                multiline
                minRows={3}
                fullWidth
                error={!descriptionValid}
                helperText={`${description.length}/500 characters`}
                inputProps={{ maxLength: 500 }}
              />
              <Autocomplete
                multiple
                freeSolo
                options={[]}
                value={tags}
                inputValue={tagInput}
                disabled={disabled}
                onInputChange={(_, value) => setTagInput(value)}
                onChange={(_, values) => {
                  setTags([
                    ...new Set(values.map((tag) => tag.trim()).filter(Boolean)),
                  ]);
                  setTagInput("");
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Tags"
                    error={!tagsValid}
                    helperText="Up to 20 tags. Use lowercase letters, digits, dots, dashes or underscores (30 characters each)."
                  />
                )}
              />
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button
          type="submit"
          variant="contained"
          disabled={disabled || !nameValid || !tagsValid || !descriptionValid}
        >
          {mutation.isPending ? "Saving..." : "Save changes"}
        </Button>
      </DialogActions>
    </form>
  );
}

export default function DatasetSettingsDialog({ datasetId, onClose }) {
  const query = useDatasetDetails(datasetId);
  const { role } = useAuthContext();
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  // Wait for this opening's read, not a cached list row or stale detail response.
  const ready = query.isFetchedAfterMount && !query.isError;
  return (
    <Dialog
      open
      fullWidth
      maxWidth="sm"
      aria-labelledby="dataset-settings-title"
      disableEscapeKeyDown
      onClose={(_, reason) => {
        if (reason !== "backdropClick") onClose();
      }}
    >
      <DialogTitle id="dataset-settings-title">Dataset settings</DialogTitle>
      {ready && query.data ? (
        <SettingsForm
          key={datasetId}
          datasetId={datasetId}
          details={query.data}
          onClose={onClose}
          canEdit={canEdit}
        />
      ) : (
        <>
          <DialogContent>
            {query.isError ? (
              <Alert severity="error">
                Couldn&apos;t load dataset details. Check your access and try
                again.
              </Alert>
            ) : (
              <Alert severity="info">Loading dataset details...</Alert>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={onClose}>Close</Button>
            {query.isError && (
              <Button onClick={() => query.refetch()}>Retry</Button>
            )}
          </DialogActions>
        </>
      )}
    </Dialog>
  );
}

SettingsForm.propTypes = {
  datasetId: PropTypes.string.isRequired,
  details: PropTypes.shape({
    name: PropTypes.string.isRequired,
    description: PropTypes.string.isRequired,
    tags: PropTypes.arrayOf(PropTypes.string).isRequired,
  }).isRequired,
  onClose: PropTypes.func.isRequired,
  canEdit: PropTypes.bool.isRequired,
};
DatasetSettingsDialog.propTypes = {
  datasetId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
};
