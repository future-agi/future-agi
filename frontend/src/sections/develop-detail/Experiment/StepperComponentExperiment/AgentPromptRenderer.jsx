import { isSurfaceShown } from "src/launch/launchSwitch";
import { Box, Button, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useFieldArray, useWatch } from "react-hook-form";
import { usePromptVersions } from "src/api/develop/prompt";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import { VERSION_STATUS } from "src/sections/agent-playground/utils/constants";
import NewModelRenderWithParamsTool from "./NewModelRenderWithParamsTool";
import VersionMenuSelect from "./VersionMenuSelect";
import { PROMPT_CONFIG_TYPE } from "../constants";
import { useGetAgentVersions } from "../../../../api/experiment/use-get-agents.js";
import RunLetterBadge from "../RunLetterBadge";
import RunCardShell from "../RunCardShell";
import { escapeModelKey } from "../utils";
import { promptVersionConfiguration } from "../promptVersionConfiguration";
import { useExperimentForm } from "../ExperimentFormContext";
import { toneSx } from "../toneSx";
import {
  mappingChipLabel,
  mappingStatus,
  variablesInMessages,
} from "../variableMapping";
import { requestMappingFocus } from "../mappingFocus";
import ToolModesBlock from "../ToolModesBlock";
import RowLimits from "src/sections/common/RunSettings/RowLimits";
import TryOnOneRow from "../TryOnOneRow";
import { cardRunConfig, unmappedBlockText } from "../tryOnOneRowModel";
import { EXPERIMENT_STEP } from "../constants";
import { INPUT_SYNONYMS } from "src/sections/common/RunSources/runOnRows";
import { useRunExperimentStore } from "../../states";
import { PLAYGROUND_AGENTS_PATH } from "src/sections/agent-playground/agentsTabs";

const sameList = (a = [], b = []) =>
  a.length === b.length && a.every((x, i) => x === b[i]);

/**
 * A Prompt or Agent run card (PRODUCT-SPEC §3.8 step 1). Prompt: version
 * (drafts disabled) and one row per model, each row one run. Agent: graph
 * and version, prefilled from the graph's active version. Variables are
 * listed here and mapped on the Data step.
 */
const AgentPromptRenderer = ({
  prompt,
  onRemove,
  setValue,
  control,
  index,
  getValues,
  watch,
  unregister,
  fieldPrefix = "config",
  type = "prompt",
  letters = [],
  showErrors = false,
  readOnlyTarget = false,
}) => {
  const { nextOrdinal, mappingColumns, datasetId, exampleCount } =
    useExperimentForm();
  const [open, setOpen] = useState(false);
  const isPrompt = type === PROMPT_CONFIG_TYPE.PROMPT;
  const promptVersions = usePromptVersions(isPrompt ? prompt?.promptId : null);
  const agentVersions = useGetAgentVersions(
    isPrompt ? null : prompt?.agentId,
    null,
  );

  const versions = useMemo(() => {
    if (isPrompt)
      return promptVersions.data?.pages?.flatMap((p) => p?.results ?? []) ?? [];
    return (
      agentVersions.data?.pages?.flatMap((p) => p?.result?.versions ?? []) ?? []
    );
  }, [isPrompt, promptVersions.data, agentVersions.data]);

  const versionField = isPrompt ? "promptVersion" : "agentVersion";
  const selectedVersionId = useWatch({
    control,
    name: `${fieldPrefix}.${versionField}`,
  });
  const isDraftVersion = (v) =>
    isPrompt ? Boolean(v?.is_draft) : v?.status === VERSION_STATUS.DRAFT;
  const labelOf = (v) =>
    isPrompt ? v?.template_version ?? "" : `v${v?.version_number}`;
  const selected = versions.find((v) => v?.id === selectedVersionId);
  const versionConfiguration = useMemo(
    () => promptVersionConfiguration(selected?.prompt_config_snapshot),
    [selected],
  );
  // v2.1 g4 (card:prompt): a deep link's version that this prompt or agent
  // does not have is said, not silently swapped for the default.
  const wantedVersion = prompt?.wantedVersion;
  const wantedMissing =
    wantedVersion != null &&
    versions.length > 0 &&
    !versions.some(
      (v) =>
        v?.id === wantedVersion ||
        String(labelOf(v)).replace(/^v/i, "") ===
          String(wantedVersion).replace(/^v/i, ""),
    );

  // Default: the prompt's default version, else the newest saved one. Never
  // a draft (it cannot run).
  useEffect(() => {
    if (!versions.length || readOnlyTarget) return;
    if (selected && !isDraftVersion(selected)) return;
    if (selected && isDraftVersion(selected) && selectedVersionId) return;
    const saved = versions.filter((v) => !isDraftVersion(v));
    // R4 P5: a deep link's `prompt_version=1` picks V1.
    const wanted = prompt?.wantedVersion;
    const wantedPick =
      wanted != null
        ? saved.find(
            (v) =>
              String(v?.template_version ?? "").replace(/^v/i, "") ===
                String(wanted).replace(/^v/i, "") || v?.id === wanted,
          )
        : null;
    const pick =
      wantedPick ||
      (isPrompt && saved.find((v) => v?.is_default)) ||
      saved[0] ||
      null;
    if (pick) setValue(`${fieldPrefix}.${versionField}`, pick.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versions, selectedVersionId]);

  // Mirror what the steps need: the version label, draft flag and the
  // run's variables (mapped on the Data step).
  useEffect(() => {
    if (!selected) return;
    // IT-R4 G1 P1: the `{{…}}` names the prompt's messages use, so a
    // prompt edited in this session maps what it uses (with the saved
    // variable_names).
    const saved = selected.variable_names ?? {};
    const variables = isPrompt
      ? [
          ...new Set([
            ...(Array.isArray(saved) ? saved : Object.keys(saved)),
            ...variablesInMessages(
              selected.prompt_config_snapshot?.messages ?? [],
            ),
          ]),
        ]
      : // v2.1 (knob:prompt:variables HIDE): an agent takes each row's own
        // message; its global variables are not mapped from columns.
        [];
    const current = getValues(fieldPrefix) ?? {};
    if (current.versionLabel !== labelOf(selected))
      setValue(`${fieldPrefix}.versionLabel`, labelOf(selected));
    if (Boolean(current.isDraft) !== isDraftVersion(selected))
      setValue(`${fieldPrefix}.isDraft`, isDraftVersion(selected));
    if (!sameList(current.variables ?? [], variables))
      setValue(`${fieldPrefix}.variables`, variables);
    // W1-5: the version's messages, so the Data step can show the prompt
    // this run sends on the sample row (not sent: the schema drops it).
    const messages = isPrompt
      ? selected.prompt_config_snapshot?.messages ?? []
      : [];
    if (
      JSON.stringify(current.templateMessages ?? []) !==
      JSON.stringify(messages)
    )
      setValue(`${fieldPrefix}.templateMessages`, messages);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const { fields, remove, replace } = useFieldArray({
    control,
    name: `${fieldPrefix}.model`,
  });
  const watchModels = useWatch({ control, name: `${fieldPrefix}.model` });
  const variables = useWatch({ control, name: `${fieldPrefix}.variables` });
  const mapping = useWatch({ control, name: `${fieldPrefix}.variableMapping` });
  const status = mappingStatus(variables ?? [], mapping ?? {}, mappingColumns);

  const versionOptions = versions.map((v) => {
    const draft = isDraftVersion(v);
    return {
      value: v.id,
      label: labelOf(v),
      chip: draft ? "Draft" : isPrompt && v.is_default ? "Default" : undefined,
      hint: isPrompt ? v.commit_message || undefined : v.commit_message,
      disabled: draft,
      disabledHint: isPrompt
        ? "Save it in Prompts to use it"
        : "Publish it in Agent Playground to use it",
    };
  });

  const models = (watchModels ?? []).filter((m) => m?.value);
  // The card as it is now, for `Try on one row` (I1 quick test config).
  const card = useWatch({ control, name: fieldPrefix });
  const evalCount = useWatch({ control, name: "userEvalMetrics" })?.length ?? 0;
  const tryConfig = useMemo(
    () => cardRunConfig({ ...(card ?? {}), targetType: type }),
    [card, type],
  );
  const cardError = !showErrors
    ? null
    : selected && isDraftVersion(selected)
      ? `${labelOf(selected)} is a draft. Pick a saved version.`
      : isPrompt && !models.length
        ? "Add a model"
        : null;

  // L3 G8 P5: Open in Prompts opens the version picked on this card.
  const openHref = isPrompt
    ? `/dashboard/workbench/create/${prompt?.promptId}?tab=Playground${selected ? `&version=${encodeURIComponent(labelOf(selected))}` : ""}`
    : `/dashboard/agents/playground/${prompt?.agentId}/build${selectedVersionId ? `?version=${encodeURIComponent(selectedVersionId)}` : ""}`;
  // IT-R2 G1 P10: Try on one row names the real blocker with its action.
  const savedVersions = versions.filter((v) => !isDraftVersion(v));
  const onlyDrafts =
    isPrompt &&
    !promptVersions.isPending &&
    versions.length > 0 &&
    !savedVersions.length;
  const goMap = () => {
    requestMappingFocus(status.unmapped?.[0]);
    useRunExperimentStore.getState().setCurrentStep(EXPERIMENT_STEP.DATA);
  };
  // IT-R5 G1 S1: one obvious column for the one unmapped variable maps in
  // one click: a free text column named like the variable, else the only
  // free text column, else the one named like an input.
  const used = new Set(Object.values(mapping ?? {}).filter(Boolean));
  const textCols = (mappingColumns ?? []).filter(
    (c) => (!c.data_type || c.data_type === "text") && !used.has(c.id),
  );
  const want = String(status.unmapped?.[0] ?? "").toLowerCase();
  const named = (c) => String(c.name ?? "").toLowerCase();
  const fixCol =
    status.unmapped?.length === 1 && textCols.length
      ? textCols.find(
          (c) =>
            named(c) === want ||
            named(c).includes(want) ||
            want.includes(named(c)),
        ) ??
        (textCols.length === 1 ? textCols[0] : null) ??
        textCols.find((c) =>
          [...INPUT_SYNONYMS, "user_request", "request"].includes(named(c)),
        ) ??
        null
      : null;
  const useFixCol = () =>
    setValue(
      `${fieldPrefix}.variableMapping`,
      { ...(mapping ?? {}), [status.unmapped[0]]: fixCol.id },
      { shouldDirty: true },
    );
  const tryBlocked =
    isPrompt && (onlyDrafts || (selected && isDraftVersion(selected)))
      ? {
          reason: `This prompt is a draft. Save it as V${savedVersions.length + 1} in Prompts`,
          action: "Open in Prompts",
          href: openHref,
        }
      : isPrompt && !models.length
        ? {
            reason: "Pick a model",
            action: "Pick",
            onAction: () => setOpen(true),
          }
        : !selectedVersionId && savedVersions.length
          ? { reason: "Pick a version (top right of this card)" }
          : status.unmapped?.length
            ? {
                reason: unmappedBlockText(
                  status.unmapped.map((v) => `{{${v}}}`),
                ),
                action: "Go to Data",
                onAction: goMap,
                soft: true,
                fix: fixCol
                  ? { label: `Use ${fixCol.name}`, onFix: useFixCol }
                  : null,
              }
            : null;

  return (
    <RunCardShell
      icon={
        <SvgColor
          src={
            isPrompt
              ? "/assets/icons/navbar/ic_prompt.svg"
              : "/assets/icons/navbar/ic_agents.svg"
          }
          sx={{ width: 16, height: 16, color: "text.secondary" }}
        />
      }
      letters={isSurfaceShown("exp.new") && !isPrompt ? letters : []}
      title={prompt?.name}
      typeLabel={isPrompt ? "Prompt" : "Agent"}
      error={cardError}
      onRemove={onRemove}
      headerExtra={
        <VersionMenuSelect
          value={selectedVersionId}
          options={versionOptions}
          isPending={
            isPrompt ? promptVersions.isPending : agentVersions.isPending
          }
          onChange={(v) => setValue(`${fieldPrefix}.${versionField}`, v)}
          placeholder="Version"
        />
      }
      actions={
        isSurfaceShown("exp.new") && (
          <Button
            size="small"
            href={openHref}
            target="_blank"
            rel="noopener noreferrer"
            endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
            sx={{ color: "text.secondary", typography: "s2", px: 1 }}
          >
            {isPrompt ? "Open in Prompts" : "Open in Agent Playground"}
          </Button>
        )
      }
    >
      {wantedMissing ? (
        <Typography
          typography="s3"
          color="warning.dark"
          data-testid="wanted-version-missing"
        >
          {`The link asked for version ${String(wantedVersion).replace(/^v/i, isPrompt ? "V" : "v")}, which ${prompt?.name ?? "this " + (isPrompt ? "prompt" : "agent")} does not have. Showing ${selected ? labelOf(selected) : "its default version"}; pick another above.`}
        </Typography>
      ) : null}
      {isPrompt ? (
        <Stack spacing={0.75}>
          {fields.map((field, modelIdx) => {
            const letter = letters[models.indexOf(watchModels?.[modelIdx])];
            return (
              <NewModelRenderWithParamsTool
                key={field.id}
                selectedModel={watchModels?.[modelIdx]}
                modelIndex={modelIdx}
                index={index}
                control={control}
                setValue={setValue}
                getValues={getValues}
                useWatch={watch}
                modelParamsFieldName={`${fieldPrefix}.modelParams.${escapeModelKey(watchModels?.[modelIdx]?.value)}`}
                modelType="llm"
                fallbackParams={versionConfiguration}
                fallbackVersionId={selectedVersionId}
                fallbackParamsNote={`From ${selected?.name ?? "this version"}; change it in Prompts`}
                voiceFieldName={`${fieldPrefix}.model.${modelIdx}.voices`}
                fieldPrefix={fieldPrefix}
                deleteIcon="/assets/icons/ic_close.svg"
                leading={
                  isSurfaceShown("exp.new") && letter ? (
                    <RunLetterBadge
                      letter={letter}
                      ordinal={nextOrdinal ?? undefined}
                    />
                  ) : null
                }
                onRemove={
                  fields.length === 1
                    ? null
                    : () => {
                        remove(modelIdx);
                        unregister(
                          `${fieldPrefix}.modelParams.${escapeModelKey(watchModels?.[modelIdx]?.value)}`,
                        );
                      }
                }
              />
            );
          })}
          <Box>
            <CustomModelDropdownControl
              fieldName="model"
              fieldPrefix={fieldPrefix}
              modelObjectKey={null}
              multiple
              control={control}
              openSelectModel={open}
              setOpenSelectModel={setOpen}
              showButtons
              extraParams={{ model_type: "llm" }}
              customTrigger={
                <Button
                  size="small"
                  variant="text"
                  startIcon={<Iconify icon="mdi:plus" width={16} />}
                  onClick={() => setOpen(true)}
                  sx={{ px: 1 }}
                >
                  Add model
                </Button>
              }
              onChange={(e) => replace(e.target.value || [])}
            />
          </Box>
          {isSurfaceShown("exp.new") && (
            <ToolModesBlock
              control={control}
              setValue={setValue}
              getValues={getValues}
              fieldPrefix={fieldPrefix}
              version={selected}
              promptName={prompt?.name}
              rowCount={
                Number.isFinite(exampleCount) ? exampleCount : undefined
              }
            />
          )}
          {isSurfaceShown("exp.new") && (
            <Stack direction="row" alignItems="center" gap={0.5}>
              <Typography typography="s3" color="text.disabled">
                Need several steps? Build them as an agent graph:
              </Typography>
              <Button
                size="small"
                href={PLAYGROUND_AGENTS_PATH}
                target="_blank"
                rel="noopener noreferrer"
                endIcon={<Iconify icon="mdi:arrow-top-right" width={12} />}
                sx={{
                  typography: "s3",
                  px: 0.5,
                  py: 0,
                  minWidth: 0,
                  "& .MuiButton-endIcon": { ml: 0.25 },
                }}
              >
                Open Agent Playground
              </Button>
            </Stack>
          )}
        </Stack>
      ) : null}
      {isSurfaceShown("exp.new") && (
        <RowLimits control={control} fieldPrefix={fieldPrefix} />
      )}

      {isSurfaceShown("exp.new") && (variables ?? []).length > 0 ? (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            flexWrap: "wrap",
            typography: "s2",
            color: "text.secondary",
          }}
        >
          <Iconify icon="mdi:code-braces" width={14} />
          <Typography typography="s2" color="text.secondary">
            {(variables ?? []).join(", ")}
          </Typography>
          <Box
            component="span"
            sx={{
              typography: "s3",
              px: 0.75,
              py: 0.125,
              borderRadius: 0.5,
              ...toneSx(status.mapped === status.total ? "pass" : "warn"),
            }}
          >
            {mappingChipLabel(status)}
          </Box>
          <Typography typography="s3" color="text.disabled">
            Map on the Data step
          </Typography>
        </Box>
      ) : null}

      {isSurfaceShown("exp.new") && !readOnlyTarget ? (
        <Box
          sx={{
            pt: 1,
            mt: 0.25,
            borderTop: "1px dashed",
            borderColor: "divider",
          }}
        >
          <TryOnOneRow
            datasetId={datasetId}
            config={tryConfig}
            kind={isPrompt ? "prompt" : "agent"}
            hasEvals={evalCount > 0}
            unmapped={status.unmapped}
            onMapNow={goMap}
            blocked={tryBlocked}
          />
        </Box>
      ) : null}
    </RunCardShell>
  );
};

export default AgentPromptRenderer;

AgentPromptRenderer.propTypes = {
  prompt: PropTypes.object,
  onRemove: PropTypes.func,
  setValue: PropTypes.func,
  control: PropTypes.object,
  index: PropTypes.number,
  getValues: PropTypes.func,
  watch: PropTypes.func,
  unregister: PropTypes.func,
  fieldPrefix: PropTypes.string,
  type: PropTypes.string,
  letters: PropTypes.arrayOf(PropTypes.string),
  showErrors: PropTypes.bool,
  readOnlyTarget: PropTypes.bool,
};
