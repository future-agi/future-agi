import { isSurfaceShown } from "src/launch/launchSwitch";
import ClassicConfigureLLM, { promptCard } from "../ClassicConfigureLLM";
import { Box, Button, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { getRandomId } from "src/utils/utils";
import CustomTooltip from "src/components/tooltip";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import AgentPromptRenderer from "./AgentPromptRenderer";
import RunTargetPicker from "../RunTargetPicker";
import FirstPromptComposer from "../FirstPromptComposer";
import ExternalRunCard from "../ExternalRunCard";
import HarnessRunCard from "../HarnessRunCard";
import LinkedTracesCard from "../LinkedTracesCard";
import HttpRunForm from "../HttpRunForm";
import A2aRunForm from "../A2aRunForm";
import McpToolRunForm from "../McpToolRunForm";
import { MAX_RUNS_PER_EXPERIMENT, PROMPT_CONFIG_TYPE } from "../constants";
import { useExperimentForm } from "../ExperimentFormContext";
import { flattenRuns, lettersByConfig, targetTypeOf } from "../runPlan";
import {
  newR4Card,
  newRunCard,
  notCopiedLine,
  withoutCard,
} from "../experimentFormValues";
import R4RunCard from "../R4RunCard";
import EndpointLimitsRow from "../EndpointLimitsRow";
import { useReservedIds } from "../reservedIds";
import SourcePicker from "src/sections/common/RunSources/SourcePicker";
import { usePickerUi } from "src/sections/common/RunSources/pickerUi";
import { variantLetter } from "src/sections/common/Runs/runLabel";
import { useRunConfigs } from "src/api/develop/r2";
import {
  isOffered,
  recommendedFromRuns,
  sourceAvailable,
} from "src/sections/common/RunSources/catalog";
import { useRunSourceAvailability } from "src/sections/common/RunSources/useRunSourceAvailability";
import { configurationIssues } from "../stepIssues";
import { useRunExperimentStoreShallow } from "../../states";
import { usePromptExecutions } from "src/api/develop/prompt";

const agentCard = (a) => ({
  _itemId: getRandomId(),
  experimentType: "llm",
  targetType: PROMPT_CONFIG_TYPE.AGENT,
  promptId: null,
  promptVersion: null,
  agentId: a?.id ?? "",
  // GraphListSerializer sends active_version_id (not the graph id).
  agentVersion: a?.active_version_id ?? null,
  name: a?.name,
  outputFormat: "string",
  isDraft: false,
  variables: [],
  variableMapping: {},
});

/**
 * Runs of a text experiment (PRODUCT-SPEC §3.8 step 1, R2-SPEC §5.3.1).
 * `+ Add run` is the only place a target is chosen; its menu is grouped
 * `Run in Future AGI` / `Run on your side` (task suites put the harness
 * first). Up to 12 runs; each shows the letter it will run as. `Start from`
 * (top) copies the run cards of an earlier experiment.
 */

const EnhancedConfigureStepLLM = ({
  control,
  setValue,
  getValues,
  watch,
  unregister,
  showErrors = false,
  failedNext = 0,
  isEditing = false,
}) => {
  const promptConfig = useWatch({ control, name: "promptConfig" }) ?? [];
  const trials = useWatch({ control, name: "trials" });
  // dataset integration FE-0: Start from / Duplicate & edit name the runs they skipped.
  const notCopied = useWatch({ control, name: "notCopied" });
  const notCopiedLabels = useWatch({ control, name: "notCopiedLabels" });
  const skippedLine = notCopiedLine(notCopied, notCopiedLabels);
  const { datasetId } = useExperimentForm();
  const { kind: r2Kind, dataset: datasetEntry } = useDatasetKind(datasetId);
  // R4: `scenarios` (voice) is not an R2 kind; read the raw kind too.
  const datasetKind = datasetEntry?.kind ?? r2Kind;
  const { firstRunType, prefill } = useRunExperimentStoreShallow((s) => ({
    firstRunType: s.firstRunType,
    prefill: s.prefill,
  }));
  // P5 (pt-4): `source=prompt&prompt_id=&prompt_version=` arrives with that
  // prompt version selected and its variables mapped.
  const wantPrompt =
    !isEditing && prefill?.prompt_id ? prefill.prompt_id : null;
  const promptList = usePromptExecutions(Boolean(wantPrompt), "", "chat");
  const promptAdded = useRef(false);
  useEffect(() => {
    if (!wantPrompt || promptAdded.current) return;
    if ((getValues("promptConfig") ?? []).length) return;
    const found = (promptList.data?.pages ?? [])
      .flatMap((p) => p?.results ?? [])
      .find((p) => p?.id === wantPrompt);
    if (!found) return;
    promptAdded.current = true;
    append({
      ...promptCard(found),
      wantedVersion: prefill?.prompt_version ?? null,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantPrompt, promptList.data]);
  const [picker, setPicker] = useState(null); // {anchorEl, type}
  // JRN-EXP-NO-PROMPT-DEADEND: with no saved prompt, the Prompt picker's
  // `Write a prompt` opens a composer here; its save adds the run.
  const [writing, setWriting] = useState(false);
  // A failed Next (⌘↵ works with a picker open) closes the pickers, so the
  // `Add at least one run` error and the red cards are never behind them.
  useEffect(() => {
    if (!failedNext) return;
    setPicker(null);
  }, [failedNext]);
  const runs = flattenRuns(promptConfig);
  const letters = lettersByConfig(promptConfig);
  const full = runs.length >= MAX_RUNS_PER_EXPERIMENT;

  const append = (card) =>
    setValue("promptConfig", [...(getValues("promptConfig") ?? []), card], {
      shouldDirty: true,
    });

  // R4 (lane Ra): the run-source picker replaces the R3 cards and the
  // `+ Add run` menu. Prompt / Agent graph / HTTP / traces / upload still
  // add R3's cards; code, harness and voice come back as Level 3 drafts.
  // The server's report decides which of those kinds can be added here (read
  // through a ref: the door below adds its kind from a timer).
  const availability = useRunSourceAvailability();
  const availabilityRef = useRef(availability);
  availabilityRef.current = availability;
  const ctx = useExperimentForm();
  const { data: reservedIds } = useReservedIds(datasetId, {
    enabled: !isEditing,
  });
  // IT-R2 G1 P5: `Recommended` from the dataset's baseline run (context).
  const { data: runConfigs } = useRunConfigs(datasetId, {
    enabled: !isEditing,
  });
  // options-truth L1 B (picker:recommended): the door decides. A re-test
  // (rows preselected) recommends the baseline's kind, a prompt-version door
  // recommends Prompt; otherwise the dataset kind's first card does.
  const recommended = useMemo(() => {
    if (isEditing) return null;
    if (Array.isArray(prefill?.row_ids) && prefill.row_ids.length) {
      const id = runConfigs?.baseline?.run_id;
      const base = (runConfigs?.results ?? []).find(
        (r) => (r.run_id ?? r.id) === id,
      );
      return base ? recommendedFromRuns(runConfigs, base) : null;
    }
    // L7c g2 (picker:recommended): the prompt door already added the
    // prompt's card, so step 1 never recommends adding it again.
    return null;
  }, [isEditing, prefill, runConfigs]);
  // IT-R2 G1 P5: a re-test (rows preselected) starts with the baseline's
  // agent: `Re-test with Support Agent v3 (as in #1B)` · `Use a different
  // agent` (one click to the same run; the cards stay for anything else).
  const [retestDismissed, setRetestDismissed] = useState(false);
  const retest = useMemo(() => {
    if (
      isEditing ||
      !Array.isArray(prefill?.row_ids) ||
      !prefill.row_ids.length
    )
      return null;
    const id = runConfigs?.baseline?.run_id;
    const base = (runConfigs?.results ?? []).find((r) => r.run_id === id);
    const t = base?.target;
    if (!base || !t?.graph_id || !t?.version_id) return null;
    // Not offered where the server says Agent graph cannot run.
    if (!sourceAvailable(PROMPT_CONFIG_TYPE.AGENT, availability)) return null;
    return {
      label: base.label,
      name: base.name ?? `${t.graph_name} ${t.version_name ?? ""}`.trim(),
      card: {
        id: t.graph_id,
        active_version_id: t.version_id,
        name: t.graph_name ?? base.name,
      },
    };
  }, [isEditing, prefill, runConfigs, availability]);
  const [pickerOpen, setPickerOpen] = useState(false);
  // IT-R2 G1 P4: after the first run, a hint names the baseline run to
  // compare (voice agents and harnesses are removed sources, the capability contract).
  // L7c g2 (picker:add-another-hint): `{text, card}`: the baseline run the
  // hint names and the card that adds it (voice / harness are absent).
  const compareHint = useMemo(() => {
    const runs1 = flattenRuns(promptConfig);
    if (runs1.length !== 1) return null;
    const base = runConfigs?.baseline;
    const baseRun = (runConfigs?.results ?? []).find(
      (r) => r.run_id === base?.run_id,
    );
    // Never suggest the run that is already here.
    const only = promptConfig[0] ?? {};
    const same =
      (baseRun?.target?.version_id &&
        baseRun.target.version_id === only.agentVersion) ||
      (baseRun?.target?.prompt_id &&
        baseRun.target.prompt_id === only.promptId);
    if (!(base?.label && baseRun && !same)) return null;
    const t = baseRun.target ?? {};
    const card =
      t.graph_id && t.version_id
        ? agentCard({
            id: t.graph_id,
            active_version_id: t.version_id,
            name: t.graph_name ?? baseRun.name,
          })
        : t.prompt_id
          ? {
              ...promptCard({
                id: t.prompt_id,
                name: t.prompt_name ?? baseRun.name,
                model: t.model,
              }),
              wantedVersion: t.prompt_version_name ?? null,
            }
          : null;
    // Never suggest a run of a kind the server cannot run here.
    if (card && !sourceAvailable(targetTypeOf(card), availability)) return null;
    return { text: `${base.label} ${baseRun.name ?? ""}`.trim(), card };
  }, [promptConfig, runConfigs, availability]);
  const [editIndex, setEditIndex] = useState(null);
  // L5 G3: a picker card carries its own tries (`r4.limits.tries`): Edit
  // opens on that card's number, a new card on the dataset default (1),
  // never the last card's value or the experiment's Trials.
  const [cardTries, setCardTries] = useState(null);
  useEffect(() => {
    setCardTries(null);
  }, [editIndex, pickerOpen]);
  const editingTries =
    editIndex != null
      ? Number(getValues(`promptConfig.${editIndex}`)?.r4?.limits?.tries) ||
        null
      : null;
  const pickerTries = cardTries ?? editingTries ?? 1;
  const [openedAtMs] = useState(() => Date.now());
  const pickerLevel = usePickerUi((st) => st.level);
  const showPicker = !promptConfig.length || pickerOpen || editIndex != null;
  const draftLetter =
    editIndex != null
      ? letters[editIndex]?.[0] ?? variantLetter(runs.length)
      : variantLetter(runs.length);
  const ordinal = reservedIds?.ordinal ?? ctx.nextOrdinal;
  const runLabel = ordinal != null ? `#${ordinal}${draftLetter}` : draftLetter;
  const reserved = {
    experimentId: reservedIds?.experimentId ?? null,
    runId: reservedIds?.runIds?.[draftLetter] ?? null,
  };
  const rowNoun = datasetKind === "task_suite" ? "task" : "row";
  const pickerDataset = {
    id: datasetId,
    name: ctx.datasetName,
    environment: datasetEntry?.environment ?? null,
    inputColumn: ctx.inputColumn,
    trials: Number(trials) || 1,
    firstRowId: ctx.rows?.[0]?.row_id ?? null,
    keys: ctx.prototypeKeys ?? null,
    // IT-R1 G1 P1: `Download sample_rows.jsonl (3 rows)`.
    sample: { rows: (ctx.rows ?? []).slice(0, 3), columns: ctx.columns ?? [] },
  };
  const addR4 = (draft) => {
    const card = newR4Card(draft);
    if (editIndex != null) {
      const prev = getValues(`promptConfig.${editIndex}`);
      setValue(
        `promptConfig.${editIndex}`,
        { ...card, _itemId: prev?._itemId ?? card._itemId },
        { shouldDirty: true },
      );
      setEditIndex(null);
    } else append(card);
    // IT-R2 G1 P5 (S6): no `Added #4A` toast; the run list shows the card.
    setPickerOpen(false);
  };

  const choose = (type, anchorEl) => {
    // Any door (the picker, a kinds menu, a key) asking for a kind the server
    // cannot run here adds nothing; step 1 shows the server's reason.
    if (!sourceAvailable(type, availabilityRef.current)) return;
    setPickerOpen(false);
    if (type === PROMPT_CONFIG_TYPE.PROMPT || type === PROMPT_CONFIG_TYPE.AGENT)
      setPicker({ anchorEl, type });
    else append(newRunCard(type));
  };

  // initiateCreateMode({firstRunType}) opens the drawer on that target.
  const openedFirst = useRef(false);
  useEffect(() => {
    if (openedFirst.current || !firstRunType || promptConfig.length) return;
    openedFirst.current = true;
    const t = setTimeout(
      () =>
        choose(
          firstRunType,
          document.querySelector(
            `[data-testid="source-card-${firstRunType}"]`,
          ) ?? undefined,
        ),
      50,
    );
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstRunType]);

  // options-truth L3 G6 (harness-r2): remove by the card's own id, so the
  // cards that stay keep their kind and summary.
  const removeAt = (index) => {
    const cards = getValues("promptConfig") ?? [];
    // L7b G2: exactly one card goes (never every card sharing an id).
    setValue("promptConfig", withoutCard(cards, index), { shouldDirty: true });
    if (editIndex === index) setEditIndex(null);
  };

  const issuesOf = (item) =>
    configurationIssues({
      name: "x",
      experimentType: "llm",
      promptConfig: [item],
    })[0];

  // A2-16: the `Runs` heading always shows, right above the runs it
  // lists; before the first run it sits under the cards that add one.
  const runsHeading = (
    <Box
      data-testid="runs-heading"
      sx={{ display: "flex", alignItems: "center", gap: 1, minHeight: 30 }}
    >
      <Typography typography="s1_2" fontWeight="fontWeightMedium">
        Runs
      </Typography>
      <Typography typography="s2" color="text.secondary">
        {runs.length
          ? `${runs.length} of ${MAX_RUNS_PER_EXPERIMENT}`
          : "· none yet"}
      </Typography>
      <Box sx={{ flex: 1 }} />
      {promptConfig.length ? (
        <CustomTooltip
          show={full}
          title={`${MAX_RUNS_PER_EXPERIMENT} is the limit per experiment. Split into two experiments and compare them.`}
          placement="top"
          size="small"
          type="black"
        >
          <span>
            <Button
              size="small"
              variant="outlined"
              color="primary"
              disabled={full}
              startIcon={<Iconify icon="solar:add-linear" width={16} />}
              onClick={() => {
                setEditIndex(null);
                setPickerOpen((v) => !v);
              }}
              aria-expanded={pickerOpen}
            >
              {pickerOpen ? "Close" : "Add run"}
            </Button>
          </span>
        </CustomTooltip>
      ) : null}
    </Box>
  );

  return (
    <Stack spacing={1.25}>
      {promptConfig.length ? runsHeading : null}
      {skippedLine ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="runs-not-copied"
        >
          {skippedLine}
        </Typography>
      ) : null}
      {retest &&
      !retestDismissed &&
      !promptConfig.length &&
      pickerLevel <= 1 ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          data-testid="retest-strip"
          sx={{
            px: 1.25,
            py: 1,
            border: "1px solid",
            borderColor: "primary.main",
            borderRadius: 0.75,
            bgcolor: "status.discovery.bg",
          }}
        >
          <Typography typography="s2" sx={{ flex: 1, minWidth: 0 }}>
            {`Re-test these ${prefill.row_ids.length} rows with the agent your baseline ran.`}
          </Typography>
          <Button
            size="small"
            variant="contained"
            onClick={() => append(agentCard(retest.card))}
            data-testid="retest-same"
            sx={{ flexShrink: 0 }}
          >
            {`Re-test with ${retest.name} (as in ${retest.label})`}
          </Button>
          <Button
            size="small"
            onClick={() => setRetestDismissed(true)}
            sx={{ flexShrink: 0, color: "text.secondary" }}
          >
            Use a different agent
          </Button>
        </Stack>
      ) : null}
      {/* options-truth L3 G8 P6: Edit adds runs through the same picker;
          a new card has no run id, so the save lists it as New. */}
      {showPicker ? (
        <Box
          sx={
            promptConfig.length
              ? {
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "4px",
                  p: 1.25,
                }
              : undefined
          }
        >
          <SourcePicker
            dataset={pickerDataset}
            datasetKind={datasetKind}
            rowCount={ctx.exampleCount ?? undefined}
            rowNoun={rowNoun}
            runLabel={runLabel}
            reserved={reserved}
            openedAtMs={openedAtMs}
            onAddCard={(type, el) => choose(type, el)}
            onAddR4={addR4}
            editing={
              editIndex != null
                ? getValues(`promptConfig.${editIndex}`)?.r4 ?? null
                : null
            }
            onCancelEdit={() => setEditIndex(null)}
            tries={pickerTries}
            onTries={(n) => setCardTries(Number(n) || 1)}
            recommended={recommended}
          />
        </Box>
      ) : null}
      {writing ? (
        <FirstPromptComposer
          onCancel={() => setWriting(false)}
          onSaved={(saved) => {
            append(promptCard(saved));
            setWriting(false);
            setPickerOpen(false);
          }}
        />
      ) : null}
      {!promptConfig.length && pickerLevel <= 1 ? runsHeading : null}
      {!promptConfig.length && showErrors ? (
        <Typography typography="s2" color="error.main">
          Add at least one run
        </Typography>
      ) : null}

      {flattenRuns(promptConfig).length === 1 && !showPicker && !isEditing ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          data-testid="add-another-hint"
          sx={{ order: 10 }}
        >
          <Typography typography="s2" color="text.secondary">
            {`Add another run to compare${compareHint ? ` — e.g. ${compareHint.text}` : ""}`}
          </Typography>
          <Button
            size="small"
            data-testid="add-another-run"
            onClick={() =>
              compareHint?.card ? append(compareHint.card) : setPickerOpen(true)
            }
            startIcon={<Iconify icon="solar:add-linear" width={14} />}
            sx={{ typography: "s2" }}
          >
            {compareHint?.card ? `Add ${compareHint.text}` : "Add run"}
          </Button>
        </Stack>
      ) : null}
      <Stack spacing={1}>
        {promptConfig.map((item, index) => {
          const type = targetTypeOf(item);
          const fieldPrefix = `promptConfig.${index}`;
          // L7c g2 (legacy:r4-run-card-kept): the index is part of the key,
          // so a card whose index changes (a card above it was removed)
          // remounts and its fields read their new prefix, not the old one.
          const key = `${item?._itemId || type}-${index}`;
          const common = {
            control,
            fieldPrefix,
            letters: letters[index] ?? [],
            onRemove: () => removeAt(index),
            showErrors,
          };
          // R4 (lane Ra): runs added from the run-source picker.
          if (item?.r4)
            return (
              <R4RunCard
                key={key}
                {...common}
                error={issuesOf(item)}
                onEdit={
                  isOffered(item.r4.entryId)
                    ? () => {
                        setPickerOpen(false);
                        setEditIndex(index);
                      }
                    : undefined
                }
              />
            );
          // R2 (P3): My code uses the From code card (L3a extends it by
          // type); Agent harness and Traces I already have get their own.
          if (type === PROMPT_CONFIG_TYPE.HARNESS)
            return (
              <HarnessRunCard
                key={key}
                {...common}
                index={index}
                setValue={setValue}
                trials={Number(trials) || 1}
                error={issuesOf(item)}
              />
            );
          if (type === PROMPT_CONFIG_TYPE.TRACES)
            return (
              <LinkedTracesCard
                key={key}
                {...common}
                index={index}
                setValue={setValue}
                trials={Number(trials) || 1}
                error={issuesOf(item)}
              />
            );
          if (
            type === PROMPT_CONFIG_TYPE.EXTERNAL ||
            type === PROMPT_CONFIG_TYPE.CODE
          )
            return (
              <ExternalRunCard
                key={key}
                {...common}
                trials={Number(trials) || 1}
                error={issuesOf(item)}
              />
            );
          // options-truth L1 G5: Already running › A2A agent / MCP tool.
          // L5 G3: endpoint, A2A and MCP runs carry their own tries and
          // run cost cap (EndpointLimitsRow).
          if (type === PROMPT_CONFIG_TYPE.A2A)
            return (
              <Stack key={key} spacing={0.5}>
                <A2aRunForm {...common} setValue={setValue} />
                <EndpointLimitsRow
                  control={control}
                  fieldPrefix={fieldPrefix}
                />
              </Stack>
            );
          if (type === PROMPT_CONFIG_TYPE.MCP_TOOL)
            return (
              <Stack key={key} spacing={0.5}>
                <McpToolRunForm {...common} setValue={setValue} />
                <EndpointLimitsRow
                  control={control}
                  fieldPrefix={fieldPrefix}
                />
              </Stack>
            );
          if (type === PROMPT_CONFIG_TYPE.HTTP)
            return (
              <Stack key={key} spacing={0.5}>
                <HttpRunForm
                  {...common}
                  setValue={setValue}
                  getValues={getValues}
                  error={issuesOf(item)}
                />
                <EndpointLimitsRow
                  control={control}
                  fieldPrefix={fieldPrefix}
                />
              </Stack>
            );
          return (
            <AgentPromptRenderer
              key={key}
              {...common}
              index={index}
              prompt={item}
              type={type}
              setValue={setValue}
              getValues={getValues}
              watch={watch}
              unregister={unregister}
              readOnlyTarget={isEditing && Boolean(item?.runId)}
            />
          );
        })}
      </Stack>

      <RunTargetPicker
        anchorEl={picker?.anchorEl ?? null}
        type={picker?.type ?? PROMPT_CONFIG_TYPE.PROMPT}
        onClose={() => setPicker(null)}
        onPick={(item) =>
          append(
            picker?.type === PROMPT_CONFIG_TYPE.AGENT
              ? agentCard(item)
              : promptCard(item),
          )
        }
        onWrite={
          isEditing
            ? undefined
            : () => {
                setWriting(true);
                setPickerOpen(false);
              }
        }
      />
    </Stack>
  );
};

export default function ConfigureStepLLM(props) {
  return isSurfaceShown("exp.new") ? (
    <EnhancedConfigureStepLLM {...props} />
  ) : (
    <ClassicConfigureLLM {...props} />
  );
}

EnhancedConfigureStepLLM.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  getValues: PropTypes.func.isRequired,
  watch: PropTypes.func.isRequired,
  unregister: PropTypes.func.isRequired,
  showErrors: PropTypes.bool,
  failedNext: PropTypes.number,
  isEditing: PropTypes.bool,
};
