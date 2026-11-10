import React, { useEffect, useId, useRef, useState } from "react";
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
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  InputAdornment,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  useErrorFeedEscalationSettings,
  useUpdateErrorFeedEscalationSettings,
} from "src/api/errorFeed/error-feed";
import EscalationSummary from "./EscalationSummary";
import EscalationPreviewChart from "./EscalationPreviewChart";
import {
  ENTRY,
  accentSx,
  FIELD_NAMES,
  PRESETS,
  RECOVERY,
  deciding,
  escalateAt,
  matchPreset,
  numericValues,
  presetValues,
  recoverAt,
  toPayload,
  validate,
} from "./escalationSettings";

const disclosureSx = {
  bgcolor: "transparent",
  backgroundImage: "none",
  boxShadow: "none",
  // The theme paints expanded accordions with background.paper, which shows
  // as a band on the dialog surface in dark mode.
  "&.Mui-expanded": { bgcolor: "transparent" },
  "&:before": { display: "none" },
  "& .MuiAccordionSummary-root": {
    minHeight: 36,
    px: 0,
    flexDirection: "row-reverse",
    gap: 0.75,
  },
  "& .MuiAccordionSummary-content": { my: 0.75, alignItems: "center" },
  "& .MuiAccordionSummary-expandIconWrapper.Mui-expanded": {
    transform: "rotate(90deg)",
  },
  "& .MuiAccordionDetails-root": {
    border: "1px solid",
    borderColor: "divider",
    borderRadius: 1,
    p: 2,
  },
};

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
    setFocus,
    handleSubmit,
    formState: { isDirty },
  } = useForm({ defaultValues: {} });
  const [loaded, setLoaded] = useState(null);
  const [tuneOpen, setTuneOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [reloaded, setReloaded] = useState(false);
  const [usual, setUsual] = useState(10);
  const lastValid = useRef(null);
  const idPrefix = useId();
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
      setReloaded(false);
      setDiscardOpen(false);
      lastValid.current = null;
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

  const values = ready ? numericValues(draft) : null;
  const defaults = ready ? numericValues(scopedData.defaults) : null;
  const errors = ready ? validate(values, scopedData.ranges) : {};
  const errorNames = FIELD_NAMES.filter((name) => errors[name]);
  const valid = ready && errorNames.length === 0;
  if (valid) lastValid.current = values;
  // While a value is invalid the previews keep showing the last valid setup.
  const shown = valid ? values : lastValid.current ?? defaults;
  const active = valid
    ? matchPreset(values, scopedData.defaults) ?? "custom"
    : "custom";
  const atDefaults =
    ready && FIELD_NAMES.every((name) => values[name] === defaults[name]);

  const edited = () => {
    setSaveError((current) => (current === "conflict" ? current : null));
    setReloaded(false);
  };

  const requestClose = () => {
    if (mutation.isPending) return;
    if (isDirty) setDiscardOpen(true);
    else onClose();
  };

  const applyValues = (next) => {
    reset(next, { keepDefaultValues: true });
    edited();
  };

  const choosePreset = (_event, key) => {
    if (!editable) return;
    if (key === null || key === "custom") {
      if (active === "custom" || key === "custom") setTuneOpen(true);
      return;
    }
    const preset = PRESETS.find((item) => item.key === key);
    applyValues(presetValues(preset, scopedData.defaults));
  };

  const submit = handleSubmit(async () => {
    if (!editable) return;
    if (errorNames.length) {
      setTuneOpen(true);
      requestAnimationFrame(() => setFocus(errorNames[0]));
      return;
    }
    setSaveError(null);
    const capturedScope = scope;
    try {
      await mutation.mutateAsync({
        organizationId,
        workspaceId,
        revision: loaded.revision,
        values: toPayload(values),
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
      setReloaded(true);
    }
  };

  const fieldGroup = (title, note, definitions, decides, outcome, dot) => {
    const headingId = `${idPrefix}-${title}`;
    return (
      <Stack gap={1.5} role="group" aria-labelledby={headingId}>
        <Box>
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ pb: 1, borderBottom: "1px solid", borderColor: "divider" }}
          >
            <Box
              sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: dot }}
            />
            <Typography
              id={headingId}
              typography="s1"
              fontWeight="fontWeightSemiBold"
            >
              {title}
            </Typography>
          </Stack>
          <Typography typography="s2" color="text.secondary" sx={{ mt: 0.75 }}>
            {note}
          </Typography>
        </Box>
        {definitions.map(([name, label, unit]) => (
          <FormTextFieldV2
            key={name}
            control={control}
            fieldName={name}
            label={label}
            fieldType="number"
            size="small"
            fullWidth
            disabled={!editable}
            onChange={edited}
            error={!!errors[name]}
            helperText={errors[name]}
            inputProps={{
              min: scopedData.ranges[name].min,
              max: scopedData.ranges[name].max,
              step: scopedData.ranges[name].step,
              style: { textAlign: "left" },
              "aria-label": `${title} ${label.toLowerCase()}`,
            }}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  {name === decides && (
                    <CustomTooltip
                      show
                      size="small"
                      arrow
                      title="This limit is the highest for the example above, so it decides when the status changes."
                    >
                      <Chip
                        size="small"
                        label="decides"
                        sx={{
                          mr: 1,
                          color: "text.secondary",
                          bgcolor: "action.hover",
                        }}
                      />
                    </CustomTooltip>
                  )}
                  {unit}
                </InputAdornment>
              ),
            }}
          />
        ))}
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ pt: 1, borderTop: "1px solid", borderColor: "divider" }}
        >
          For an issue that usually gets {usual.toLocaleString("en-US")}:
          <Box
            component="b"
            sx={{ display: "block", color: "text.primary", fontWeight: 600 }}
          >
            {outcome}
          </Box>
        </Typography>
      </Stack>
    );
  };

  const [entryDecides, recoveryDecides] = shown
    ? deciding(shown, usual)
    : [null, null];

  return (
    <>
      <Dialog
        open={open}
        onClose={requestClose}
        fullWidth
        aria-labelledby="escalation-settings-title"
        PaperProps={{
          sx: {
            width: 600,
            maxWidth: 600,
            maxHeight: "90vh",
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        <DialogTitle
          id="escalation-settings-heading"
          sx={{ px: 3, pt: 2.5, pb: 1.5 }}
        >
          <Stack direction="row" alignItems="flex-start" gap={1.5}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Stack direction="row" alignItems="center" gap={0.75}>
                <Typography
                  id="escalation-settings-title"
                  component="span"
                  typography="m2"
                  fontWeight="fontWeightSemiBold"
                >
                  Escalation settings
                </Typography>
                {ready && (
                  <CustomTooltip
                    show
                    size="small"
                    arrow
                    placement="bottom-start"
                    title={`Findings are counted over the last ${
                      scopedData.window_minutes ?? 60
                    } minutes, leaving out the latest ${
                      scopedData.processing_allowance_minutes ?? 10
                    } so processing can finish, and checked every ${
                      scopedData.cadence_minutes ?? 5
                    } minutes. For evaluation issues, each failed evaluation counts as one finding. Statuses you set by hand are never changed automatically.`}
                  >
                    <IconButton
                      size="small"
                      aria-label="How escalation works"
                      sx={{ p: 0.25, color: "text.secondary" }}
                    >
                      <Iconify icon="eva:info-outline" width={16} />
                    </IconButton>
                  </CustomTooltip>
                )}
              </Stack>
              {ready && (
                <Typography typography="s2" color="text.secondary">
                  Applies to all projects in{" "}
                  <Box
                    component="b"
                    sx={{ color: "text.primary", fontWeight: 500 }}
                  >
                    {scopedData.workspace_name}
                  </Box>
                </Typography>
              )}
            </Box>
            <IconButton
              aria-label="Close escalation settings"
              onClick={requestClose}
              disabled={mutation.isPending}
              sx={{ p: 0.5 }}
            >
              <Iconify icon="line-md:close" width={20} />
            </IconButton>
          </Stack>
        </DialogTitle>
        <DialogContent sx={{ px: 3, pt: 0.5, pb: 2.5 }}>
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
              component="form"
              id="escalation-settings-form"
              onSubmit={submit}
              noValidate
            >
              {!scopedData.can_edit && (
                <Alert severity="info" sx={{ mb: 1.75 }}>
                  Only workspace administrators can change these settings.
                </Alert>
              )}
              {saveError && (
                <Alert
                  severity={saveError === "conflict" ? "warning" : "error"}
                  sx={{ mb: 1.75 }}
                  action={
                    saveError === "conflict" ? (
                      <Button
                        size="small"
                        onClick={reload}
                        sx={{ whiteSpace: "nowrap" }}
                      >
                        Reload settings
                      </Button>
                    ) : undefined
                  }
                >
                  {saveError === "conflict"
                    ? "Settings changed. Reload the latest settings before saving."
                    : "Could not save escalation settings. Your changes are retained."}
                </Alert>
              )}
              {reloaded && (
                <Alert severity="success" sx={{ mb: 1.75 }}>
                  Loaded the latest settings.
                </Alert>
              )}

              <ToggleButtonGroup
                exclusive
                value={active}
                onChange={choosePreset}
                disabled={!editable}
                aria-label="Sensitivity"
                sx={{
                  display: "grid",
                  gridTemplateColumns: {
                    xs: "1fr 1fr",
                    sm: "repeat(3, 1fr) 0.85fr",
                  },
                  gap: 0.5,
                  "& .MuiToggleButtonGroup-grouped": {
                    m: 0,
                    flexDirection: "column",
                    "&.Mui-selected, &.Mui-selected:hover": {
                      ...accentSx,
                      borderColor: (t) =>
                        `${t.palette.primary.main} !important`,
                    },
                    "&.Mui-disabled": { opacity: 0.6 },
                    "&.Mui-disabled.Mui-selected": { opacity: 1, ...accentSx },
                  },
                }}
              >
                {PRESETS.map((preset) => (
                  <ToggleButton
                    key={preset.key}
                    value={preset.key}
                    aria-label={`${preset.label}: ${preset.description}`}
                  >
                    <Typography typography="s1" fontWeight="fontWeightSemiBold">
                      {preset.label}
                    </Typography>
                    <Typography
                      sx={{
                        fontSize: 11,
                        lineHeight: "16px",
                        color:
                          active === preset.key ? "inherit" : "text.secondary",
                      }}
                    >
                      {preset.hint}
                    </Typography>
                  </ToggleButton>
                ))}
                <ToggleButton
                  value="custom"
                  aria-label="Custom: set each value under Fine-tune"
                  sx={{
                    ...(active !== "custom" && {
                      borderStyle: "dashed !important",
                      borderColor: (t) => `${t.palette.divider} !important`,
                    }),
                  }}
                >
                  <Typography typography="s1" fontWeight="fontWeightSemiBold">
                    Custom
                  </Typography>
                  <Typography
                    sx={{
                      fontSize: 11,
                      lineHeight: "16px",
                      color: active === "custom" ? "inherit" : "text.secondary",
                    }}
                  >
                    {active === "custom" ? "Your values" : "Set each value"}
                  </Typography>
                </ToggleButton>
              </ToggleButtonGroup>

              <Box sx={{ mt: 1.75 }}>
                <EscalationSummary
                  values={shown}
                  usual={usual}
                  onUsualChange={setUsual}
                  stale={!valid}
                  baselineHours={scopedData.baseline_hours ?? 24}
                />
              </Box>

              <Accordion
                expanded={tuneOpen}
                onChange={(_event, expanded) => setTuneOpen(expanded)}
                disableGutters
                sx={{ ...disclosureSx, mt: 1.25 }}
              >
                <AccordionSummary
                  expandIcon={
                    <Iconify icon="eva:chevron-right-fill" width={20} />
                  }
                >
                  <Typography typography="s1" fontWeight="fontWeightSemiBold">
                    Fine-tune
                  </Typography>
                  {errorNames.length > 0 && !tuneOpen && (
                    <Chip
                      size="small"
                      color="error"
                      variant="soft"
                      label={`${errorNames.length} to fix`}
                      sx={{ ml: 1 }}
                    />
                  )}
                  {active === "custom" && (
                    <Chip
                      size="small"
                      label="Edited"
                      sx={{
                        ml: "auto",
                        mr: 1,
                        color: "text.secondary",
                        bgcolor: "action.hover",
                      }}
                    />
                  )}
                </AccordionSummary>
                <AccordionDetails>
                  <Box
                    sx={{
                      display: "grid",
                      gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                      columnGap: 4,
                      rowGap: 3,
                    }}
                  >
                    {fieldGroup(
                      "Escalate",
                      "Count must clear all three limits for the hold time.",
                      ENTRY,
                      entryDecides,
                      `${escalateAt(shown, usual).toLocaleString("en-US")}+/hr`,
                      "error.main",
                    )}
                    {fieldGroup(
                      "Recover",
                      "Count must drop to the highest limit for the hold time.",
                      RECOVERY,
                      recoveryDecides,
                      `${recoverAt(shown, usual).toLocaleString("en-US")} or fewer`,
                      "warning.main",
                    )}
                  </Box>
                </AccordionDetails>
              </Accordion>

              <Accordion
                expanded={previewOpen}
                onChange={(_event, expanded) => setPreviewOpen(expanded)}
                disableGutters
                TransitionProps={{ unmountOnExit: true }}
                sx={disclosureSx}
              >
                <AccordionSummary
                  expandIcon={
                    <Iconify icon="eva:chevron-right-fill" width={20} />
                  }
                >
                  <Typography typography="s1" fontWeight="fontWeightSemiBold">
                    Preview on a sample issue
                  </Typography>
                </AccordionSummary>
                <AccordionDetails>
                  <EscalationPreviewChart
                    values={shown}
                    usual={usual}
                    stale={!valid}
                  />
                </AccordionDetails>
              </Accordion>
            </Stack>
          )}
        </DialogContent>
        <DialogActions
          sx={{
            px: 3,
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
              color="inherit"
              disabled={!editable || atDefaults}
              startIcon={<Iconify icon="eva:refresh-outline" width={16} />}
              sx={{ mr: "auto", color: "text.secondary" }}
              onClick={() => applyValues(defaults)}
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
            {ready && !scopedData.can_edit ? "Close" : "Cancel"}
          </Button>
          {ready && scopedData.can_edit && (
            <LoadingButton
              variant="contained"
              color="primary"
              size="small"
              type="submit"
              form="escalation-settings-form"
              loading={mutation.isPending}
              disabled={!editable || !isDirty || saveError === "conflict"}
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
