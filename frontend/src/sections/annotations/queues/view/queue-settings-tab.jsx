import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  FormLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Controller, useForm, FormProvider } from "react-hook-form";
import {
  useHardDeleteAnnotationQueue,
  useUpdateAnnotationQueue,
  useUpdateAnnotationQueueStatus,
} from "src/api/annotation-queues/annotation-queues";
import RHFTextField from "src/components/hook-form/rhf-text-field";
import { RHFCheckbox } from "src/components/hook-form/rhf-checkbox";
import LabelPicker from "../components/label-picker";
import AnnotatorPicker from "../components/annotator-picker";

const ALL_STATUS_OPTIONS = {
  draft: { label: "Draft", hint: "Queue is being set up" },
  active: { label: "Active", hint: "Open for annotation and review" },
  paused: { label: "Paused", hint: "Work paused for configuration changes" },
  completed: { label: "Completed", hint: "All items annotated and reviewed" },
};

const VALID_TRANSITIONS = {
  draft: ["active"],
  active: ["paused", "completed"],
  paused: ["active", "completed"],
  completed: ["active", "paused"],
};

function getStatusOptions(currentStatus) {
  const allowed = VALID_TRANSITIONS[currentStatus] || [];
  const opt = (s) => ({
    value: s,
    label: ALL_STATUS_OPTIONS[s]?.label || s,
    hint: ALL_STATUS_OPTIONS[s]?.hint || "",
  });
  return [opt(currentStatus), ...allowed.map(opt)];
}

const RESERVATION_TIMEOUT_OPTIONS = [
  { value: 15, label: "15 minutes" },
  { value: 30, label: "30 minutes" },
  { value: 60, label: "1 hour" },
  { value: 240, label: "4 hours" },
];

export default function QueueSettingsTab({ queue, queueId, creatorId }) {
  const { mutate: updateQueue, isPending: isUpdating } =
    useUpdateAnnotationQueue();
  const { mutate: updateStatus, isPending: isStatusUpdating } =
    useUpdateAnnotationQueueStatus();
  const isPending = isUpdating || isStatusUpdating;

  const methods = useForm({
    defaultValues: {
      name: "",
      description: "",
      instructions: "",
      status: "draft",
      assignment_strategy: "manual",
      annotations_required: 1,
      reservation_timeout_minutes: 60,
      requires_review: false,
      autoAssign: false,
      label_ids: [],
      annotators: [],
    },
  });

  const { control, handleSubmit, reset, setValue, watch } = methods;

  const labelIds = watch("label_ids");
  const annotators = watch("annotators");
  const hasInitializedRef = useRef(false);

  useEffect(() => {
    if (queue && !hasInitializedRef.current) {
      hasInitializedRef.current = true;
      const qLabels = queue.labels?.map((l) => l.label_id || l.id) || [];
      const qAnnotators =
        queue.annotators?.map((a) => ({
          userId: a.user_id,
          role: a.role || "annotator",
        })) || [];
      reset({
        name: queue.name || "",
        description: queue.description || "",
        instructions: queue.instructions || "",
        status: queue.status || "draft",
        assignment_strategy: queue.assignment_strategy || "manual",
        annotations_required: queue.annotations_required ?? 1,
        reservation_timeout_minutes: queue.reservation_timeout_minutes ?? 60,
        requires_review: queue.requires_review ?? false,
        autoAssign: queue.auto_assign ?? false,
        label_ids: qLabels,
        annotators: qAnnotators,
      });
    }
  }, [queue, reset]);

  const onSubmit = (formData) => {
    const queuePayload = {
      id: queueId,
      name: formData.name,
      description: formData.description || "",
      instructions: formData.instructions || "",
      assignment_strategy: formData.assignment_strategy,
      annotations_required: formData.annotations_required,
      reservation_timeout_minutes: formData.reservation_timeout_minutes,
      requires_review: formData.requires_review,
      auto_assign: formData.autoAssign,
      label_ids: formData.label_ids,
      annotator_ids: formData.annotators.map((a) => a.userId),
      annotator_roles: Object.fromEntries(
        formData.annotators.map((a) => [a.userId, a.role]),
      ),
    };

    const currentStatus = queue?.status || "draft";
    const statusChanged = formData.status !== currentStatus;

    // Save queue settings first, then update status if changed
    updateQueue(queuePayload, {
      onSuccess: () => {
        hasInitializedRef.current = false;
        if (statusChanged) {
          updateStatus(
            { id: queueId, status: formData.status },
            {
              onSuccess: () => {
                hasInitializedRef.current = false;
              },
            },
          );
        }
      },
    });
  };

  return (
    <FormProvider {...methods}>
      <Box component="form" onSubmit={handleSubmit(onSubmit)}>
        <Stack spacing={3}>
          {/* General */}
          <Card
            elevation={0}
            sx={{
              boxShadow: "none",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
            }}
          >
            <CardContent>
              <Typography variant="subtitle1" sx={{ mb: 2 }}>
                General
              </Typography>
              <Stack spacing={2.5}>
                <RHFTextField
                  name="name"
                  size="small"
                  label="Queue Name"
                  required
                  sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
                />

                <RHFTextField
                  name="description"
                  label="Description"
                  multiline
                  rows={2}
                  sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
                />

                <RHFTextField
                  name="instructions"
                  label="Instructions for annotators"
                  multiline
                  rows={4}
                  placeholder="Provide guidelines for annotators. Supports **markdown** formatting."
                  helperText="Supports markdown: bold, italic, bullet lists, numbered lists, headings"
                  sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
                />

                <Controller
                  name="status"
                  control={control}
                  render={({ field }) => (
                    <TextField
                      {...field}
                      size="small"
                      select
                      label="Status"
                      fullWidth
                      sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
                      SelectProps={{
                        renderValue: (v) => ALL_STATUS_OPTIONS[v]?.label || v,
                        MenuProps: {
                          PaperProps: {
                            sx: { borderRadius: "4px !important" },
                          },
                        },
                      }}
                    >
                      {getStatusOptions(queue?.status || field.value).map(
                        (opt) => (
                          <MenuItem key={opt.value} value={opt.value}>
                            <Box>
                              <Typography variant="body2">
                                {opt.label}
                              </Typography>
                              <Typography
                                variant="caption"
                                color="text.disabled"
                              >
                                {opt.hint}
                              </Typography>
                            </Box>
                          </MenuItem>
                        ),
                      )}
                    </TextField>
                  )}
                />
              </Stack>
            </CardContent>
          </Card>

          {/* Labels & Annotators */}
          <Card
            elevation={0}
            sx={{
              boxShadow: "none",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
            }}
          >
            <CardContent>
              <Typography variant="subtitle1" sx={{ mb: 2 }}>
                Labels
              </Typography>
              <Stack spacing={2.5}>
                <LabelPicker
                  selectedIds={labelIds}
                  onChange={(ids) =>
                    setValue("label_ids", ids, { shouldDirty: true })
                  }
                />

                <Divider />
                <Typography variant="subtitle1">Annotators</Typography>
                <AnnotatorPicker
                  value={annotators}
                  onChange={(a) =>
                    setValue("annotators", a, { shouldDirty: true })
                  }
                  creatorId={creatorId}
                  isManager
                />
              </Stack>
            </CardContent>
          </Card>

          {/* Workflow */}
          <Card
            elevation={0}
            sx={{
              boxShadow: "none",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
            }}
          >
            <CardContent>
              <Typography variant="subtitle1" sx={{ mb: 2 }}>
                Workflow
              </Typography>
              <Stack spacing={2.5}>
                <Controller
                  name="annotations_required"
                  control={control}
                  rules={{
                    validate: (value) => {
                      const n = Number(value);
                      if (!value && value !== 0) return "Required";
                      if (n < 1) return "Must be at least 1";
                      if (annotators.length > 0 && n > annotators.length)
                        return `Cannot exceed annotator count (${annotators.length})`;
                      return true;
                    },
                  }}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      onChange={(e) => {
                        const raw = e.target.value;
                        field.onChange(raw === "" ? "" : parseInt(raw, 10));
                      }}
                      label="Annotations required per item"
                      type="number"
                      size="small"
                      fullWidth
                      error={!!fieldState.error}
                      inputProps={{ min: 1, max: 10 }}
                      helperText={
                        fieldState.error?.message ||
                        "Number of annotators that must complete each item"
                      }
                      FormHelperTextProps={{ sx: { ml: 0 } }}
                      sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
                    />
                  )}
                />

                <Controller
                  name="reservation_timeout_minutes"
                  control={control}
                  render={({ field }) => (
                    <TextField
                      {...field}
                      select
                      size="small"
                      label="Reservation timeout"
                      fullWidth
                      helperText="How long an item is reserved for an annotator"
                      FormHelperTextProps={{ sx: { ml: 0 } }}
                      sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
                    >
                      {RESERVATION_TIMEOUT_OPTIONS.map((opt) => (
                        <MenuItem key={opt.value} value={opt.value}>
                          {opt.label}
                        </MenuItem>
                      ))}
                    </TextField>
                  )}
                />

                <Stack spacing={0.5}>
                  <RHFCheckbox
                    name="requires_review"
                    label={
                      <Typography
                        variant="body2"
                        fontWeight={500}
                        color="text.primary"
                      >
                        Require reviewer approval
                      </Typography>
                    }
                  />

                  <RHFCheckbox
                    name="autoAssign"
                    label={
                      <Box>
                        <Typography
                          variant="body2"
                          fontWeight={500}
                          color="text.primary"
                        >
                          Auto-assign items to all annotators
                        </Typography>
                        <Typography variant="caption" color="text.disabled">
                          When on, all annotators are assigned to every item and
                          anyone can annotate any item
                        </Typography>
                      </Box>
                    }
                    sx={{ alignItems: "flex-start" }}
                  />
                </Stack>

                <FormControl>
                  <FormLabel
                    sx={{
                      typography: "s1",
                      color: "text.primary",
                      fontWeight: "fontWeightBold",
                    }}
                  >
                    Assignment Strategy
                  </FormLabel>
                  <Controller
                    name="assignment_strategy"
                    control={control}
                    render={({ field }) => (
                      <RadioGroup {...field}>
                        <FormControlLabel
                          value="manual"
                          control={<Radio size="small" />}
                          label="Manual"
                        />
                        <FormControlLabel
                          value="round_robin"
                          disabled
                          control={<Radio size="small" />}
                          label={
                            <Stack
                              direction="row"
                              alignItems="center"
                              spacing={1}
                            >
                              <Typography variant="body2" color="text.disabled">
                                Round Robin
                              </Typography>
                              <Chip
                                label="Coming soon"
                                size="small"
                                variant="outlined"
                                color="primary"
                              />
                            </Stack>
                          }
                        />
                        <FormControlLabel
                          value="load_balanced"
                          disabled
                          control={<Radio size="small" />}
                          label={
                            <Stack
                              direction="row"
                              alignItems="center"
                              spacing={1}
                            >
                              <Typography variant="body2" color="text.disabled">
                                Load Balanced
                              </Typography>
                              <Chip
                                label="Coming soon"
                                size="small"
                                variant="outlined"
                                color="primary"
                              />
                            </Stack>
                          }
                        />
                      </RadioGroup>
                    )}
                  />
                </FormControl>
              </Stack>
            </CardContent>
          </Card>

          {/* Save */}
          <Box sx={{ display: "flex", justifyContent: "flex-end", pb: 3 }}>
            <Button
              type="submit"
              variant="contained"
              color="primary"
              disabled={isPending}
              sx={{ minWidth: 200 }}
            >
              {isPending ? "Saving..." : "Save Changes"}
            </Button>
          </Box>

          <DangerZone queue={queue} />
        </Stack>
      </Box>
    </FormProvider>
  );
}

function DangerZone({ queue }) {
  // Hard delete is intentionally separated from the everyday "Archive"
  // button on the queue list. It bypasses the soft-delete that the rest
  // of the app relies on for restore-on-recreate, so we gate it behind
  // an explicit type-the-name confirmation.
  const navigate = useNavigate();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const { mutate: hardDelete, isPending: isDeleting } =
    useHardDeleteAnnotationQueue();
  if (!queue) return null;
  const matches = typed === queue.name;
  return (
    <>
      <Card sx={{ borderColor: "error.main", borderWidth: 1, borderStyle: "solid" }}>
        <CardContent>
          <Stack spacing={2}>
            <Typography variant="h6" color="error.main">
              Danger zone
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Permanently delete this queue. All automation rules, items,
              assignments, and annotation scores attached to it are removed.
              This cannot be undone — for a recoverable removal, use{" "}
              <strong>Archive</strong> from the queue list instead.
            </Typography>
            <Box>
              <Button
                variant="outlined"
                color="error"
                onClick={() => {
                  setTyped("");
                  setDialogOpen(true);
                }}
              >
                Delete queue permanently
              </Button>
            </Box>
          </Stack>
        </CardContent>
      </Card>

      <Dialog
        open={dialogOpen}
        onClose={() => !isDeleting && setDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Delete queue permanently</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Alert severity="error">
              This will hard-delete <strong>{queue.name}</strong> along with
              all rules, items, assignments, and scores. There is no way to
              recover the data after this.
            </Alert>
            <Typography variant="body2">
              Type the queue name to confirm:{" "}
              <strong>{queue.name}</strong>
            </Typography>
            <TextField
              autoFocus
              size="small"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={queue.name}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)} disabled={isDeleting}>
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            disabled={!matches || isDeleting}
            onClick={() =>
              hardDelete(
                { id: queue.id, name: queue.name },
                {
                  onSuccess: () => {
                    setDialogOpen(false);
                    navigate("/dashboard/annotations/queues");
                  },
                },
              )
            }
          >
            {isDeleting ? "Deleting…" : "Delete forever"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

DangerZone.propTypes = { queue: PropTypes.object };

QueueSettingsTab.propTypes = {
  queue: PropTypes.object,
  queueId: PropTypes.string.isRequired,
  creatorId: PropTypes.string,
};
