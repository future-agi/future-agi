import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Collapse,
  Drawer,
  FormControl,
  FormControlLabel,
  FormLabel,
  IconButton,
  MenuItem,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { Controller, useForm } from "react-hook-form";
import { useAuthContext } from "src/auth/hooks";
import {
  useCreateAnnotationQueue,
  useUpdateAnnotationQueue,
} from "src/api/annotation-queues/annotation-queues";
import LabelPicker from "./components/label-picker";
import AnnotatorPicker from "./components/annotator-picker";
import { QUEUE_ROLES, isQueueAnnotatorRole, queueRoleList } from "./constants";

const STATUS_OPTIONS = [
  { value: "draft", label: "Draft" },
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
  { value: "completed", label: "Completed" },
];

const RESERVATION_TIMEOUT_OPTIONS = [
  { value: 15, label: "15 minutes" },
  { value: 30, label: "30 minutes" },
  { value: 60, label: "1 hour" },
  { value: 240, label: "4 hours" },
];

const DEFAULT_VALUES = {
  name: "",
  description: "",
  instructions: "",
  assignment_strategy: "manual",
  annotations_required: 1,
  reservation_timeout_minutes: 60,
  requires_review: false,
  autoAssign: false,
  label_ids: [],
  annotators: [],
  status: "draft",
};

// ---------------------------------------------------------------------------
// Section card wrapper
// ---------------------------------------------------------------------------
Section.propTypes = {
  title: PropTypes.string.isRequired,
  subtitle: PropTypes.string,
  children: PropTypes.node,
};

function Section({ title, subtitle, children }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          px: 1.5,
          py: 1,
          bgcolor: "background.neutral",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography variant="subtitle1" fontWeight={600}>
          {title}
        </Typography>
        {subtitle && (
          <Typography variant="caption" color="text.secondary">
            {subtitle}
          </Typography>
        )}
      </Box>
      <Box sx={{ p: 1.5 }}>{children}</Box>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Main drawer
// ---------------------------------------------------------------------------
CreateQueueDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  editQueue: PropTypes.object,
  onCreated: PropTypes.func,
};

export default function CreateQueueDrawer({
  open,
  onClose,
  editQueue,
  onCreated,
}) {
  const isEdit = editQueue && editQueue.id && !editQueue._isDuplicate;
  const { user } = useAuthContext();
  const { mutate: createQueue, isPending: isCreating } =
    useCreateAnnotationQueue();
  const { mutate: updateQueue, isPending: isUpdating } =
    useUpdateAnnotationQueue();
  const isPending = isCreating || isUpdating;
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const { control, handleSubmit, reset, setValue, watch, trigger } = useForm({
    defaultValues: DEFAULT_VALUES,
  });

  const labelIds = watch("label_ids");
  const annotators = watch("annotators");
  const annotatorCount = annotators.filter(isQueueAnnotatorRole).length;

  // RHF only re-runs field validation on user interaction with that field, so
  // the "Cannot exceed annotator count" error on `annotations_required` would
  // stay stale when annotators are added/removed or have their roles changed.
  // Re-trigger whenever the annotator count shifts so the error clears (or
  // re-appears) in step with the picker.
  useEffect(() => {
    trigger("annotations_required");
  }, [annotatorCount, trigger]);

  useEffect(() => {
    if (open && editQueue) {
      const qLabels = editQueue.labels?.map((l) => l.label_id || l.id) || [];
      const qAnnotators =
        editQueue.annotators?.map((a) => ({
          userId: a.user_id,
          role: a.role || "annotator",
          roles: queueRoleList(a),
        })) || [];
      reset({
        name: editQueue.name || "",
        description: editQueue.description || "",
        instructions: editQueue.instructions || "",
        assignment_strategy: editQueue.assignment_strategy || "manual",
        annotations_required: editQueue.annotations_required ?? 1,
        reservation_timeout_minutes:
          editQueue.reservation_timeout_minutes ?? 60,
        requires_review: editQueue.requires_review ?? false,
        autoAssign: editQueue.auto_assign ?? false,
        label_ids: qLabels,
        annotators: qAnnotators,
        status: editQueue.status || "draft",
      });
      setAdvancedOpen(false);
    } else if (open) {
      // Pre-select the current user as manager for new queues
      const currentUserId = user?.id || user?.pk;
      reset({
        ...DEFAULT_VALUES,
        annotators: currentUserId
          ? [
              {
                userId: String(currentUserId),
                role: QUEUE_ROLES.MANAGER,
                roles: [
                  QUEUE_ROLES.MANAGER,
                  QUEUE_ROLES.REVIEWER,
                  QUEUE_ROLES.ANNOTATOR,
                ],
              },
            ]
          : [],
      });
      setAdvancedOpen(false);
    }
  }, [open, editQueue, reset, user]);

  const onSubmit = (formData) => {
    const payload = {
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

    if (isEdit) {
      updateQueue(
        { id: editQueue.id, ...payload, status: formData.status },
        { onSuccess: () => onClose() },
      );
    } else {
      createQueue(payload, {
        onSuccess: (data) => {
          const created = data?.data?.result || data?.data;
          onCreated?.(created);
          onClose();
        },
      });
    }
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: { xs: "100%", sm: "45vw" },
          minWidth: 520,
          borderRadius: "0 !important",
        },
      }}
    >
      <Box
        component="form"
        onSubmit={handleSubmit(onSubmit)}
        sx={{ display: "flex", flexDirection: "column", height: "100%" }}
      >
        {/* ── Header ─────────────────────────────────────────── */}
        <Box
          sx={{
            px: 3,
            py: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack
            direction="row"
            alignItems="flex-start"
            justifyContent="space-between"
          >
            <Box>
              <Typography variant="h6">
                {isEdit ? "Edit annotation queue" : "Create annotation queue"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Annotation queues let you organize traces for review and
                labeling.
              </Typography>
            </Box>
            <IconButton onClick={onClose} size="small" sx={{ mt: 0.5 }}>
              <Iconify icon="mingcute:close-line" />
            </IconButton>
          </Stack>
        </Box>

        {/* ── Body ───────────────────────────────────────────── */}
        <Box sx={{ flex: 1, overflow: "auto", px: 3, py: 2.5 }}>
          <Stack spacing={3}>
            {/* ── Section: Queue Details ───────────────────── */}
            <Section title="Queue Details">
              <Stack spacing={3}>
                <Controller
                  name="name"
                  control={control}
                  rules={{ required: "Queue name is required" }}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Queue Name"
                      size="small"
                      fullWidth
                      required
                      placeholder="eg: Hallucination analysis v2"
                      error={!!fieldState.error}
                      helperText={
                        fieldState.error?.message ||
                        "Enter annotation queue name"
                      }
                      inputProps={{ maxLength: 255 }}
                      FormHelperTextProps={{
                        sx: { ml: 0, color: "text.disabled" },
                      }}
                      sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
                    />
                  )}
                />

                <Controller
                  name="description"
                  control={control}
                  render={({ field }) => (
                    <TextField
                      {...field}
                      label="Description"
                      size="small"
                      fullWidth
                      multiline
                      rows={2}
                      placeholder="Brief description of this queue's purpose"
                      sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
                    />
                  )}
                />

                {isEdit && (
                  <Controller
                    name="status"
                    control={control}
                    render={({ field }) => (
                      <TextField
                        {...field}
                        select
                        label="Status"
                        size="small"
                        fullWidth
                        sx={{
                          "& .MuiOutlinedInput-root": { borderRadius: 0.5 },
                        }}
                      >
                        {STATUS_OPTIONS.map((opt) => (
                          <MenuItem key={opt.value} value={opt.value}>
                            {opt.label}
                          </MenuItem>
                        ))}
                      </TextField>
                    )}
                  />
                )}
              </Stack>
            </Section>

            {/* ── Section: Annotation Labels ───────────────── */}
            <Section
              title="Annotation Labels"
              subtitle="Choose labels that annotators will assign to items in this queue."
            >
              <LabelPicker
                selectedIds={labelIds}
                onChange={(ids) => setValue("label_ids", ids)}
              />
            </Section>

            {/* ── Section: Annotators ──────────────────────── */}
            <Section
              title="Annotators"
              subtitle="Annotators label items; Reviewers approve completed items."
            >
              <Stack spacing={2}>
                <AnnotatorPicker
                  value={annotators}
                  onChange={(a) => setValue("annotators", a)}
                  creatorId={
                    isEdit
                      ? editQueue?.created_by
                      : String(user?.id || user?.pk || "")
                  }
                  isManager
                />

                <Controller
                  name="autoAssign"
                  control={control}
                  render={({ field }) => (
                    <FormControlLabel
                      control={
                        <Checkbox
                          size="small"
                          checked={field.value}
                          onChange={(e) => field.onChange(e.target.checked)}
                        />
                      }
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
                            When on, all annotators are assigned to every item
                            and anyone can annotate any item
                          </Typography>
                        </Box>
                      }
                      sx={{ alignItems: "flex-start" }}
                    />
                  )}
                />

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
                      label="Submissions per item"
                      type="number"
                      size="small"
                      fullWidth
                      error={!!fieldState.error}
                      inputProps={{ min: 1, max: 10 }}
                      helperText={
                        fieldState.error?.message ||
                        "Number of responses should be less than or equal to added annotators"
                      }
                      FormHelperTextProps={{
                        sx: { ml: 0, color: "text.disabled" },
                      }}
                      sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
                    />
                  )}
                />
              </Stack>
            </Section>

            {/* ── Section: Guidelines ──────────────────────── */}
            <Section title="Annotation Guidelines">
              <Controller
                name="instructions"
                control={control}
                render={({ field }) => (
                  <TextField
                    {...field}
                    label="Instructions"
                    size="small"
                    fullWidth
                    multiline
                    rows={4}
                    placeholder="If the response is unclear or incomplete, choose the closest matching label"
                    helperText="Supports markdown formatting"
                    FormHelperTextProps={{
                      sx: { ml: 0, color: "text.disabled" },
                    }}
                    sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
                  />
                )}
              />
            </Section>

            {/* ── Advanced Settings (collapsible) ──────────── */}
            <Box>
              <Box
                onClick={() => setAdvancedOpen((v) => !v)}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.5,
                  cursor: "pointer",
                  py: 0.5,
                  "&:hover": { opacity: 0.8 },
                }}
              >
                <Iconify
                  icon={
                    advancedOpen
                      ? "eva:chevron-down-fill"
                      : "eva:chevron-right-fill"
                  }
                  width={20}
                  sx={{ color: "text.secondary" }}
                />
                <Typography
                  variant="subtitle2"
                  color="text.secondary"
                  sx={{ fontSize: 13 }}
                >
                  Advanced settings
                </Typography>
                {!advancedOpen && (
                  <Typography
                    variant="caption"
                    color="text.disabled"
                    sx={{ ml: 0.5 }}
                  >
                    Assignment strategy, reservation timeout, review
                  </Typography>
                )}
              </Box>

              <Collapse in={advancedOpen}>
                <Box
                  sx={{
                    mt: 1,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 0.5,
                    p: 1.5,
                  }}
                >
                  <Stack spacing={3}>
                    {/* Assignment Strategy */}
                    <FormControl>
                      <FormLabel
                        sx={{
                          fontSize: 13,
                          mb: 0.5,
                          color: "text.secondary",
                          fontWeight: "fontWeightMedium",
                        }}
                      >
                        Assignment Strategy
                      </FormLabel>
                      <Controller
                        name="assignment_strategy"
                        control={control}
                        render={({ field }) => (
                          <RadioGroup {...field} row>
                            <FormControlLabel
                              value="manual"
                              control={<Radio size="small" />}
                              label={
                                <Typography variant="body2">Manual</Typography>
                              }
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
                                  <Typography
                                    variant="body2"
                                    color="text.disabled"
                                  >
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
                                  <Typography
                                    variant="body2"
                                    color="text.disabled"
                                  >
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

                    {/* Reservation Timeout */}
                    <Controller
                      name="reservation_timeout_minutes"
                      control={control}
                      render={({ field }) => (
                        <TextField
                          {...field}
                          select
                          label="Reservation timeout"
                          size="small"
                          fullWidth
                          helperText="How long an item stays reserved for an annotator"
                          FormHelperTextProps={{
                            sx: { ml: 0, color: "text.disabled" },
                          }}
                          sx={{
                            "& .MuiOutlinedInput-root": { borderRadius: 0.5 },
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

                    {/* Requires Review */}
                    <Controller
                      name="requires_review"
                      control={control}
                      render={({ field }) => (
                        <FormControlLabel
                          control={
                            <Checkbox
                              size="small"
                              checked={field.value}
                              onChange={(e) => field.onChange(e.target.checked)}
                            />
                          }
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
                      )}
                    />
                  </Stack>
                </Box>
              </Collapse>
            </Box>
          </Stack>
        </Box>

        {/* ── Footer ─────────────────────────────────────────── */}
        <Stack
          direction="row"
          spacing={1.5}
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
            type="submit"
            variant="contained"
            color="primary"
            disabled={isPending}
            sx={{ minWidth: 160 }}
          >
            {isEdit ? "Save changes" : "Create annotation queue"}
          </Button>
        </Stack>
      </Box>
    </Drawer>
  );
}
