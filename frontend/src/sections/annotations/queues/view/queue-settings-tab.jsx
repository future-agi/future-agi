import PropTypes from "prop-types";
import React, { useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { enqueueSnackbar } from "notistack";
import {
  Alert,
  Box,
  Button,
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
import Section from "src/components/section";
import { LoadingButton } from "@mui/lab";
import { Controller, useForm, FormProvider } from "react-hook-form";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  useArchiveAnnotationQueue,
  useHardDeleteAnnotationQueue,
  useRestoreAnnotationQueue,
  useUpdateAnnotationQueue,
  useUpdateAnnotationQueueStatus,
} from "src/api/annotation-queues/annotation-queues";
import RHFTextField from "src/components/hook-form/rhf-text-field";
import { RHFCheckbox } from "src/components/hook-form/rhf-checkbox";
import LabelPicker from "../components/label-picker";
import AnnotatorPicker from "../components/annotator-picker";
import { isQueueAnnotatorRole, queueRoleList } from "../constants";

/** @type {Record<string, { label: string; hint: string }>} */
const ALL_STATUS_OPTIONS = {
  draft: { label: "Draft", hint: "Queue is being set up" },
  active: { label: "Active", hint: "Open for annotation and review" },
  paused: { label: "Paused", hint: "Work paused for configuration changes" },
  completed: { label: "Completed", hint: "All items annotated and reviewed" },
};

/** @type {Record<string, string[]>} */
const VALID_TRANSITIONS = {
  draft: ["active"],
  active: ["paused", "completed"],
  paused: ["active", "completed"],
  completed: ["active", "paused"],
};

function getStatusOptions(currentStatus, { hasLabels = true } = {}) {
  const allowed = VALID_TRANSITIONS[currentStatus] || [];
  const opt = (s, { isCurrent = false } = {}) => {
    const disabled = !isCurrent && s === "active" && !hasLabels;
    return {
      value: s,
      label: ALL_STATUS_OPTIONS[s]?.label || s,
      hint: disabled
        ? "Add at least one label to activate the queue"
        : ALL_STATUS_OPTIONS[s]?.hint || "",
      disabled,
    };
  };
  return [
    opt(currentStatus, { isCurrent: true }),
    ...allowed.map((s) => opt(s)),
  ];
}

const RESERVATION_TIMEOUT_OPTIONS = [
  { value: 15, label: "15 minutes" },
  { value: 30, label: "30 minutes" },
  { value: 60, label: "1 hour" },
  { value: 240, label: "4 hours" },
];

export default function QueueSettingsTab({ queue, queueId, creatorId }) {
  const navigate = useNavigate();
  const { mutate: updateQueue, isPending: isUpdating } =
    useUpdateAnnotationQueue();
  const { mutate: updateStatus, isPending: isStatusUpdating } =
    useUpdateAnnotationQueueStatus();
  const { mutate: archiveQueue, isPending: isArchiving } =
    useArchiveAnnotationQueue();
  const { mutate: restoreQueue } = useRestoreAnnotationQueue();
  const isPending = isUpdating || isStatusUpdating;
  const [archiveConfirmOpen, setArchiveConfirmOpen] = useState(false);

  // Archive asks first, like the queue list. It leaves the page only after
  // the archive succeeds, so a failed call keeps the user's edits in place.
  const handleConfirmArchive = () => {
    archiveQueue(queueId, {
      onSuccess: () => {
        // Same toast as the queue list's Archive, with an explicit Undo.
        enqueueSnackbar(
          queue?.is_default
            ? "Default queue archived. Rules paused. Visiting the project page again restores it."
            : "Queue archived. Rules paused.",
          {
            variant: "info",
            action: () => (
              <Button
                color="inherit"
                size="small"
                onClick={() => restoreQueue(queueId)}
              >
                Undo
              </Button>
            ),
          },
        );
        setArchiveConfirmOpen(false);
        navigate("/dashboard/annotations/queues");
      },
    });
  };

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

  const {
    control,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { isDirty },
  } = methods;

  const labelIds = watch("label_ids");
  const annotators = watch("annotators");
  const autoAssign = watch("autoAssign");
  const annotatorCount = annotators.filter(isQueueAnnotatorRole).length;
  const hasInitializedRef = useRef(false);

  useEffect(() => {
    if (queue && !hasInitializedRef.current) {
      hasInitializedRef.current = true;
      const qLabels = queue.labels?.map((l) => l.label_id || l.id) || [];
      const qAnnotators =
        queue.annotators?.map((a) => ({
          userId: a.user_id,
          role: a.role || "annotator",
          roles: queueRoleList(a),
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
        formData.annotators.map((a) => [a.userId, a.roles || [a.role]]),
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
          <Section
            variant="panel"
            sx={{
              boxShadow: "none",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
            }}
          >
            <Typography variant="subtitle1" sx={{ mb: 2 }}>
              General
            </Typography>
            <Stack spacing={2.5}>
              <RHFTextField
                name="name"
                size="small"
                label="Queue Name"
                required
                sx={{ "& .MuiOutlinedInput-root": { borderRadius: "4px" } }}
              />

              <RHFTextField
                name="description"
                label="Description"
                multiline
                rows={2}
                sx={{ "& .MuiOutlinedInput-root": { borderRadius: "4px" } }}
              />

              <RHFTextField
                name="instructions"
                label="Instructions for annotators"
                multiline
                rows={4}
                placeholder="Provide guidelines for annotators. Supports **markdown** formatting."
                helperText="Supports markdown: bold, italic, bullet lists, numbered lists, headings"
                sx={{ "& .MuiOutlinedInput-root": { borderRadius: "4px" } }}
              />

              <Controller
                name="status"
                control={control}
                render={({ field }) => {
                  const currentStatus = queue?.status || field.value;
                  return (
                    <TextField
                      {...field}
                      size="small"
                      select
                      label="Status"
                      fullWidth
                      FormHelperTextProps={{
                        sx: { ml: 0, color: "warning.main" },
                      }}
                      sx={{
                        "& .MuiOutlinedInput-root": { borderRadius: "4px" },
                      }}
                      SelectProps={{
                        renderValue: (v) => ALL_STATUS_OPTIONS[v]?.label || v,
                        MenuProps: {
                          PaperProps: {
                            sx: { borderRadius: "4px !important" },
                          },
                        },
                      }}
                    >
                      {getStatusOptions(currentStatus, {
                        hasLabels: (labelIds || []).length > 0,
                      }).map((opt) => (
                        <MenuItem
                          key={opt.value}
                          value={opt.value}
                          disabled={opt.disabled}
                        >
                          <Box>
                            <Typography variant="body2">{opt.label}</Typography>
                            <Typography variant="caption" color="text.disabled">
                              {opt.hint}
                            </Typography>
                          </Box>
                        </MenuItem>
                      ))}
                    </TextField>
                  );
                }}
              />
            </Stack>
          </Section>

          {/* Labels & Members */}
          <Section
            variant="panel"
            sx={{
              boxShadow: "none",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
            }}
          >
            <Typography variant="subtitle1" sx={{ mb: 2 }}>
              Labels
            </Typography>
            <Stack spacing={2.5}>
              <LabelPicker
                selectedIds={labelIds}
                lockLastSelected
                onChange={(ids) =>
                  setValue("label_ids", ids, { shouldDirty: true })
                }
              />

              <Divider />
              <Typography variant="subtitle1">Members</Typography>
              <AnnotatorPicker
                value={annotators}
                onChange={(a) =>
                  setValue("annotators", a, { shouldDirty: true })
                }
                creatorId={creatorId}
                highlightAutoAssigned={autoAssign}
                isManager
              />
            </Stack>
          </Section>

          {/* Workflow */}
          <Section
            variant="panel"
            sx={{
              boxShadow: "none",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
            }}
          >
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
                    if (annotatorCount > 0 && n > annotatorCount)
                      return `Cannot exceed annotator count (${annotatorCount})`;
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
                      "Number of members with the Annotator role that must complete each item"
                    }
                    FormHelperTextProps={{ sx: { ml: 0 } }}
                    sx={{
                      "& .MuiOutlinedInput-root": { borderRadius: "4px" },
                    }}
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
                    sx={{
                      "& .MuiOutlinedInput-root": { borderRadius: "4px" },
                    }}
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
                        Auto-assign items to all annotator members
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        When on, all members with the Annotator role are
                        assigned to every item and can annotate any item
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
                    fontWeight: "fontWeightSemiBold",
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
                    </RadioGroup>
                  )}
                />
              </FormControl>
            </Stack>
          </Section>

          {/* Save */}
          <Box
            sx={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 2,
              pb: 3,
            }}
          >
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

          <DangerZone
            queue={queue}
            onArchive={() => setArchiveConfirmOpen(true)}
            isArchiving={isArchiving}
          />
        </Stack>
      </Box>

      <ConfirmDialog
        open={archiveConfirmOpen}
        onClose={() => !isArchiving && setArchiveConfirmOpen(false)}
        closeDisabled={isArchiving}
        title="Archive queue"
        content={
          <>
            <Typography>
              Archive <strong>{queue?.name}</strong>?
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Automation rules pause and items are hidden.
              {queue?.is_default
                ? " Visiting the project page again restores this queue automatically."
                : " You can restore it from Queues → Archived."}
            </Typography>
            {isDirty && (
              <Typography variant="body2" color="warning.main" sx={{ mt: 1 }}>
                Unsaved changes will be lost.
              </Typography>
            )}
          </>
        }
        action={
          <LoadingButton
            size="small"
            variant="contained"
            color="warning"
            loading={isArchiving}
            onClick={handleConfirmArchive}
          >
            Archive
          </LoadingButton>
        }
      />
    </FormProvider>
  );
}

function DangerZone({ queue, onArchive, isArchiving }) {
  // Both removal actions live here, away from "Save Changes". Archive is the
  // recoverable soft delete (with a confirm and Undo). Hard delete bypasses
  // the soft-delete that the rest of the app relies on for
  // restore-on-recreate, so it is gated behind a type-the-name confirmation.
  const navigate = useNavigate();
  const headingId = useId();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const { mutate: hardDelete, isPending: isDeleting } =
    useHardDeleteAnnotationQueue();
  if (!queue) return null;
  const matches = typed === queue.name;
  return (
    <>
      <Section
        variant="panel"
        component="section"
        aria-labelledby={headingId}
        sx={{ borderColor: "error.main", borderWidth: 1, borderStyle: "solid" }}
      >
        <Stack spacing={2}>
          <Typography id={headingId} variant="h6" color="error.main">
            Danger zone
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Archive this queue to pause its automation rules and hide its
            items. You can restore it from Queues → Archived.
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Delete it permanently to remove all automation rules, items,
            assignments, and annotation scores attached to it. This cannot be
            undone.
          </Typography>
          <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap">
            <LoadingButton
              type="button"
              variant="outlined"
              color="warning"
              loading={isArchiving}
              startIcon={<Iconify icon="solar:archive-down-bold" />}
              onClick={onArchive}
            >
              Archive queue
            </LoadingButton>
            <Button
              type="button"
              variant="outlined"
              color="error"
              onClick={() => {
                setTyped("");
                setDialogOpen(true);
              }}
            >
              Delete queue permanently
            </Button>
          </Stack>
        </Stack>
      </Section>

      <Dialog
        open={dialogOpen}
        onClose={() => !isDeleting && setDialogOpen(false)}
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 720 } }}
        fullWidth
      >
        <DialogTitle>Delete queue permanently</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Alert severity="error">
              This will hard-delete <strong>{queue.name}</strong> along with all
              rules, items, assignments, and scores. There is no way to recover
              the data after this.
            </Alert>
            <Typography variant="body2">
              Type the queue name to confirm: <strong>{queue.name}</strong>
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

DangerZone.propTypes = {
  queue: PropTypes.object,
  onArchive: PropTypes.func.isRequired,
  isArchiving: PropTypes.bool,
};

QueueSettingsTab.propTypes = {
  queue: PropTypes.object,
  queueId: PropTypes.string.isRequired,
  creatorId: PropTypes.string,
};
