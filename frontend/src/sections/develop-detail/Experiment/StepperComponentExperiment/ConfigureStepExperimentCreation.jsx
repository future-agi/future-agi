import { CLASSIC_EXPERIMENT_COPY } from "src/launch/classicExperimentCopy";
import { isSurfaceShown } from "src/launch/launchSwitch";
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useController, useWatch } from "react-hook-form";
import { ShowComponent } from "src/components/show";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import ConfigureStepSTT from "./ConfigureStepSTT";
import ConfigureStepLLM from "./ConfigureStepLLM";
import ConfigureStepTTS from "./ConfigureStepTTS";
import { MODEL_TYPES } from "../../RunPrompt/common";
import { experimentTypeOptions, getOutputOptions } from "../constants";
import { useExperimentForm } from "../ExperimentFormContext";
import StartFromExperiment from "../StartFromExperiment";
import { Section, useLayoutPrefs } from "src/sections/common/ux/oneview";
import {
  configuredCardCount,
  moreSettingsChanges,
  typeSwitchText,
} from "../moreSettings";
import { usePickerUi } from "src/sections/common/RunSources/pickerUi";
import { heldText, wizardSettingShown } from "src/launch/wizardSettings";

// forwardRef's inferred empty props omit the wrapper's MUI pass-through props.
const ExperimentNameField =
  /** @type {React.ComponentType<React.ComponentProps<typeof FormTextFieldV2> & import("@mui/material").TextFieldProps & { control?: import("react-hook-form").Control, fieldName: string }>} */ (
    FormTextFieldV2
  );

/**
 * @typedef {object} ExperimentNameState
 * @property {React.FocusEventHandler<HTMLInputElement>} [onFocus]
 * @property {boolean} [suggested]
 * @property {boolean} [checking]
 * @property {boolean} [taken]
 * @property {boolean} [valid]
 */

const NEWEXP_SURFACE = "newExperiment";
const MORE_SECTION = "newexp.more";

const ExperimentTypeSelect = ({ control, onChange, disabled }) => {
  // Both wizards offer every type the backend runs: LLM, TTS, STT, Image.
  const typeOptions = experimentTypeOptions;
  const { field } = useController({ control, name: "experimentType" });
  const cards = useWatch({ control, name: "promptConfig" });
  // v2.1 (type:llm): a type switch that would drop configured runs asks.
  const [pending, setPending] = React.useState(null);
  const apply = (value) => {
    field.onChange(value);
    onChange?.({ target: { value } });
  };
  const dropped = configuredCardCount(cards);
  const fromLabel =
    typeOptions.find((x) => x.value === (field.value ?? "llm"))?.label ??
    "this type";
  const toLabel = typeOptions.find((x) => x.value === pending)?.label;
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
      <Dialog
        open={Boolean(pending)}
        onClose={() => setPending(null)}
        data-testid="type-switch-confirm"
      >
        <DialogTitle>{`Switch to ${toLabel ?? "another type"}?`}</DialogTitle>
        <DialogContent>
          <Typography typography="s1">
            {typeSwitchText(dropped, fromLabel)}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPending(null)}>Keep my runs</Button>
          <Button
            variant="contained"
            color="error"
            onClick={() => {
              const v = pending;
              setPending(null);
              apply(v);
            }}
          >
            {`Switch and remove ${dropped === 1 ? "the run" : "them"}`}
          </Button>
        </DialogActions>
      </Dialog>
      <Typography typography="s2" color="text.secondary">
        Experiment type
      </Typography>
      <Select
        size="small"
        inputProps={{ "aria-label": CLASSIC_EXPERIMENT_COPY.experimentType }}
        value={field.value ?? "llm"}
        disabled={disabled}
        onChange={(e) => {
          const v = e.target.value;
          if (v === (field.value ?? "llm")) return;
          if (configuredCardCount(cards) > 0) setPending(v);
          else apply(v);
        }}
        renderValue={(v) => {
          const o = typeOptions.find((x) => x.value === v);
          return (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
              <SvgColor src={o?.icon} sx={{ width: 16, height: 16 }} />
              <Typography typography="s2" fontWeight="fontWeightMedium">
                {o?.label}
              </Typography>
            </Box>
          );
        }}
        sx={{ height: 30, minWidth: 120, bgcolor: "background.paper" }}
        MenuProps={{ slotProps: { paper: { sx: { width: 300 } } } }}
      >
        {typeOptions.map((o) => (
          <MenuItem key={o.value} value={o.value} sx={{ gap: 1, py: 0.75 }}>
            <SvgColor src={o.icon} sx={{ width: 18, height: 18 }} />
            <Box>
              <Typography typography="s1" fontWeight="fontWeightMedium">
                {o.label}
              </Typography>
              <Typography typography="s3" color="text.secondary">
                {o.subtitle}
              </Typography>
            </Box>
          </MenuItem>
        ))}
      </Select>
    </Box>
  );
};
ExperimentTypeSelect.propTypes = {
  control: PropTypes.object,
  onChange: PropTypes.func,
  disabled: PropTypes.bool,
};

/**
 * Step 1 · Configuration (PRODUCT-SPEC §3.8): name (#N chip), description,
 * the existing experiment type, then the runs. Text experiments add runs
 * of any target; TTS / STT / Image keep today's configure UI.
 */
const ConfigureStepExperimentCreation = ({
  control,
  allColumns,
  getValues,
  setValue,
  unregister,
  clearErrors,
  errors,
  watch,
  jsonSchemas,
  isEditing = false,
  derivedVariables,
  handleModelTypeChange,
  nameState = /** @type {ExperimentNameState} */ ({}),
  showErrors = false,
  failedNext = 0,
}) => {
  const { nextOrdinal } = useExperimentForm();
  const watchModelType = useWatch({ control, name: "experimentType" });
  const startFrom = useWatch({ control, name: "startFrom" });
  // A2-15: `More settings` (experiment type, Start from) is collapsed by
  // default and opens by itself when something in it is not the default
  // (a Start from, a non-text type). The user's own toggle is remembered
  // (layout prefs, per user) until a value changes it again.
  const changes = moreSettingsChanges({
    experimentType: watchModelType,
    startFrom,
  });
  const nonDefault = changes.length > 0;
  const [, patchLayout] = useLayoutPrefs(NEWEXP_SURFACE);
  const wasNonDefault = React.useRef(nonDefault);
  React.useEffect(() => {
    if (nonDefault && !wasNonDefault.current)
      patchLayout({ sections: { [MORE_SECTION]: undefined } });
    wasNonDefault.current = nonDefault;
  }, [nonDefault, patchLayout]);
  const { checking, taken, valid, suggested } = nameState;
  const runsForName = useWatch({ control, name: "promptConfig" }) ?? [];
  // IT-R2 G1 P4: an untouched name is a greyed suggestion that follows the
  // runs; typing replaces it for good.
  const nameHelp = taken
    ? "An experiment with this name already exists on this dataset"
    : suggested
      ? runsForName.length
        ? "Suggested from your runs. Type to set your own."
        : undefined
      : undefined;
  // IT-R4 G1 P4: the suggested name is real text (not a grey placeholder)
  // and not marked required while a default exists.
  const suggestedSx = {};
  // R4 (lane Ra MUST 1): while no run is added, the picker comes first;
  // name and description sit in one inline row and More settings moves
  // below the picker.
  const runsNow = useWatch({ control, name: "promptConfig" }) ?? [];
  const description = useWatch({ control, name: "description" });
  const pickerFirst =
    isSurfaceShown("exp.new") &&
    !isEditing &&
    watchModelType === MODEL_TYPES.LLM &&
    !runsNow.length;
  const [showDescription, setShowDescription] = React.useState(false);
  // L6 launch switch (exp:description, more:*): dev keeps no description
  // and no More settings; a launch build shows them only while they hold a
  // value that is not the default (src/launch/wizardSettings.js).
  const descriptionShown =
    isSurfaceShown("exp.new") &&
    wizardSettingShown("description", heldText(description));
  const moreSettingsShown =
    isSurfaceShown("exp.new") && wizardSettingShown("moreSettings", nonDefault);
  // Level 3 is about one run: the experiment's name row steps aside (it is
  // never renamed by a pick, P2) and comes back after Add to experiment.
  const pickerLevel = usePickerUi((st) => st.level);
  const watchName = useWatch({ control, name: "name" });
  const [headOpen, setHeadOpen] = React.useState(false);
  React.useEffect(() => {
    if (pickerLevel === 0) setHeadOpen(false);
  }, [pickerLevel]);

  const moreSettings = moreSettingsShown ? (
    <Section
      id={MORE_SECTION}
      surface={NEWEXP_SURFACE}
      title="More settings"
      defaultOpen={nonDefault}
      // A2-15: the collapsed header lists what is not the default.
      summary={changes.length ? changes.join(" · ") : null}
      keepMounted
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        px: 1,
        bgcolor: "background.paper",
      }}
    >
      <Box
        data-testid="new-experiment-more"
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 1.25,
          pb: 1.25,
          pl: 3,
        }}
      >
        <ExperimentTypeSelect
          control={control}
          onChange={handleModelTypeChange}
          disabled={isEditing}
        />
        {!isEditing && watchModelType === MODEL_TYPES.LLM ? (
          <StartFromExperiment
            control={control}
            setValue={setValue}
            getValues={getValues}
          />
        ) : null}
      </Box>
    </Section>
  ) : null;

  if (pickerFirst)
    return (
      <Stack spacing={1.5}>
        <Box
          data-testid="experiment-name-row"
          sx={{
            display: pickerLevel === 3 ? "none" : "flex",
            gap: 1,
            alignItems: "center",
          }}
        >
          <Box sx={{ position: "relative", flex: 1, minWidth: 0 }}>
            <ExperimentNameField
              control={control}
              fieldName="name"
              onFocus={nameState.onFocus}
              label="Experiment name"
              size="small"
              fullWidth
              required={!suggested}
              placeholder="Name this experiment"
              helperText={nameHelp}
              error={Boolean(taken)}
              sx={suggestedSx}
            />
          </Box>
          {isSurfaceShown("exp.new") && nextOrdinal != null ? (
            <Box
              data-testid="experiment-ordinal-chip"
              sx={{
                px: 1,
                height: 26,
                display: "flex",
                alignItems: "center",
                borderRadius: 0.5,
                border: "1px solid",
                borderColor: "divider",
                bgcolor: "background.neutral",
                typography: "s1",
                fontWeight: "fontWeightSemiBold",
                flexShrink: 0,
              }}
            >
              #{nextOrdinal}
            </Box>
          ) : null}
          {showDescription || description || !descriptionShown ? null : (
            <Button
              size="small"
              onClick={() => setShowDescription(true)}
              startIcon={<Iconify icon="mdi:plus" width={14} />}
              sx={{ flexShrink: 0, typography: "s2", color: "text.secondary" }}
            >
              Description
            </Button>
          )}
        </Box>
        {(showDescription || description) &&
        descriptionShown &&
        pickerLevel !== 3 ? (
          <FormTextFieldV2
            control={control}
            fieldName="description"
            label="Description (optional)"
            size="small"
            fullWidth
            multiline
            minRows={1}
            maxRows={3}
            autoFocus={showDescription && !description}
            placeholder="What are you testing? e.g. Does citing the refund policy stop invented timelines?"
          />
        ) : null}
        <ConfigureStepLLM
          unregister={unregister}
          watch={watch}
          getValues={getValues}
          control={control}
          setValue={setValue}
          showErrors={showErrors}
          failedNext={failedNext}
          isEditing={isEditing}
        />
        {/* IT-R1 G1 P1: Level 3 is about one run; its Advanced row is the
            only settings row there (experiment settings come back after
            Add to experiment). */}
        {pickerLevel === 3 ? null : moreSettings}
      </Stack>
    );

  // IT-R5 G1 P7: while a run editor (the picker) is open, the name,
  // description and More settings fold into one line, so the editor gets
  // the drawer's height. `Edit` unfolds them.
  const compactHead =
    isSurfaceShown("exp.new") && pickerLevel > 0 && !headOpen && !isEditing;
  const compactRow = (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      data-testid="experiment-head-compact"
      sx={{ minWidth: 0 }}
    >
      <CustomTooltip
        show
        title={`${nextOrdinal != null ? `#${nextOrdinal} ` : ""}${watchName || ""}${description ? ` · ${description}` : ""}`}
        size="small"
        placement="bottom-start"
      >
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {`${nextOrdinal != null ? `#${nextOrdinal} ` : ""}${watchName || "Untitled experiment"}`}
        </Typography>
      </CustomTooltip>
      <Button
        size="small"
        onClick={() => setHeadOpen(true)}
        data-testid="experiment-head-edit"
        sx={{ flexShrink: 0, typography: "s2", minWidth: 0, px: 0.75 }}
      >
        Edit
      </Button>
    </Stack>
  );

  return (
    <Stack spacing={2}>
      {!isSurfaceShown("exp.new") && (
        <ExperimentTypeSelect
          control={control}
          onChange={handleModelTypeChange}
          disabled={isEditing}
        />
      )}
      {compactHead ? compactRow : null}
      <Box
        sx={{
          display: compactHead ? "none" : "flex",
          gap: 1.5,
          alignItems: "flex-start",
        }}
      >
        <Box sx={{ position: "relative", flex: 1 }}>
          <ExperimentNameField
            control={control}
            fieldName="name"
            disabled={isEditing && !isSurfaceShown("exp.new")}
            onFocus={nameState.onFocus}
            label="Experiment name"
            size="small"
            fullWidth
            required={!suggested}
            placeholder="e.g. Support reply v5 · temperature sweep"
            helperText={nameHelp}
            error={Boolean(taken)}
            sx={{ "& .MuiOutlinedInput-root": { pr: "40px" }, ...suggestedSx }}
          />
          <Box
            sx={{
              position: "absolute",
              right: 12,
              top: 20,
              transform: "translateY(-50%)",
              display: "flex",
              pointerEvents: "none",
            }}
          >
            {checking ? (
              <CircularProgress size={14} sx={{ color: "text.disabled" }} />
            ) : valid ? (
              <Iconify
                icon="mdi:check-circle"
                width={16}
                sx={{ color: "success.main" }}
              />
            ) : null}
          </Box>
        </Box>
        {isSurfaceShown("exp.new") && nextOrdinal != null ? (
          <CustomTooltip
            show
            title={`Runs will be labelled #${nextOrdinal}A, #${nextOrdinal}B…`}
            size="small"
            type="black"
            arrow
          >
            <Box
              data-testid="experiment-ordinal-chip"
              sx={{
                mt: 0.75,
                px: 1,
                height: 26,
                display: "flex",
                alignItems: "center",
                borderRadius: 0.5,
                border: "1px solid",
                borderColor: "divider",
                bgcolor: "background.neutral",
                typography: "s1",
                fontWeight: "fontWeightSemiBold",
                color: "text.primary",
              }}
            >
              #{nextOrdinal}
            </Box>
          </CustomTooltip>
        ) : null}
      </Box>
      {compactHead || !descriptionShown ? null : (
        <FormTextFieldV2
          control={control}
          fieldName="description"
          label="Description (optional)"
          size="small"
          fullWidth
          multiline
          minRows={1}
          maxRows={4}
          placeholder="What are you testing? e.g. Does citing the refund policy stop invented timelines?"
        />
      )}
      {compactHead ? null : moreSettings}

      <ShowComponent condition={watchModelType === MODEL_TYPES.LLM}>
        <ConfigureStepLLM
          unregister={unregister}
          watch={watch}
          getValues={getValues}
          control={control}
          setValue={setValue}
          showErrors={showErrors}
          failedNext={failedNext}
          isEditing={isEditing}
        />
      </ShowComponent>
      <ShowComponent
        condition={
          watchModelType === MODEL_TYPES.TTS ||
          watchModelType === MODEL_TYPES.IMAGE
        }
      >
        <ConfigureStepTTS
          control={control}
          allColumns={allColumns}
          setValue={setValue}
          clearErrors={clearErrors}
          errors={errors}
          watch={watch}
          jsonSchemas={jsonSchemas}
          getValues={getValues}
          derivedVariables={derivedVariables}
          unregister={unregister}
        />
      </ShowComponent>
      <ShowComponent condition={watchModelType === MODEL_TYPES.STT}>
        <ConfigureStepSTT
          unregister={unregister}
          allColumns={allColumns}
          control={control}
          fieldPrefix={`promptConfig.0`}
          getValues={getValues}
          setValue={setValue}
        />
      </ShowComponent>
      <ShowComponent condition={watchModelType !== MODEL_TYPES.LLM}>
        <FormSearchSelectFieldControl
          fullWidth
          label="Output Format"
          size="small"
          required={true}
          control={control}
          fieldName="outputFormat"
          disabled={
            watchModelType === MODEL_TYPES.TTS ||
            watchModelType === MODEL_TYPES.IMAGE
          }
          options={getOutputOptions[watchModelType] || []}
        />
      </ShowComponent>
    </Stack>
  );
};

export default ConfigureStepExperimentCreation;

ConfigureStepExperimentCreation.propTypes = {
  control: PropTypes.object,
  errors: PropTypes.object.isRequired,
  allColumns: PropTypes.array,
  getValues: PropTypes.func,
  setValue: PropTypes.func,
  unregister: PropTypes.func,
  clearErrors: PropTypes.func,
  watch: PropTypes.func,
  jsonSchemas: PropTypes.any,
  isEditing: PropTypes.bool,
  derivedVariables: PropTypes.object,
  handleModelTypeChange: PropTypes.func,
  nameState: PropTypes.shape({
    onFocus: PropTypes.func,
    suggested: PropTypes.bool,
    checking: PropTypes.bool,
    taken: PropTypes.bool,
    valid: PropTypes.bool,
  }),
  showErrors: PropTypes.bool,
  failedNext: PropTypes.number,
};
