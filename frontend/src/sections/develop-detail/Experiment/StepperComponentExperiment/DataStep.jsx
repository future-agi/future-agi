import { isSurfaceShown } from "src/launch/launchSwitch";
import ClassicExperimentData from "../ClassicExperimentData";
import React, { useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ListSubheader,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import { useController, useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { SplitChip } from "src/sections/common/Datasets/SplitChips";
import { VersionNumber } from "src/sections/common/Datasets/VersionChip";
import { fDateTime } from "src/utils/format-time";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import VariableMappingTable from "../VariableMappingTable";
import ReadsOnSampleTable from "./ReadsOnSampleTable";
import SampleSetting from "./SampleSetting";
import { referenceHelpOf, remapReference } from "../readsOnSampleModel";
import { dataIssues } from "../stepIssues";
import { EXPERIMENT_STEP } from "../constants";
import { useRunExperimentStore } from "../../states";
import { useChangeSetStore } from "../../DataTab/Versions/changeSetStore";
import { useUnsavedSelection } from "../../DataTab/Versions/useUnsavedSelection";
import UnsavedRowsBanner from "../../DataTab/Versions/UnsavedRowsBanner";
import {
  doorSplitOf,
  pendingBySplit,
  remapRowIds,
} from "../../DataTab/Versions/unsavedSelection";
import { useSelectedSampleContext } from "../../DataTab/Versions/selectedSample";
import RunLetterBadge from "../RunLetterBadge";
import { runInputLines, runInputsOf } from "../runInputs";
import { useVersionSplitCounts } from "../useVersionScope";
import { ExperimentFormContext, rowLabelOf } from "../ExperimentFormContext";
import { PROMPT_CONFIG_TYPE } from "../constants";
import {
  isExistingCard,
  lettersByConfig,
  runDisplayName,
  targetTypeOf,
} from "../runPlan";
import {
  heldList,
  heldText,
  wizardSettingShown,
} from "src/launch/wizardSettings";

const Section = ({ title, helper, children, right }) => (
  <Box>
    <Box sx={{ display: "flex", alignItems: "baseline", gap: 1, mb: 0.75 }}>
      <Typography typography="s1_2" fontWeight="fontWeightMedium">
        {title}
      </Typography>
      <Box sx={{ flex: 1 }} />
      {right}
    </Box>
    {children}
    {helper ? (
      <Typography typography="s3" color="text.secondary" sx={{ mt: 0.5 }}>
        {helper}
      </Typography>
    ) : null}
  </Box>
);
Section.propTypes = {
  title: PropTypes.string,
  helper: PropTypes.node,
  children: PropTypes.node,
  right: PropTypes.node,
};

const CODE_LIKE = [
  PROMPT_CONFIG_TYPE.HARNESS,
  PROMPT_CONFIG_TYPE.CODE,
  PROMPT_CONFIG_TYPE.EXTERNAL,
  PROMPT_CONFIG_TYPE.TRACES,
];

const plural = (n, one, many = `${one}s`) =>
  `${Number(n).toLocaleString()} ${n === 1 ? one : many}`;

const versionText = (v, latestNumber) => {
  const tag = v?.tags?.[0];
  if (v?.number === latestNumber)
    return `Latest · v${v.number}${tag ? ` · ${tag}` : ""}`;
  return tag ? `${tag} · v${v.number}` : `v${v.number}`;
};

const typeIcon = (type) =>
  type === PROMPT_CONFIG_TYPE.PROMPT ? (
    <SvgColor
      src="/assets/icons/navbar/ic_prompt.svg"
      sx={{ width: 16, height: 16, color: "text.secondary" }}
    />
  ) : type === PROMPT_CONFIG_TYPE.AGENT ? (
    <SvgColor
      src="/assets/icons/navbar/ic_agents.svg"
      sx={{ width: 16, height: 16, color: "text.secondary" }}
    />
  ) : type === PROMPT_CONFIG_TYPE.HTTP ? (
    <Iconify icon="mdi:api" width={16} sx={{ color: "text.secondary" }} />
  ) : (
    <Iconify
      icon="mdi:code-braces"
      width={16}
      sx={{ color: "text.secondary" }}
    />
  );

/**
 * Step 2 · Data (PRODUCT-SPEC §3.8): dataset version, splits (union),
 * reference column, then one variable-mapping table per run checked
 * against a sample row. Version, splits are read-only when editing.
 */
const EnhancedDataStep = ({
  control,
  setValue,
  isEditing = false,
  showErrors = false,
}) => {
  // IT-R2 G5 P2 (S8): the selected rows come first and the Sample is the
  // first of them (pending door rows included); children read the same.
  const sampleCtx = useSelectedSampleContext(control);
  const {
    datasetId,
    versions,
    splits,
    exampleCount,
    rowsInScope,
    rows,
    sampleRow,
    sampleRowLabel,
    setSampleRowId,
    userColumns,
  } = sampleCtx;
  const labelOfRow = sampleCtx.rowLabel ?? rowLabelOf;
  // v2.1 (data:replay HIDE): conversation datasets have no Replay mode
  // choice; their runs map the conversation like any other column.
  const { kind: datasetKind } = useDatasetKind(datasetId);
  const { field: versionField } = useController({
    control,
    name: "datasetVersionId",
  });
  const { field: splitField } = useController({ control, name: "splitIds" });
  // P5 (ir-2, ir-3): the rows a door selected arrive preselected.
  const { field: rowsField } = useController({ control, name: "rowIds" });
  const rowIds = rowsField.value ?? [];
  const [doorRows, setDoorRows] = React.useState(() => rowsField.value ?? []);
  const pendingOps = useChangeSetStore((st) => st.sets?.[datasetId]?.ops);
  // IT-R1 G5 P1: unsaved door rows block the run; `Save vN and continue`
  // saves, follows them into the new version and opens Review.
  const evalsNow = useWatch({ control, name: "userEvalMetrics" });
  const evalsRef = React.useRef(evalsNow);
  evalsRef.current = evalsNow;
  const unsaved = useUnsavedSelection(control, {
    guard: !isEditing,
    onRemap: (map) => setDoorRows((ids) => remapRowIds(ids, map)),
    // P1: a re-test of production failures scores what failed there.
    applyEvals: (fn) =>
      setValue("userEvalMetrics", fn(evalsRef.current ?? []), {
        shouldDirty: true,
      }),
  });
  // P2: rows imported into one split arrive with that split preselected, so
  // the same rows stay one click away after the toast is gone.
  const pendingInSplit = useMemo(
    () => pendingBySplit(pendingOps),
    [pendingOps],
  );
  useEffect(() => {
    if (isEditing || (splitField.value ?? []).length) return;
    const split = doorSplitOf(pendingOps, doorRows);
    if (split) splitField.onChange([split]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // IT-R4 G1 P1: `Save as vN and continue` saves the rows but stays on Data
  // while a variable has no column (the save moves on to Evaluations).
  const allValues = useWatch({ control });
  const mapColumns = sampleCtx.mappingColumns;
  const unmappedNow =
    dataIssues(allValues, { columns: mapColumns ?? [], isEditing }).length > 0;
  const saveAndStayIfUnmapped = async () => {
    const stay = unmappedNow;
    await unsaved.saveAndContinue();
    if (
      stay &&
      useRunExperimentStore.getState().currentStep ===
        EXPERIMENT_STEP.EVALUATIONS
    )
      useRunExperimentStore.getState().setCurrentStep(EXPERIMENT_STEP.DATA);
  };
  const { field: refField } = useController({ control, name: "columnId" });
  const experimentType = useWatch({ control, name: "experimentType" });
  const promptConfig = useWatch({ control, name: "promptConfig" }) ?? [];
  const latestNumber = Math.max(0, ...(versions ?? []).map((v) => v.number));
  const selectedVersion =
    (versions ?? []).find((v) => v.id === versionField.value) ??
    (versions ?? []).find((v) => v.number === latestNumber);
  const splitIds = splitField.value ?? [];
  // v2.1 (data:rows:split): chip counts at the pinned version.
  const versionSplitCounts = useVersionSplitCounts(
    datasetId,
    versions,
    versionField.value,
    splits,
  );
  const chosenSplits = (splits ?? []).filter((s) => splitIds.includes(s.id));
  const letters = lettersByConfig(promptConfig);
  // P6: harness / code runs take each row's instruction as is; when no run
  // has a {{variable}}, the mapping area is one sentence, not a card per run.
  const mapsNothing =
    promptConfig.length > 0 &&
    promptConfig.every((item) => !(item?.variables ?? []).length);
  const codeOnly = promptConfig.every((item) =>
    CODE_LIKE.includes(targetTypeOf(item)),
  );
  // v2.1 (data:nothing-to-map): what agent, A2A and MCP runs send per row.
  const inputLines = runInputLines(promptConfig, userColumns, {
    labelOf: (item) => runDisplayName(item),
  });

  const tagged = (versions ?? []).filter(
    (v) => v.number !== latestNumber && v.tags?.length,
  );
  const others = (versions ?? []).filter(
    (v) => v.number !== latestNumber && !v.tags?.length,
  );
  const latest = (versions ?? []).find((v) => v.number === latestNumber);

  // Held settings stay visible until explicitly cleared. A held dataset
  // scope does not enable the hidden picker or its version copy.
  const versionsAvailable = isSurfaceShown("dataset.versions");
  const versionShown = wizardSettingShown(
    "version",
    heldText(versionField.value),
  );
  const splitChipsShown = wizardSettingShown("splits", heldList(splitIds));
  const theseRowsShown = wizardSettingShown("theseRows", heldList(rowIds));
  const scopeShown = splitChipsShown || theseRowsShown;

  const toggleSplit = (id) => {
    rowsField.onChange([]);
    const next = splitIds.includes(id)
      ? splitIds.filter((x) => x !== id)
      : [...splitIds, id];
    splitField.onChange(next);
  };

  const countText = rowIds.length
    ? `Runs on ${plural(rowIds.length, "selected row")}${versionsAvailable && selectedVersion ? ` · v${selectedVersion.number}` : ""}`
    : exampleCount == null
      ? "Counting examples…"
      : chosenSplits.length
        ? `${plural(exampleCount, "example")} (${chosenSplits.map((s) => s.name).join(" ∪ ")})`
        : `${plural(exampleCount, "example")} (all rows)`;

  const versionItem = (v) => (
    <MenuItem key={v.id} value={v.id} dense sx={{ gap: 1 }}>
      <VersionNumber number={v.number} />
      <Typography typography="s2" fontWeight="fontWeightMedium">
        {versionText(v, latestNumber)}
      </Typography>
      <Typography typography="s3" color="text.secondary" sx={{ ml: "auto" }}>
        {plural(v.row_count ?? 0, "example")}
        {v.number !== latestNumber && v.created_at
          ? ` · ${fDateTime(v.created_at, "MMM d, HH:mm")}`
          : ""}
      </Typography>
    </MenuItem>
  );

  return (
    <ExperimentFormContext.Provider value={sampleCtx}>
      <Stack spacing={2.25}>
        {isEditing ? (
          <Box
            sx={{
              display: "flex",
              gap: 1,
              alignItems: "center",
              px: 1.25,
              py: 0.75,
              borderRadius: 0.5,
              bgcolor: "blue.o5",
              border: "1px solid",
              borderColor: "blue.o20",
            }}
          >
            <Iconify
              icon="mdi:lock-outline"
              width={16}
              sx={{ color: "blue.600" }}
            />
            <Typography typography="s2">
              Create a new experiment to change data or trials. This keeps
              results comparable.
            </Typography>
          </Box>
        ) : null}

        <Box sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 2 }}>
          {versionsAvailable ? (
            <Section
              title="Dataset version"
              helper="The experiment is pinned to this version. Later edits to the dataset don't change its inputs."
            >
              <Select
                size="small"
                fullWidth
                disabled={isEditing}
                value={selectedVersion?.id ?? ""}
                onChange={(e) => versionField.onChange(e.target.value)}
                renderValue={() =>
                  selectedVersion ? (
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Typography typography="s2" fontWeight="fontWeightMedium">
                        {versionText(selectedVersion, latestNumber)}
                      </Typography>
                      <Typography typography="s3" color="text.secondary">
                        {plural(selectedVersion.row_count ?? 0, "example")}
                      </Typography>
                    </Box>
                  ) : (
                    <Typography typography="s2" color="text.disabled">
                      Loading versions…
                    </Typography>
                  )
                }
                sx={{ height: 34, bgcolor: "background.paper" }}
                data-testid="experiment-version-select"
              >
                {latest ? versionItem(latest) : null}
                {tagged.length ? (
                  <ListSubheader sx={{ typography: "s3", lineHeight: "28px" }}>
                    Tagged
                  </ListSubheader>
                ) : null}
                {tagged.map(versionItem)}
                {others.length ? (
                  <ListSubheader sx={{ typography: "s3", lineHeight: "28px" }}>
                    Versions
                  </ListSubheader>
                ) : null}
                {others.map(versionItem)}
              </Select>
            </Section>
          ) : versionShown ? (
            <Section
              title="Dataset selection"
              helper="A new experiment can't run with this selection. Clear it to use the dataset's current rows; row and split selections stay as they are. Existing experiments keep their original data."
            >
              <Button
                type="button"
                size="small"
                variant="outlined"
                onClick={() => versionField.onChange("")}
              >
                Clear selection
              </Button>
            </Section>
          ) : null}

          <Section
            title="Expected answers column"
            helper={referenceHelpOf(evalsNow ?? [], userColumns ?? [])}
          >
            <Select
              size="small"
              fullWidth
              displayEmpty
              value={refField.value ?? ""}
              onChange={(e) => {
                // W1-5: the evals' reference inputs follow the pick here
                // too (the Evaluations step is not mounted on this step).
                const next = e.target.value || null;
                const evals = evalsNow ?? [];
                const remapped = remapReference(evals, refField.value, next);
                refField.onChange(next);
                if (remapped !== evals)
                  setValue("userEvalMetrics", remapped, { shouldDirty: true });
              }}
              renderValue={(val) => {
                const col = (userColumns ?? []).find((c) => c.id === val);
                return col ? (
                  <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
                    <Typography typography="s2" fontWeight="fontWeightMedium">
                      {col.name}
                    </Typography>
                    {col.role === "expected" ? (
                      <Typography typography="s3" color="text.secondary">
                        Expected
                      </Typography>
                    ) : null}
                  </Box>
                ) : (
                  <Typography typography="s2" color="text.disabled">
                    No reference column
                  </Typography>
                );
              }}
              sx={{ height: 34, bgcolor: "background.paper" }}
            >
              <MenuItem value="" dense>
                <Typography typography="s2" color="text.secondary">
                  No reference column
                </Typography>
              </MenuItem>
              {(userColumns ?? []).map((c) => (
                <MenuItem key={c.id} value={c.id} dense sx={{ gap: 1 }}>
                  <Typography typography="s2">{c.name}</Typography>
                  {c.role === "expected" ? (
                    <Typography typography="s3" color="text.secondary">
                      Expected
                    </Typography>
                  ) : null}
                </MenuItem>
              ))}
            </Select>
          </Section>
        </Box>

        <Section
          title={scopeShown ? "Splits" : "Rows"}
          helper={
            sampleCtx.rowRefsNote ? (
              <>
                {countText}
                <Box
                  component="span"
                  data-testid="row-refs-note"
                  role="status"
                  sx={{ display: "block", color: "warning.dark", mt: 0.25 }}
                >
                  {sampleCtx.rowRefsNote}
                </Box>
              </>
            ) : (
              countText
            )
          }
          right={
            splitChipsShown ? (
              <Typography typography="s3" color="text.secondary">
                Pick none to run on every row; several run on their union.
              </Typography>
            ) : null
          }
        >
          {scopeShown ? (
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
              {doorRows.length && theseRowsShown ? (
                <Box
                  role="button"
                  aria-pressed={rowIds.length > 0}
                  data-testid="these-rows"
                  onClick={() => {
                    if (isEditing) return;
                    rowsField.onChange(doorRows);
                    splitField.onChange([]);
                  }}
                  sx={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.5,
                    height: 26,
                    px: 1,
                    borderRadius: 0.5,
                    border: "1px solid",
                    borderColor: rowIds.length ? "primary.main" : "divider",
                    bgcolor: rowIds.length ? "purple.o10" : "background.paper",
                    cursor: isEditing ? "default" : "pointer",
                    typography: "s2",
                    fontWeight: "fontWeightMedium",
                  }}
                >
                  {doorRows.length === 1
                    ? "This row"
                    : `These ${plural(doorRows.length, "row")}`}
                </Box>
              ) : null}
              <Box
                role="button"
                aria-pressed={!splitIds.length && !rowIds.length}
                onClick={() => {
                  if (isEditing) return;
                  splitField.onChange([]);
                  rowsField.onChange([]);
                }}
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 0.5,
                  height: 26,
                  px: 1,
                  borderRadius: 0.5,
                  border: "1px solid",
                  borderColor:
                    !splitIds.length && !rowIds.length
                      ? "primary.main"
                      : "divider",
                  bgcolor:
                    !splitIds.length && !rowIds.length
                      ? "purple.o10"
                      : "background.paper",
                  cursor: isEditing ? "default" : "pointer",
                  typography: "s2",
                  fontWeight: "fontWeightMedium",
                }}
              >
                All rows
                <Typography
                  component="span"
                  typography="s3"
                  color="text.secondary"
                >
                  {selectedVersion?.row_count ?? ""}
                </Typography>
              </Box>
              {(splitChipsShown ? splits ?? [] : []).map((s) => {
                const on = splitIds.includes(s.id);
                return (
                  <Box
                    key={s.id}
                    role="button"
                    aria-pressed={on}
                    data-testid={`split-${s.name}`}
                    onClick={() => !isEditing && toggleSplit(s.id)}
                    sx={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 0.5,
                      height: 26,
                      pl: 0.5,
                      pr: 1,
                      borderRadius: 0.5,
                      border: "1px solid",
                      borderColor: on ? "primary.main" : "divider",
                      bgcolor: on ? "purple.o10" : "background.paper",
                      cursor: isEditing ? "default" : "pointer",
                    }}
                  >
                    <Iconify
                      icon={
                        on
                          ? "mdi:checkbox-marked"
                          : "mdi:checkbox-blank-outline"
                      }
                      width={16}
                      sx={{ color: on ? "primary.main" : "text.disabled" }}
                    />
                    <SplitChip split={s} size="small" />
                    <Typography
                      component="span"
                      typography="s3"
                      color="text.secondary"
                    >
                      {versionSplitCounts?.[s.id] ?? s.row_count}
                      {pendingInSplit[s.id]
                        ? ` + ${pendingInSplit[s.id]} unsaved`
                        : ""}
                    </Typography>
                  </Box>
                );
              })}
            </Box>
          ) : null}
          {!rowIds.length ? (
            <Box sx={{ mt: 1 }}>
              <SampleSetting
                control={control}
                inScope={rowsInScope ?? exampleCount}
                disabled={isEditing}
                noun={datasetKind === "task_suite" ? "task" : "row"}
              />
            </Box>
          ) : null}
          {unsaved.sentence ? (
            <Box sx={{ mt: 1 }}>
              <UnsavedRowsBanner
                sentence={unsaved.sentence}
                saveLabel={unsaved.saveLabel}
                onSave={saveAndStayIfUnmapped}
                nudge={unsaved.nudge}
                note={unsaved.note}
                onEditNote={unsaved.editNote}
                saving={unsaved.saving}
              />
              {unsaved.nudge ? (
                // v2.1 (data:unsaved-rows): Continue never moves silently
                // past unsaved rows; it says why it stayed.
                <Typography
                  typography="s2"
                  color="amber.700"
                  data-testid="unsaved-rows-continue-note"
                  sx={{ mt: 0.5 }}
                >
                  {`Continue waits for these rows: a run reads saved rows only. Use "${unsaved.saveLabel}".`}
                </Typography>
              ) : null}
            </Box>
          ) : null}
        </Section>

        {experimentType === "llm" && mapsNothing ? (
          <Section title="Variables">
            <Stack spacing={0.5}>
              {inputLines.map((l) => (
                <Box
                  key={l.index}
                  data-testid="run-inputs-line"
                  sx={{ display: "flex", alignItems: "center", gap: 1 }}
                >
                  {(letters[l.index] ?? []).map((x) => (
                    <RunLetterBadge key={x} letter={x} />
                  ))}
                  <Typography typography="s2" color="text.secondary">
                    {l.text}
                  </Typography>
                </Box>
              ))}
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="nothing-to-map"
              >
                {codeOnly
                  ? "These runs get every column of each row as it is — nothing to map."
                  : inputLines.length
                    ? "Set on each run's card — no {{variables}} to map here."
                    : "None of these runs use {{variables}} — nothing to map."}
              </Typography>
            </Stack>
          </Section>
        ) : experimentType === "llm" ? (
          <Section
            title="Variables"
            helper="Mapped automatically by name: exact, then case-insensitive, then normalized."
            right={
              rows?.length ? (
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
                  <Typography typography="s3" color="text.secondary">
                    Sample
                  </Typography>
                  <Select
                    size="small"
                    value={sampleRow?.row_id ?? ""}
                    onChange={(e) => setSampleRowId(e.target.value)}
                    renderValue={() => sampleRowLabel}
                    sx={{ height: 26, typography: "s2", minWidth: 96 }}
                    MenuProps={{
                      slotProps: { paper: { sx: { maxHeight: 300 } } },
                    }}
                    aria-label="Sample row"
                  >
                    {rows.map((r) => (
                      <MenuItem key={r.row_id} value={r.row_id} dense>
                        {labelOfRow(r)}
                      </MenuItem>
                    ))}
                  </Select>
                </Box>
              ) : null
            }
          >
            <Stack spacing={1}>
              {promptConfig.map((item, index) => {
                const type = targetTypeOf(item);
                // L7b G2 (vars:kept-in-edit): a kept card that knows what
                // its run ran with shows that mapping (a change re-runs it).
                const ranWith =
                  Boolean(item?.ranWith) ||
                  (item?.model ?? []).some((m) => m?.ranWith);
                const kept = isEditing && isExistingCard(item) && !ranWith;
                const inputs = runInputsOf(item, userColumns);
                if (inputs && !(item?.variables ?? []).length && !kept)
                  return (
                    <Box
                      key={item._itemId ?? index}
                      data-testid="run-inputs-line"
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        px: 1.25,
                        py: 1,
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: 0.5,
                      }}
                    >
                      {(letters[index] ?? []).map((l) => (
                        <RunLetterBadge key={l} letter={l} />
                      ))}
                      {typeIcon(type)}
                      <Typography typography="s1" fontWeight="fontWeightMedium">
                        {runDisplayName(item)}
                      </Typography>
                      <Typography typography="s2" color="text.secondary">
                        {`Each row sends ${inputs.join(" · ")} (set on its card).`}
                      </Typography>
                    </Box>
                  );
                if (type === PROMPT_CONFIG_TYPE.EXTERNAL || kept)
                  return (
                    <Box
                      key={item._itemId ?? index}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        px: 1.25,
                        py: 1,
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: 0.5,
                      }}
                    >
                      {(letters[index] ?? []).map((l) => (
                        <RunLetterBadge key={l} letter={l} />
                      ))}
                      {typeIcon(type)}
                      <Typography typography="s1" fontWeight="fontWeightMedium">
                        {kept && type !== PROMPT_CONFIG_TYPE.EXTERNAL
                          ? `${item?.name ?? ""}${item?.versionLabel ? ` ${item.versionLabel}` : ""}`
                          : runDisplayName(item)}
                      </Typography>
                      <Typography typography="s2" color="text.secondary">
                        {type === PROMPT_CONFIG_TYPE.EXTERNAL
                          ? "Your code receives every column of each example."
                          : "Keeps the variable mapping it ran with."}
                      </Typography>
                    </Box>
                  );
                const name =
                  type === PROMPT_CONFIG_TYPE.HTTP
                    ? runDisplayName(item)
                    : `${item?.name ?? ""}${item?.versionLabel ? ` ${item.versionLabel}` : ""}`;
                return (
                  <VariableMappingTable
                    key={item._itemId ?? index}
                    control={control}
                    setValue={setValue}
                    fieldPrefix={`promptConfig.${index}`}
                    title={name}
                    letters={letters[index] ?? []}
                    icon={typeIcon(type)}
                    showErrors={showErrors}
                  />
                );
              })}
              {!promptConfig.length ? (
                <Typography typography="s2" color="text.secondary">
                  Add a run on the Configuration step to map its variables.
                </Typography>
              ) : null}
            </Stack>
          </Section>
        ) : null}
        <ReadsOnSampleTable
          control={control}
          sampleRow={sampleRow}
          sampleRowLabel={sampleRowLabel}
          datasetId={datasetId}
          letters={letters}
        />
      </Stack>
    </ExperimentFormContext.Provider>
  );
};

EnhancedDataStep.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  isEditing: PropTypes.bool,
  showErrors: PropTypes.bool,
};

export default function DataStep(props) {
  return isSurfaceShown("exp.new") ? (
    <EnhancedDataStep {...props} />
  ) : (
    <ClassicExperimentData {...props} />
  );
}
