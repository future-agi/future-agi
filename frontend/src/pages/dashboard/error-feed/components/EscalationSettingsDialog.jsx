import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { LoadingButton } from "@mui/lab";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  useErrorFeedEscalationSettings,
  useUpdateErrorFeedEscalationSettings,
} from "src/api/errorFeed/error-feed";

const ENTRY = [
  ["min_findings", "Minimum findings in an hour"],
  ["entry_multiplier", "Increase compared with baseline (×)"],
  ["entry_delta", "Minimum additional findings"],
  ["entry_confirmation_minutes", "Escalation confirmation (minutes)"],
];
const RECOVERY = [
  ["recovery_min", "Recovery minimum-count component"],
  ["recovery_multiplier", "Recovery baseline multiplier (×)"],
  ["recovery_delta", "Recovery additional findings"],
  ["recovery_confirmation_minutes", "Recovery confirmation (minutes)"],
];
const numericValues = (values) =>
  Object.fromEntries(
    Object.entries(values).map(([key, value]) => [key, Number(value)]),
  );

function validate(values, ranges) {
  const errors = {};
  for (const [name, range] of Object.entries(ranges)) {
    const value = values[name];
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < range.min ||
      value > range.max
    ) {
      errors[name] = `Enter a number from ${range.min} to ${range.max}.`;
    } else if (
      Math.abs(value / range.step - Math.round(value / range.step)) > 1e-8
    ) {
      errors[name] = `Use increments of ${range.step}.`;
    }
  }
  for (const [recovery, entry, message] of [
    ["recovery_min", "min_findings", "Must be lower than the entry minimum."],
    [
      "recovery_multiplier",
      "entry_multiplier",
      "Must be lower than the entry multiplier.",
    ],
    [
      "recovery_delta",
      "entry_delta",
      "Must be lower than the entry additional findings.",
    ],
  ]) {
    if (values[recovery] >= values[entry]) errors[recovery] = message;
  }
  return errors;
}

export default function EscalationSettingsDialog({
  open,
  onClose,
  organizationId,
  workspaceId,
}) {
  const query = useErrorFeedEscalationSettings({
    organizationId,
    workspaceId,
    enabled: open,
  });
  const mutation = useUpdateErrorFeedEscalationSettings();
  const {
    control,
    reset,
    watch,
    setError,
    clearErrors,
    handleSubmit,
    formState: { isDirty },
  } = useForm({ defaultValues: {} });
  const [loaded, setLoaded] = useState(null);
  const [recoveryOpen, setRecoveryOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const scope = `${organizationId}:${workspaceId}`;
  const loadedScope = loaded?.scope;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const draft = watch();
  const scopedData =
    query.data?.workspace_id === workspaceId ? query.data : null;
  const ready = loaded?.scope === scope && scopedData && open;
  const editable = ready && scopedData.can_edit && !mutation.isPending;

  useEffect(() => {
    if (!open || loadedScope !== scope) {
      setLoaded(null);
      setSaveError(null);
      setDiscardOpen(false);
      reset({});
    }
  }, [open, scope, reset, loadedScope]);

  useEffect(() => {
    if (open && scopedData && !loaded) {
      reset(numericValues(scopedData.values));
      setLoaded({
        scope,
        revision: scopedData.revision,
        values: scopedData.values,
      });
    }
  }, [open, scopedData, loaded, scope, reset]);

  const requestClose = () => {
    if (mutation.isPending) return;
    if (isDirty) setDiscardOpen(true);
    else onClose();
  };

  const submit = handleSubmit(async (values) => {
    if (!editable) return;
    clearErrors();
    const errors = validate(values, scopedData.ranges);
    if (Object.keys(errors).length) {
      for (const [name, message] of Object.entries(errors))
        setError(name, { type: "validate", message });
      if (Object.keys(errors).some((name) => name.startsWith("recovery_")))
        setRecoveryOpen(true);
      return;
    }
    setSaveError(null);
    const capturedScope = scope;
    try {
      await mutation.mutateAsync({
        organizationId,
        workspaceId,
        revision: loaded.revision,
        values,
      });
      if (currentScope.current === capturedScope) {
        enqueueSnackbar("Escalation settings saved", { variant: "success" });
        onClose();
      }
    } catch (error) {
      if (currentScope.current === capturedScope)
        setSaveError(
          (error.statusCode ?? error.response?.status) === 409
            ? "conflict"
            : "failed",
        );
    }
  });

  const reload = async () => {
    const capturedScope = scope;
    const result = await query.refetch();
    if (
      currentScope.current === capturedScope &&
      result.data?.workspace_id === workspaceId
    ) {
      reset(numericValues(result.data.values));
      setLoaded({
        scope,
        revision: result.data.revision,
        values: result.data.values,
      });
      setSaveError(null);
    }
  };

  const fields = (definitions) =>
    definitions.map(([name, label]) => (
      <FormTextFieldV2
        key={name}
        control={control}
        fieldName={name}
        label={label}
        fieldType="number"
        size="small"
        fullWidth
        disabled={!editable}
        onChange={() => clearErrors(name)}
        inputProps={{
          min: scopedData.ranges[name].min,
          max: scopedData.ranges[name].max,
          step: scopedData.ranges[name].step,
        }}
      />
    ));
  const customized =
    ready &&
    Object.keys(loaded.values).some(
      (name) =>
        Number(loaded.values[name]) !== Number(scopedData.defaults[name]),
    );

  return (
    <>
      <Dialog
        open={open}
        onClose={requestClose}
        fullWidth
        aria-labelledby="escalation-settings-title"
        PaperProps={{
          sx: {
            width: 550,
            maxWidth: 590,
            maxHeight: "90vh",
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        <DialogTitle
          id="escalation-settings-heading"
          sx={{ px: 1.5, pt: 1.5, pb: 0.5 }}
        >
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
          >
            <Typography
              id="escalation-settings-title"
              component="span"
              typography="m2"
              fontWeight="fontWeightSemiBold"
            >
              Escalation settings
            </Typography>
            <IconButton
              aria-label="Close escalation settings"
              onClick={requestClose}
              disabled={mutation.isPending}
              sx={{ p: 0 }}
            >
              <Iconify icon="line-md:close" width={20} />
            </IconButton>
          </Stack>
        </DialogTitle>
        <DialogContent sx={{ px: 1.5, py: 1.5 }}>
          {!ready && !query.isError && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
              <CircularProgress
                size={24}
                aria-label="Loading escalation settings"
              />
            </Box>
          )}
          {query.isError && (
            <Alert
              severity="error"
              action={<Button onClick={() => query.refetch()}>Retry</Button>}
            >
              Could not load escalation settings.
            </Alert>
          )}
          {ready && (
            <Stack
              gap={1.5}
              component="form"
              id="escalation-settings-form"
              onSubmit={submit}
              noValidate
            >
              <Box>
                <Typography typography="s1" fontWeight="fontWeightSemiBold">
                  {scopedData.workspace_name}
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  Applies to all projects in this workspace.
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  {customized ? "Customized thresholds" : "Default thresholds"}
                </Typography>
              </Box>
              {!scopedData.can_edit && (
                <Alert severity="info">
                  Only workspace administrators can change these settings.
                </Alert>
              )}
              {saveError && (
                <Alert
                  severity="error"
                  action={
                    saveError === "conflict" ? (
                      <Button onClick={reload}>Reload settings</Button>
                    ) : undefined
                  }
                >
                  {saveError === "conflict"
                    ? "Settings changed. Reload the latest settings before saving."
                    : "Could not save escalation settings. Your changes are retained."}
                </Alert>
              )}
              <Stack
                gap={1.5}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  p: 1.5,
                }}
              >
                <Typography typography="s1" fontWeight="fontWeightSemiBold">
                  When to mark an issue as Escalating
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  Escalating means an issue is getting worse. “Usual” means its
                  average hourly findings over the preceding 24 hours.
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  We count findings in a rolling one-hour window, leaving out
                  the latest 10 minutes so processing can finish. For evaluation
                  issues, each failed evaluation counts as one finding.
                </Typography>
                {fields(ENTRY)}
                <Typography typography="s2" color="text.secondary">
                  All three checks must pass: at least {draft.min_findings}{" "}
                  findings in the latest hour, at least {draft.entry_multiplier}
                  × the usual hourly count, and at least {draft.entry_delta}{" "}
                  more findings than usual.
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  For example, an issue that usually gets 10 findings an hour
                  needs at least{" "}
                  {Math.ceil(
                    Math.max(
                      draft.min_findings,
                      draft.entry_multiplier * 10,
                      10 + Number(draft.entry_delta),
                    ),
                  )}{" "}
                  findings in the latest hour. The hourly count must keep
                  meeting all three checks for{" "}
                  {draft.entry_confirmation_minutes} minutes, checked every five
                  minutes. This does not mean that many new findings are needed
                  every five minutes.
                </Typography>
              </Stack>
              <Typography typography="s2" color="text.secondary">
                Recovery means the issue is no longer getting worse. It moves
                from Escalating to For review when findings stay low; it is not
                resolved.
              </Typography>
              <Accordion
                expanded={recoveryOpen}
                onChange={(_event, expanded) => setRecoveryOpen(expanded)}
                disableGutters
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "6px !important",
                  "&:before": { display: "none" },
                }}
              >
                <AccordionSummary
                  expandIcon={
                    <Iconify icon="eva:chevron-down-fill" width={18} />
                  }
                >
                  <Typography typography="s1" fontWeight="fontWeightSemiBold">
                    Recovery
                  </Typography>
                </AccordionSummary>
                <AccordionDetails>
                  <Stack gap={1.5}>
                    {fields(RECOVERY)}
                    <Typography typography="s2" color="text.secondary">
                      These three values set the hourly count considered low
                      enough to stop escalating. We use the largest of the three
                      limits. For an issue that usually gets 10 findings an
                      hour, return to For review at{" "}
                      {Math.max(
                        draft.recovery_min,
                        draft.recovery_multiplier * 10,
                        10 + Number(draft.recovery_delta),
                      )}{" "}
                      or fewer findings in the latest hour, at every five-minute
                      check for {draft.recovery_confirmation_minutes} minutes.
                    </Typography>
                    <Typography typography="s2" color="text.secondary">
                      We keep the usual hourly count fixed from when escalation
                      began, so the spike does not redefine what is normal.
                    </Typography>
                  </Stack>
                </AccordionDetails>
              </Accordion>
              <Typography typography="s2" color="text.secondary">
                Statuses you choose manually won’t be changed automatically.
              </Typography>
            </Stack>
          )}
        </DialogContent>
        <DialogActions
          sx={{
            px: 1.5,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
            flexWrap: "wrap",
            gap: 1,
          }}
        >
          {ready && scopedData.can_edit && (
            <Button
              size="small"
              disabled={!editable}
              sx={{ mr: "auto" }}
              onClick={() => {
                clearErrors();
                reset(numericValues(scopedData.defaults), {
                  keepDefaultValues: true,
                });
                setSaveError(null);
              }}
            >
              Reset to defaults
            </Button>
          )}
          <Button
            variant="outlined"
            size="small"
            color="inherit"
            onClick={requestClose}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          {ready && scopedData.can_edit && (
            <LoadingButton
              variant="contained"
              size="small"
              type="submit"
              form="escalation-settings-form"
              loading={mutation.isPending}
              disabled={!editable || saveError === "conflict"}
            >
              Save
            </LoadingButton>
          )}
        </DialogActions>
      </Dialog>
      <ConfirmDialog
        open={discardOpen}
        title="Discard changes?"
        content="Your unsaved escalation settings will be discarded."
        onClose={() => setDiscardOpen(false)}
        action={
          <Button
            color="error"
            variant="contained"
            onClick={() => {
              setDiscardOpen(false);
              onClose();
            }}
          >
            Discard changes
          </Button>
        }
      />
    </>
  );
}

EscalationSettingsDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  organizationId: PropTypes.string,
  workspaceId: PropTypes.string,
};
