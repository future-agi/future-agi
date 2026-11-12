import { CLASSIC_EXPERIMENT_COPY } from "src/launch/classicExperimentCopy";
import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useForm, useFormState, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { enqueueSnackbar } from "src/components/snackbar";
import { ConfirmDialog } from "src/components/custom-dialog";
import UxConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { useDebounce } from "src/hooks/use-debounce";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { useGetExperimentDetails } from "src/hooks/useGetExperimentDetails";
import { invalidateDatasetListCache } from "src/utils/cacheUtils";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import {
  useDatasetDerivedVariables,
  useGetJsonColumnSchema,
} from "src/api/develop/develop-detail";
import {
  parseCreateResponse,
  useCreateExperiment,
  useDatasetEvalsForExperiment,
  useExperimentDatasetSplits,
  useExperimentDatasetVersions,
  useExperimentNameCheck,
  useExperimentNameSuggestion,
  useExperimentSampleTable,
  useUpdateExperiment,
  wizardVersionsOf,
} from "src/api/develop/experiment-create";
import { currentRowsChoiceShown } from "./datasetVersionChoice";
import { useRunExperimentStore, useRunExperimentStoreShallow } from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import { MODEL_TYPES } from "../RunPrompt/common";
import HelperText from "../Common/HelperText";
import RunExperimentFormSkeleton from "./RunExperimentFormSkeleton";
import ExperimentSourceGate from "./ExperimentSourceGate";
import { RunTrayDock } from "./RunTray";
import { VOCAB } from "src/sections/common/vocab";
import StepperForExperiment from "./StepperForExperiment";
import ConfigureStepExperimentCreation from "./StepperComponentExperiment/ConfigureStepExperimentCreation";
import DataStep from "./StepperComponentExperiment/DataStep";
import EvaluationStepExperimentCreation from "./StepperComponentExperiment/EvaluationStepExperimentCreation";
import ReviewStep from "./StepperComponentExperiment/ReviewStep";
import WaitingForResultsPanel from "./WaitingForResultsPanel";
import { terminalRunsOf } from "./StartItBlock";
import RunActionConfirmHost from "./RunActionConfirmHost";
import {
  EstimateBreakdown,
  EstimateSummary,
  useEstimateDetails,
} from "./EstimateDetails";
import { SimpleEstimate } from "./CostEstimateLine";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import axios, { endpoints } from "src/utils/axios";
import { MAX_TOAST_MS, notify } from "src/sections/common/ux/toast";
import { MOD_KEY_LABEL } from "src/utils/keymap";
import { trackRuns, useRunTray } from "./runTrayStore";
import { undoReturnPath } from "./undoReturn";
import { START_FROM_PRESET, cardsFromExperiment } from "./StartFromExperiment";
import { launchedTitleOf } from "./launchedTitle";
import { useRunConfigs } from "src/api/develop/r2";
import { useExperimentDetail } from "src/api/develop/runs";
import {
  getDefaultPromptConfigByModelType,
  getExperimentDefaultValue,
  getNewExperimentValidationSchema,
} from "./common";
import { EXPERIMENT_STEP, experimentCreationSteps } from "./constants";
import { ExperimentFormContext, rowLabelOf } from "./ExperimentFormContext";
import {
  editChangeSummary,
  editRunScope,
  emptyExperimentValues,
  evalFromDatasetDefault,
  evalIdsOfExperiment,
  sampleOfValues,
} from "./experimentFormValues";
import { experimentBody, experimentBodyOfValues } from "./experimentBody";
import { scopeReceipt } from "./scopeReceipt";
import {
  hydrateHttpSourceValues,
  httpEditChanges,
  withHttpSourceSettings,
  serverBlockOnStep,
  wireExperimentBody,
  withServerBlocks,
} from "./serverBody";
import {
  editSavePlan,
  ownNamesOf,
  renameFieldErrors,
  runEditSave,
  settingsErrorText,
} from "./editSave";
import {
  refreshAfterRename,
  renameErrorOf,
  renameErrorText,
  renameSuccessText,
} from "src/api/develop/experiment-metadata";
import {
  allRunsExternal,
  executionSummary,
  flattenRuns,
  runCount,
} from "./runPlan";
import { runTriesOf } from "./estimateInputs";
import { sampledCount } from "src/sections/common/Runs/runLedger";
import { editKeptLine } from "src/sections/common/Runs/rerunCopyModel";
import {
  HTTP_RESEND_COPY,
  httpEditSummaryText,
} from "src/sections/common/Runs/httpResendCopy";
import { stepIssues } from "./stepIssues";
import {
  SUBMIT_CHECK_FAILED,
  refusalLine,
  refusalOfBlocks,
  refusalOfErrors,
} from "./submitRefusal";
import { judgesOf } from "./costEstimateModel";
import { autoMapVariables, sameMapping } from "./variableMapping";
import { isPrototypeMode } from "src/utils/prototype";
import { isLaunchBuild, isSurfaceShown } from "src/launch/launchSwitch";
import {
  PICKER_DRAWER_WIDTH,
  discardBody,
  usePickerUi,
} from "src/sections/common/RunSources/pickerUi";
import {
  inputColumnFor,
  parseRunOnRows,
} from "src/sections/common/RunSources/runOnRows";
import { orgKeyPair } from "src/sections/common/RunSources/prototypeKeys";
import { launchedHowLine } from "./waitingModel";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { resolveRowRefs, rowRefsNote } from "./rowRefs";
import { tableRequestFor } from "src/sections/develop-detail/DataTab/Versions/versionedSource";
import { readAllTableRows } from "./scopeRows";
import { useAuthContext } from "src/auth/hooks";
import { STEP1_COPY, editHelpOf } from "src/sections/common/RunSources/catalog";
import { useRunSourceAvailability } from "src/sections/common/RunSources/useRunSourceAvailability";
import { useCreateNameTaken as useSavedName } from "./useCreateAdmission";
import { useRecoveryPrefill } from "./createAdmissionDraft";
import CreateAdmissionNotice from "./CreateAdmissionNotice";

// Drawer deep-link and picker keys (R4-SPEC §6 deep link; lane Ra): kept
// in the URL while the drawer is open (a reload reopens it where it was),
// removed on close.
const DRAWER_URL_KEYS = [
  "new_experiment",
  "source",
  "entry",
  "agent",
  "mode",
  "form",
  "view",
  "src",
  "rows",
  "split",
  "prompt_id",
  "prompt_version",
  "version_id",
];

const USER_ORIGINS = ["others"];
const MAPPABLE_ORIGINS = ["others", "run_prompt"];
const origin = (c) =>
  String(c?.origin_type ?? c?.originType ?? "").toLowerCase();

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

// v2.1 (exp:name): words that never end a short dataset name.
const JOINER =
  /^(?:&|\+|\/|-|–|—|·|:|,|\||and|or|of|the|for|with|to|in|on|a|an|vs)$/i;

/**
 * The dataset's first two words, cut on a word boundary: a joiner in second
 * place takes the word after it (`Multimodal & nested`), and the name never
 * ends with a joiner or punctuation. Pure.
 */
export function datasetShortName(datasetName) {
  const words = String(datasetName ?? "")
    .split(" · ")[0]
    .split(/\s+/)
    .filter(Boolean);
  let n = Math.min(2, words.length);
  while (n < words.length && n < 4 && JOINER.test(words[n - 1])) n += 1;
  const out = words.slice(0, n);
  while (out.length > 1 && JOINER.test(out[out.length - 1])) out.pop();
  return out
    .join(" ")
    .replace(/[\s&+/,:;·|–—-]+$/u, "")
    .trim();
}

/**
 * IT-R2 G1 P4: the suggested name once runs exist, until the user types one:
 * `Support ops · Claude Code vs Codex CLI vs Custom TypeScript · Oct 18`
 * (dataset, every run by what was picked, today). Pure.
 */
export function autoName(promptConfig, { datasetName, now = Date.now() } = {}) {
  // Cards still waiting for a run name or URL do not name the experiment.
  const names = flattenRuns(promptConfig)
    .filter((r) => {
      const item = promptConfig[r.configIndex];
      if (r.targetType === "external") return Boolean(item?.runName?.trim());
      if (r.targetType === "http") return Boolean(item?.http?.url?.trim());
      return true;
    })
    .map((r) => {
      const r4 = promptConfig[r.configIndex]?.r4;
      if (!r4) return r.name;
      return r4.kind === "voice"
        ? r4.runName ?? r.name
        : r4.entryName ?? r.name;
    });
  if (!names.length) return null;
  const ds = datasetShortName(datasetName);
  const d = new Date(now);
  const date = `${MONTHS[d.getMonth()]} ${d.getDate()}`;
  return [ds || null, [...new Set(names)].join(" vs "), date]
    .filter(Boolean)
    .join(" · ");
}

// IT-R2 G1 P1: what closing would lose (names + simulated / tried), as a
// stable JSON key so the effect runs only when it changes.
function draftLostOf(values) {
  const cards = values?.promptConfig ?? [];
  const runs = flattenRuns(cards);
  const r4s = runs.map((r) => cards[r.configIndex]?.r4 ?? null);
  return JSON.stringify({
    names: runs.map((r, i) => {
      const r4 = r4s[i];
      if (!r4) return r.name;
      return r4.kind === "voice"
        ? r4.runName ?? r.name
        : r4.entryName ?? r.name;
    }),
    simulated: r4s.some((r4) => Boolean(r4?.arrival)),
    tried: false,
  });
}

const RunExperimentForm = ({
  datasetId,
  mode,
  sourceDetail,
  sourceConfigs = null,
  experimentColumns,
  prefill: linkPrefill,
  setFormIsDirty,
  onDraftSummary,
  onSubmitted,
  onLaunched,
  onDatasetName,
}) => {
  const isEditing = mode === "edit";
  // SRC S1b: Edit of an experiment with saved HTTP sources only saves them.
  const httpEdit = isEditing && Boolean(sourceDetail?.http_configs?.length);
  const enhancementsShown = isSurfaceShown("exp.new");
  const creationSteps = enhancementsShown
    ? experimentCreationSteps
    : experimentCreationSteps.slice(0, EXPERIMENT_STEP.REVIEW);
  // shared-edit(F01): a restored submission is not preselected by its link.
  const prefill = useRecoveryPrefill(datasetId, linkPrefill, isEditing);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { refreshGrid } = useDevelopDetailContext();
  const { currentStep, nextStep, prevStep, setCurrentStep, visitedSteps } =
    useRunExperimentStoreShallow((state) => ({
      currentStep: state.currentStep,
      nextStep: state.nextStep,
      prevStep: state.prevStep,
      setCurrentStep: state.setCurrentStep,
      visitedSteps: state.visitedSteps,
    }));

  useEffect(() => {
    if (!enhancementsShown && currentStep >= EXPERIMENT_STEP.REVIEW)
      setCurrentStep(EXPERIMENT_STEP.EVALUATIONS);
  }, [enhancementsShown, currentStep, setCurrentStep]);

  // ---------------------------------------------------------------- data
  const { data: suggestion } = useExperimentNameSuggestion(datasetId, {
    enabled: !isEditing,
  });
  const {
    data: versionList,
    held: versionsHeld = false,
    isLoading: savedVersionsLoading = false,
    isError: savedVersionsFailed = false,
    refetch: retrySavedVersions,
  } = useExperimentDatasetVersions(datasetId, {
    enabled: enhancementsShown,
    // A launch build's Data step says itself when the list can't be read.
    meta: isLaunchBuild() ? { errorHandled: true } : undefined,
  });
  // A launch build runs the dataset's current rows; its saved versions are
  // counted on the Data step and never become the version a run reads.
  const { versions, savedVersions, runsCurrentRows } =
    wizardVersionsOf(versionList);
  const currentRowsChoice = currentRowsChoiceShown({
    runsCurrentRows,
    versionsHeld,
  });
  const { data: splitData } = useExperimentDatasetSplits(datasetId, {
    enabled: enhancementsShown,
  });
  const { data: jsonSchemas = {} } = useGetJsonColumnSchema(datasetId);
  const { data: derivedVariables = {} } = useDatasetDerivedVariables(datasetId);
  const { data: datasetEvals } = useDatasetEvalsForExperiment(datasetId);
  // options-truth L3 G8 P6: R1 run configs (shared cache with Start from)
  // name the evals an earlier experiment was scored with.
  const { data: runConfigsAll } = useRunConfigs(datasetId, {
    enabled: enhancementsShown,
  });

  // ---------------------------------------------------------------- form
  const initialValues = useMemo(() => {
    if (sourceDetail) {
      // Core HTTP readback is complete without the optional R1 catalog. Use
      // the classic fields alongside it, preserving saved scope and per-run order.
      if (!enhancementsShown || sourceDetail?.http_configs?.length)
        return hydrateHttpSourceValues({
          ...emptyExperimentValues(),
          ...getExperimentDefaultValue(sourceDetail, experimentColumns ?? []),
          ...(mode === "duplicate" ? { name: `${sourceDetail.name ?? ""} (copy)` } : {}),
        }, sourceDetail, { isEditing });
      // options-truth L3 G8 P6: Edit and Duplicate & edit build their cards
      // the way Start from does (each run's R1 config: framework, models,
      // tools, harness, where it runs), so every door shows the same runs.
      const v = cardsFromExperiment(
        sourceDetail,
        Object.fromEntries(
          (sourceConfigs?.results ?? []).map((r) => [r.run_id, r]),
        ),
        { mode: isEditing ? "edit" : "duplicate" },
      );
      if ((sourceDetail.experiment_type ?? "llm") !== "llm")
        return {
          ...v,
          ...getExperimentDefaultValue(sourceDetail, experimentColumns ?? []),
        };
      return v;
    }
    return {
      ...emptyExperimentValues(),
      datasetVersionId: prefill?.version_id ?? null,
      splitIds: prefill?.split_ids ?? [],
      // P5: a door's selected rows arrive preselected.
      rowIds: Array.isArray(prefill?.row_ids) ? prefill.row_ids : [],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const latestNumber = Math.max(0, ...versions.map((v) => v.number));
  const {
    control,
    handleSubmit,
    getValues,
    setValue,
    clearErrors,
    watch,
    unregister,
    setError,
  } = useForm({
    defaultValues: initialValues,
    resolver: (values, ctx, opts) =>
      zodResolver(
        getNewExperimentValidationSchema(
          schemaColumnsRef.current,
          isEditing,
          datasetId,
        ),
      )(values, ctx, opts),
  });
  const { isDirty, errors } = useFormState({ control });
  useEffect(() => setFormIsDirty?.(isDirty), [isDirty, setFormIsDirty]);
  const values = useWatch({ control });
  // CR#26 / IT-R2 G1 P1: the discard confirm names the runs it would lose.
  const lostKey = draftLostOf(values);
  useEffect(
    () => onDraftSummary?.(JSON.parse(lostKey)),
    [lostKey, onDraftSummary],
  );
  const selectedVersion =
    versions.find((v) => v.id === values.datasetVersionId) ??
    versions.find((v) => v.number === latestNumber);
  const isLatest = !selectedVersion || selectedVersion.number === latestNumber;

  // options-truth L1 B (entry:deep-link): `split=smoke` arrives as
  // prefill.split_name; once the splits load, preselect that split's chip.
  const splitNameDone = useRef(false);
  useEffect(() => {
    const name = String(prefill?.split_name ?? "")
      .trim()
      .toLowerCase();
    if (
      !isSurfaceShown("exp.new.splitScope") ||
      !name ||
      splitNameDone.current ||
      isEditing
    )
      return;
    const split = (splitData?.splits ?? []).find(
      (sp) => String(sp.name ?? "").toLowerCase() === name,
    );
    if (!split) return;
    splitNameDone.current = true;
    if (!(getValues("splitIds") ?? []).length)
      setValue("splitIds", [split.id], { shouldDirty: true });
  }, [prefill?.split_name, splitData, isEditing, getValues, setValue]);

  const { data: sample } = useExperimentSampleTable(
    {
      datasetId,
      versionId: selectedVersion?.id,
      versionNumber: selectedVersion?.number,
      isLatest,
      splitIds: values.splitIds ?? [],
    },
    { enabled: Boolean(datasetId) },
  );
  const columns = useMemo(() => sample?.columns ?? [], [sample?.columns]);
  useEffect(() => {
    if (sample?.datasetName) onDatasetName?.(sample.datasetName);
  }, [sample?.datasetName, onDatasetName]);
  const mappingColumns = useMemo(
    () => columns.filter((c) => MAPPABLE_ORIGINS.includes(origin(c))),
    [columns],
  );
  const userColumns = useMemo(
    () => columns.filter((c) => USER_ORIGINS.includes(origin(c))),
    [columns],
  );
  // The zod schema maps {{column}} names in TTS / STT messages to ids.
  const schemaColumnsRef = useRef([]);
  schemaColumnsRef.current = (
    experimentColumns?.length
      ? experimentColumns
      : mappingColumns.map((c) => ({
          field: c.id,
          headerName: c.name,
          dataType: c.data_type,
        }))
  ).map((c) => ({
    ...c,
    field: c.field ?? c.id,
    headerName: c.headerName ?? c.name,
  }));

  const rows = useMemo(() => sample?.rows ?? [], [sample?.rows]);
  const [sampleRowId, setSampleRowId] = useState(prefill?.row_ids?.[0] ?? null);
  // options-truth L3 G8 P1 (entry:deep-link): a deep link's `rows=` names
  // rows by id or label; once the rows load, keep the ones that exist and
  // say which values named none ("2 of 3 rows found").
  const [rowRefsRes, setRowRefsRes] = useState(null);
  const { kind: formDatasetKind } = useDatasetKind(datasetId);
  const rowRefsDone = useRef(false);
  useEffect(() => {
    const refs = prefill?.row_refs;
    if (rowRefsDone.current || isEditing || !Array.isArray(refs)) return;
    if (!sample?.rows) return;
    rowRefsDone.current = true;
    // L7b G2 (data:rows:refs-note): the refs are read against every row of
    // the pinned version (not the first grid page), so a value that names
    // no row is reported missing and never posted.
    const apply = (all) => {
      const res = resolveRowRefs(refs, all, { complete: true });
      setRowRefsRes(res);
      setValue("rowIds", res.ids, { shouldDirty: true });
      if (res.ids[0]) setSampleRowId(res.ids[0]);
    };
    const total = Number(sample.total) || 0;
    if (total <= sample.rows.length) {
      apply(sample.rows);
      return;
    }
    const { url, params } = tableRequestFor({
      datasetId,
      versionNumber: isLatest ? null : selectedVersion?.number,
      versionId: isLatest ? null : selectedVersion?.id,
    });
    // Every page: the table clamps each one, and a ref further down must
    // never be reported missing (that would run every row instead). When
    // the rows cannot be read, keep the refs as asked; create checks them.
    const keepUnverified = () => {
      const asked = [
        ...new Set(refs.map((r) => String(r ?? "").trim()).filter(Boolean)),
      ];
      setRowRefsRes({
        ids: asked,
        labels: asked,
        missing: [],
        found: asked.length,
        asked: asked.length,
      });
      setValue("rowIds", asked, { shouldDirty: true });
    };
    const read = (attempt = 0) =>
      readAllTableRows(url, params).catch((error) => {
        if (attempt < 2) return read(attempt + 1);
        throw error;
      });
    read()
      .then((res) => apply(res.rows))
      .catch(() => keepUnverified());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill?.row_refs, sample, isEditing, setValue]);
  const sampleRow =
    rows.find((r) => r.row_id === sampleRowId) ??
    rows.find((r) => r.order === 11) ??
    rows[0] ??
    null;
  const rowsInScope = values.rowIds?.length
    ? values.rowIds.length // P5: the door's selected rows
    : sample?.total ??
      (values.splitIds?.length
        ? (splitData?.splits ?? [])
            .filter((s) => values.splitIds.includes(s.id))
            .reduce((n, s) => n + (s.row_count ?? 0), 0)
        : selectedVersion?.row_count ?? null);
  // the interface contract: a seeded sample runs (and every estimate prices) its n rows.
  const exampleCount = sampledCount(sampleOfValues(values), rowsInScope);
  const nextOrdinal = isEditing
    ? sourceDetail?.ordinal ?? null
    : suggestion?.nextOrdinal ?? null;

  // Name: the suggestion, then an auto name from the runs until typed.
  const nameTouched = useRef(isEditing || mode === "duplicate");
  const lastAutoName = useRef(null);
  useEffect(() => {
    if (nameTouched.current || !suggestion?.suggestedName) return;
    if (!getValues("name")) {
      setValue("name", suggestion.suggestedName);
      lastAutoName.current = suggestion.suggestedName;
    }
  }, [suggestion?.suggestedName, getValues, setValue]);
  const runNamesKey = flattenRuns(values.promptConfig ?? [])
    .map((r) => r.name)
    .join("|");
  useEffect(() => {
    const current = getValues("name");
    if (nameTouched.current) return;
    if (current && current !== lastAutoName.current) {
      nameTouched.current = true;
      return;
    }
    const next =
      autoName(getValues("promptConfig") ?? [], {
        datasetName: sample?.datasetName,
      }) ?? suggestion?.suggestedName;
    if (next && next !== current) {
      setValue("name", next);
      lastAutoName.current = next;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runNamesKey, nextOrdinal, sample?.datasetName]);
  useEffect(() => {
    if (values.name && values.name !== lastAutoName.current)
      nameTouched.current = true;
  }, [values.name]);

  // exp.rename: what an Edit save already saved (savedMeta: name and/or
  // description), and a name sent without an answer (unconfirmedName: the
  // save may have landed). A retry does not send savedMeta again.
  const [savedMeta, setSavedMeta] = useState(null);
  const [unconfirmedName, setUnconfirmedName] = useState(null);
  const debouncedName = useDebounce(values.name, 400);
  // Edit checks a changed name too, like the list's Edit dialog, and never
  // checks a name that is (or may be) the experiment's own against itself.
  const ownNames = ownNamesOf({
    opened: initialValues.name,
    saved: savedMeta?.name,
    unconfirmed: unconfirmedName,
  });
  const isOwnName = (n) =>
    isEditing && ownNames.includes(String(n ?? "").trim());
  const nameEdited = !isOwnName(values.name);
  const nameChecked = nameEdited && !isOwnName(debouncedName);
  const nameCheck = useExperimentNameCheck(datasetId, debouncedName, {
    enabled: nameChecked,
  });
  const [serverNameTaken, setServerNameTaken] = useState(null);
  // shared-edit(F01): retain name validation except for a saved retry.
  const nameUnavailable =
    nameChecked &&
    ((nameCheck.data === false && debouncedName === values.name) ||
      serverNameTaken === values.name);
  // exp.rename: dev refused this name on an Edit save (runEdit put dev's
  // words on the field); editing the name re-validates and clears it.
  const nameRefused = errors?.name?.type === "server";
  // shared-edit(F01): a restored draft is not re-defaulted or re-copied.
  function keepChoices(restored) {
    nameTouched.current = true;
    refDefaulted.current = true;
    evalsCopiedFrom.current =
      mode === "create"
        ? restored.startFrom?.experimentId ?? null
        : copyEvalsFrom;
  }
  // hook(F01): recover a saved submission without changing its callbacks.
  const form = { getValues, setValue, setCurrentStep, isEditing, keepChoices };
  const nameTaken = useSavedName(datasetId, values.name, nameUnavailable, form);

  // Reference column: the dataset's Expected column by default.
  const refDefaulted = useRef(isEditing);
  useEffect(() => {
    if (refDefaulted.current || !userColumns.length) return;
    refDefaulted.current = true;
    if (!getValues("columnId")) {
      const expected = userColumns.find((c) => c.role === "expected");
      if (expected) setValue("columnId", expected.id);
    }
  }, [userColumns, getValues, setValue]);

  // F67 defaults are NEW: cached dataset markers must not seed classic forms.
  const experimentDefaultsShown = isSurfaceShown("dataset.experimentDefaults");
  const evalsDefaulted = useRef(mode !== "create");
  useEffect(() => {
    if (
      !experimentDefaultsShown ||
      evalsDefaulted.current ||
      !datasetEvals ||
      !columns.length
    )
      return;
    evalsDefaulted.current = true;
    if ((getValues("userEvalMetrics") ?? []).length) return;
    setValue(
      "userEvalMetrics",
      datasetEvals
        .filter((e) => e.is_experiment_default)
        .map((e) => evalFromDatasetDefault(e, columns)),
    );
  }, [experimentDefaultsShown, datasetEvals, columns, getValues, setValue]);

  // options-truth L3 G8 P6: Edit shows, and Duplicate & edit and Start
  // from copy, the evals the source experiment was scored with (its runs'
  // metric keys), unless the experiment lists its own evals (prompt / agent
  // experiments). Edit takes them as the unchanged starting point.
  const copyEvalsFrom =
    mode === "create"
      ? values.startFrom?.experimentId ?? null
      : sourceDetail?.id ?? null;
  const evalsCopiedFrom = useRef(null);
  const editEvalsBase = useRef(null);
  useEffect(() => {
    if (!copyEvalsFrom || evalsCopiedFrom.current === copyEvalsFrom) return;
    if (!datasetEvals || !columns.length || !runConfigsAll) return;
    evalsCopiedFrom.current = copyEvalsFrom;
    if (mode !== "create" && (sourceDetail?.user_eval_metrics ?? []).length)
      return;
    const ids = evalIdsOfExperiment(runConfigsAll, copyEvalsFrom);
    const copied = datasetEvals
      .filter((e) => ids.includes(e.id))
      .map((e) => evalFromDatasetDefault(e, columns));
    if (!copied.length) return;
    if (isEditing) editEvalsBase.current = copied;
    setValue("userEvalMetrics", copied, { shouldDirty: !isEditing });
  }, [
    copyEvalsFrom,
    datasetEvals,
    columns,
    runConfigsAll,
    mode,
    isEditing,
    sourceDetail,
    setValue,
  ]);

  // Variables map themselves to columns by name; user choices stay.
  const variablesKey = (values.promptConfig ?? [])
    .map((c) => (c?.variables ?? []).join(","))
    .join("|");
  useEffect(() => {
    if (!mappingColumns.length) return;
    (getValues("promptConfig") ?? []).forEach((item, i) => {
      if (!item?.variables?.length) return;
      const current = item.variableMapping ?? {};
      const next = autoMapVariables(item.variables, mappingColumns, current);
      if (!sameMapping(current, next))
        setValue(`promptConfig.${i}.variableMapping`, next);
    });
  }, [variablesKey, mappingColumns, getValues, setValue]);

  // ---------------------------------------------------------------- steps
  // L2 (the capability contract): on the real backend a setting dev's strict
  // serializers cannot honour blocks Run on its own step, with its reason
  // (serverBody.js derives them from the contract; prototype mode: none).
  const serverCtx = {
    isEditing,
    // exp.rename: a name / description an Edit save already saved is the
    // experiment's own now, so it is not sent again.
    initial: savedMeta ? { ...initialValues, ...savedMeta } : initialValues,
    columns: mappingColumns,
    // A version id's Run line names the Data step's Current rows choice.
    currentRowsChoice,
    // SRC S1b: only HTTP entries travel; other run changes block Save.
    httpEdit: httpEdit
      ? httpEditChanges(initialValues, values, {
          datasetId,
          columns: mappingColumns,
          initial: initialValues,
        })
      : null,
  };
  const serverBlocks = wireExperimentBody(
    withHttpSourceSettings(
      experimentBodyOfValues(values, { isEditing, datasetId }),
      values.promptConfig,
      serverCtx,
    ),
    serverCtx,
  ).blocks;
  const issues = withServerBlocks(
    stepIssues(values, {
      nameTaken,
      columns: mappingColumns,
      isEditing,
    }),
    serverBlocks,
  );
  const [attempted, setAttempted] = useState(() => new Set());
  const showErrorsFor = (step) => attempted.has(step);
  // Bumps on every failed Next, so open pickers close and its errors show.
  const [failedNext, setFailedNext] = useState(0);
  const markAttempted = (step) => {
    setAttempted((prev) => new Set(prev).add(step));
    setFailedNext((n) => n + 1);
  };
  // Why the last Run sent nothing ({step, text}), shown by the button
  // until the next Run (a refused check used to raise only a short toast).
  const [submitRefusal, setSubmitRefusal] = useState(null);
  const shownIssues = issues.map((list, i) =>
    visitedSteps?.[i] && (i !== currentStep || attempted.has(i)) ? list : [],
  );
  const isLastStep = currentStep === creationSteps.length - 1;
  const runsTotal = runCount(values.experimentType, values.promptConfig);
  // IT-R3 G1 P3: when every run waits on the user's terminal (local or CI),
  // the primary says what it does (nothing runs in Future AGI yet).
  const allRunsList = flattenRuns(values.promptConfig ?? []);
  const allTerminal =
    allRunsList.length > 0 &&
    terminalRunsOf(values.promptConfig ?? [], allRunsList).length ===
      allRunsList.length;
  // Start from with carry-over: only the new rows run (R2-SPEC §5.3.1).
  const sf = values.startFrom;
  const carried =
    !isEditing && sf?.versionMoved && sf?.carryOver !== false
      ? Math.min(Number(sf.rows) || 0, Number(exampleCount) || 0)
      : 0;
  const trialsN = Number(values.trials) || 1;
  const toRun = Math.max(0, (Number(exampleCount) || 0) - carried);
  // L5 G3: each run plays its own tries (card limits, else Trials), so the
  // count sums per run (one count for the footer, Review and the summary).
  const perRunTries =
    values.experimentType === "llm" || !values.experimentType
      ? allRunsList.map((r) => ({
          label: nextOrdinal != null ? `#${nextOrdinal}${r.letter}` : r.letter,
          tries: runTriesOf(values.promptConfig?.[r.configIndex], trialsN),
        }))
      : null;
  const triesSum = perRunTries
    ? perRunTries.reduce((s, r) => s + r.tries, 0)
    : trialsN * runsTotal;
  const mixedTries =
    perRunTries && new Set(perRunTries.map((r) => r.tries)).size > 1;
  const carryTries = mixedTries
    ? perRunTries.map((r) => `${r.label} ×${r.tries}`).join(" · ")
    : null;
  const carryTrials = perRunTries?.[0]?.tries ?? trialsN;
  // v2.1 (edit:estimate): an Edit save starts only its re-run and new
  // runs (editRunScope, the footer estimate's own scope), so the summary
  // counts those tries and says the kept runs keep theirs.
  const editRunsScope =
    isEditing && sourceDetail
      ? editRunScope(initialValues, values, {
          runs: sourceDetail?.runs ?? [],
          flatten: flattenRuns,
          columns: mappingColumns,
        })
      : null;
  // SRC S1b: an HTTP Edit save runs nothing.
  const editScope =
    editRunsScope && httpEdit
      ? { ...editRunsScope, promptConfig: [] }
      : editRunsScope;
  const editRuns = editScope ? flattenRuns(editScope.promptConfig) : null;
  const keptN = editScope?.kept?.length ?? 0;
  const keptText = keptN
    ? `${keptN} ${keptN === 1 ? "run" : "runs"} kept (${keptN === 1 ? "its tries are" : "their tries are"} not re-run)`
    : null;
  const editSummary = editScope
    ? editRuns.length
      ? [
          executionSummary({
            examples: exampleCount,
            trials: trialsN,
            runs: editRuns.length,
            perRun: editRuns.map((r) => ({
              label:
                nextOrdinal != null ? `#${nextOrdinal}${r.letter}` : r.letter,
              tries: runTriesOf(
                editScope.promptConfig?.[r.configIndex],
                trialsN,
              ),
            })),
          }),
          keptText,
        ]
          .filter(Boolean)
          .join(" · ")
      : `Nothing re-runs${keptText ? ` · ${keptText}` : ""}`
    : null;
  const summaryLine = editSummary
    ? editSummary
    : runsTotal && carried
      ? `${toRun.toLocaleString("en-US")} new rows${carryTries ? ` · ${carryTries}` : ` × ${carryTrials} ${carryTrials === 1 ? "try" : "tries"} × ${runsTotal} ${runsTotal === 1 ? "run" : "runs"}`} = ${(toRun * triesSum).toLocaleString("en-US")} tries · ${carried.toLocaleString("en-US")} rows carried over`
      : runsTotal
        ? executionSummary({
            examples: exampleCount,
            trials: Number(values.trials) || 1,
            runs: runsTotal,
            perRun: perRunTries,
          })
        : exampleCount != null
          ? // The Runs heading right above already says how to add one.
            `${Number(exampleCount).toLocaleString("en-US")} rows`
          : null;

  // IT-R4 G1 P1: one rule: an unmapped variable disables Continue on Data
  // (the sentence + its fix sit in the table) and the run button on Review
  // (with `Go to Data`), never a silent bounce.
  const dataBlocked =
    currentStep === EXPERIMENT_STEP.DATA &&
    issues[EXPERIMENT_STEP.DATA].length > 0;
  const earlierBlock = isLastStep
    ? issues.findIndex((list, i) => i < currentStep && list.length)
    : -1;
  // L2: a Review-step setting dev cannot honour disables Run with its reason.
  const reviewServerBlock = isLastStep
    ? serverBlocks.find((b) => b.step >= currentStep) ?? null
    : null;
  // L2 verify 2 must-fix: an earlier step's own block (the Data step's
  // remapped variable, sample, split, rows or version) shows on that step
  // as text, not only in the disabled Continue's tooltip.
  const stepServerBlock = isLastStep
    ? reviewServerBlock
    : serverBlockOnStep(serverBlocks, currentStep, creationSteps.length);
  const runBlockTitle =
    earlierBlock !== -1
      ? `${creationSteps[earlierBlock].title}: ${issues[earlierBlock][0]}`
      : reviewServerBlock?.text ?? null;
  const handleNext = () => {
    if (issues[currentStep].length) {
      markAttempted(currentStep);
      return;
    }
    nextStep();
  };

  // ---------------------------------------------------------------- keys
  // MUST 6 / CR#29: ⌘↵ = Next on every step and Run on the last; ⌥← / ⌥→
  // move between steps.
  const estimate = useEstimateDetails();
  const keysRef = useRef({});
  const rawPickerLevel = usePickerUi((st) => st.level);
  const pickerLevel = enhancementsShown ? rawPickerLevel : 0;
  const setFooterEl = usePickerUi((st) => st.setFooterEl);
  useShortcuts("newExperiment", {
    wizardNext: pickerLevel === 3 ? undefined : () => keysRef.current.next?.(),
    // ⌥← is the catalog's Back while the picker is deeper than Level 1.
    wizardPrevStep:
      pickerLevel >= 2 ? undefined : () => keysRef.current.prev?.(),
    wizardNextStep: () => keysRef.current.step?.(),
  });

  // ---------------------------------------------------------------- submit
  const create = useCreateExperiment();
  const [readingReceipt, setReadingReceipt] = useState(false);
  // exp.rename: runEdit says a refused save once, in plain words, so the
  // app-wide error toast stays out of both PUTs.
  const update = useUpdateExperiment(sourceDetail?.id, {
    meta: { errorHandled: true },
  });
  // The edit's name / description, saved on their own.
  const saveMetadata = useUpdateExperiment(sourceDetail?.id, {
    meta: { errorHandled: true },
  });
  // IT-R2 G1 P2: `Add to experiment and open results` runs and opens.
  const openAfterSubmit = useRef(false);
  const [confirmEdit, setConfirmEdit] = useState(null);

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["develop", "dataset-runs", datasetId],
    });
    queryClient.invalidateQueries({ queryKey: ["experiment-list", datasetId] });
    queryClient.invalidateQueries({
      queryKey: ["experiment-suggested-name", datasetId],
    });
    invalidateDatasetListCache(queryClient);
  }, [queryClient, datasetId]);

  // L2: the DSX body (experimentBody.js) as dev reads it; `blocks` holds
  // what dev cannot honour (never stripped silently).
  const bodyOf = (data) =>
    wireExperimentBody(
      withHttpSourceSettings(
        experimentBody(data, getValues(), { isEditing, datasetId }),
        getValues("promptConfig"),
        serverCtx,
      ),
      serverCtx,
    );

  const blockedSubmit = (blocks) => {
    if (!blocks.length) return false;
    const step = Math.min(
      creationSteps.length - 1,
      ...blocks.map((b) => b.step),
    );
    openAfterSubmit.current = false;
    markAttempted(step);
    setCurrentStep(step);
    // A block only the submitted body carries is in no step's issues.
    setSubmitRefusal(refusalOfBlocks(blocks, issues));
    return true;
  };

  const submitCreate = (data) => {
    trackEvent(Events.derivedDatasetCreated, {
      [PropertyName.derivedDataset]: { "Derived Dataset Name": data?.name },
    });
    const external = allRunsExternal(getValues("promptConfig"));
    const wire = bodyOf(data);
    if (blockedSubmit(wire.blocks)) return;
    create.mutate(wire.body, {
      onSuccess: async (res) => {
        const r = parseCreateResponse(res);
        const scopeShown = isSurfaceShown("exp.new.scope");
        // F50 qualifies independently: its persisted selection receipt must not
        // depend on lifting F05's scope/settings hold.
        const receiptShown = scopeShown || isSurfaceShown("exp.new.splitScope");
        let receipt = { examples: null, lines: [] };
        if (receiptShown && !isPrototypeMode() && r.experimentId) {
          setReadingReceipt(true);
          try {
            const detail = await axios.get(
              endpoints.develop.experiment.getExperimentDetails(r.experimentId),
              { timeout: 8000 },
            );
            receipt = scopeReceipt(detail.data.result, {
              includeScopeSettings: scopeShown,
            });
          } catch {
            receipt.lines = [
              "Experiment created; server settings could not be read back. Open the experiment to retry.",
            ];
          } finally {
            setReadingReceipt(false);
          }
        }
        const examples = isPrototypeMode()
          ? r.examples ?? exampleCount
          : receipt.examples ?? r.examples;
        const ordinal = r.ordinal ?? nextOrdinal;
        // L2: dev answers no ordinal; the toast names the experiment then.
        const title =
          ordinal != null
            ? `Experiment #${ordinal}`
            : `“${String(data?.name ?? "").trim()}”`;
        const n = r.runs.length || runsTotal;
        // IT-R2 G1 P2: runs started from the user's terminal are not running.
        const terminalN = terminalRunsOf(
          getValues("promptConfig") ?? [],
          flattenRuns(getValues("promptConfig") ?? []),
        ).length;
        const carried = res?.data?.result?.carry_over?.message;
        const text = carried
          ? carried
          : openAfterSubmit.current
            ? `${title} created`
            : terminalN
              ? `${title} created · ${terminalN === n ? (n === 1 ? "1 run waits" : `${n} runs wait`) : `${terminalN} of ${n} runs wait`} for your terminal`
              : external
                ? examples != null
                  ? `${title} created · waiting for results on ${examples} rows`
                  : `${title} created · waiting for results`
                : examples != null
                  ? `${title} started · ${n} ${n === 1 ? "run" : "runs"} × ${examples} ${Number(examples) === 1 ? "row" : "rows"}`
                  : `${title} started · ${n} ${n === 1 ? "run" : "runs"}`;
        const href = r.experimentId
          ? `/dashboard/develop/experiment/${r.experimentId}/data`
          : null;
        // CR#24: Open + Undo (cancels the experiment while the toast shows,
        // MAX_TOAST_MS); the runs
        // join the run tray (CR#42) and the leaderboard row goes live.
        // IT-R2 G1 (S6): a run that waits on the user's terminal opens the
        // waiting panel, which already says it was created; no toast then.
        if (!(external && r.experimentId && !openAfterSubmit.current))
          notify(
            <span>
              {text}
              {receiptShown &&
                receipt.lines.map((line) => (
                  <span key={line} style={{ display: "block" }}>
                    {line}
                  </span>
                ))}
              {href ? (
                <>
                  {" · "}
                  <Link
                    component="button"
                    type="button"
                    color="inherit"
                    underline="always"
                    onClick={() => navigate(href)}
                    sx={{ typography: "inherit", verticalAlign: "baseline" }}
                  >
                    Open
                  </Link>
                </>
              ) : null}
            </span>,
            {
              variant: "success",
              key: `experiment-created-${r.experimentId ?? ordinal}`,
              // P1: toasts raised from the drawer sit top-center, never
              // over code or the footer.
              anchorOrigin: { vertical: "top", horizontal: "center" },
              undo:
                isSurfaceShown("dataset.tab.experiments") && r.experimentId
                  ? {
                      ms: MAX_TOAST_MS,
                      onUndo: async () => {
                        // v2.1 (action:create-toast-undo): leave the deleted
                        // experiment first (back to the dataset's experiments),
                        // stop the tray and the page from asking for it, then
                        // delete it.
                        const expId = r.experimentId;
                        useRunTray
                          .getState()
                          .untrack((r.runs ?? []).map((x) => x.run_id));
                        if (window.location.pathname.includes(expId))
                          navigate(undoReturnPath(datasetId, window.location));
                        try {
                          // Dev's delete route answers DELETE only (L2).
                          await axios.delete(
                            endpoints.develop.experiment.delete,
                            {
                              data: { experiment_ids: [expId] },
                            },
                          );
                          queryClient.removeQueries({
                            predicate: (q) =>
                              JSON.stringify(q.queryKey ?? []).includes(expId),
                          });
                          invalidate();
                          notify(`${title} cancelled`, {
                            key: `experiment-created-${r.experimentId}`,
                          });
                        } catch (e) {
                          notify(
                            getRequestErrorMessage(
                              e,
                              "Could not cancel the experiment",
                            ),
                            { variant: "error" },
                          );
                        }
                      },
                    }
                  : undefined,
            },
          );
        if (isSurfaceShown("dataset.tab.experiments") && r.runs?.length)
          trackRuns(r.runs, { datasetId, experimentId: r.experimentId });
        invalidate();
        refreshGrid?.(null, true);
        setFormIsDirty?.(false);
        if (openAfterSubmit.current && href) {
          openAfterSubmit.current = false;
          onSubmitted?.();
          navigate(href);
          return;
        }
        if (external && r.experimentId) {
          onLaunched({
            experimentId: r.experimentId,
            ordinal,
            runs: r.runs,
            examples,
            trials: Number(getValues("trials")) || 1,
            exampleId: sampleRow?.row_id,
            // R4: the Level 3 draft of each run, in run order.
            r4: (getValues("promptConfig") ?? []).map((c) => c?.r4 ?? null),
            datasetName: sample?.datasetName ?? null,
            inputColumn: context.inputColumn,
            keys: context.prototypeKeys,
            // IT-R1 G1 P1: `Download sample_rows.jsonl` after Run too.
            sample: {
              rows: (context.rows ?? []).slice(0, 3),
              columns: context.columns ?? [],
            },
          });
          return;
        }
        onSubmitted?.();
        // P6 (ir-11): land on the new run's results with its progress,
        // not on the dataset grid with a toast.
        if (href) navigate(href);
      },
      onError: (error) => {
        const message = getRequestErrorMessage(
          error,
          "Could not start the experiment. Try again.",
        );
        if (/already exists/i.test(message)) {
          setServerNameTaken(getValues("name"));
          markAttempted(EXPERIMENT_STEP.CONFIGURATION);
          setCurrentStep(EXPERIMENT_STEP.CONFIGURATION);
          return;
        }
        enqueueSnackbar(message, { variant: "error" });
      },
    });
  };

  const submitEdit = (data) => {
    const original = editEvalsBase.current
      ? { ...initialValues, userEvalMetrics: editEvalsBase.current }
      : initialValues;
    const summary = editChangeSummary(original, getValues(), {
      runs: sourceDetail?.runs ?? [],
      flatten: flattenRuns,
      columns: mappingColumns,
    });
    const wire = bodyOf(data);
    if (blockedSubmit(wire.blocks)) return;
    setConfirmEdit({
      summary,
      ...editSavePlan(wire, { original, current: getValues(), datasetId }),
    });
  };

  // exp.rename: an Edit save is up to two PUTs, run in order by
  // editSave.js runEditSave: a changed name or description first, on its
  // own, then the run settings only when they changed, so a rename alone
  // starts nothing. Each step resolves with dev's answer or rejects with
  // its error; runEdit says the outcome once, in plain words.
  const saveSettings = (body) =>
    new Promise((resolve, reject) => {
      update.mutate(body, { onSuccess: resolve, onError: reject });
    });
  const saveNameAndDescription = (body) =>
    new Promise((resolve, reject) => {
      saveMetadata.mutate(body, { onSuccess: resolve, onError: reject });
    });
  const settingsSavedText = (res) => {
    const c = res?.data?.result?.changes ?? {};
    const bits = [
      c.rerun?.length && `re-running ${c.rerun.join(", ")}`,
      c.added?.length && `added ${c.added.join(", ")}`,
      c.evals_added?.length && `scoring ${c.evals_added.join(", ")}`,
    ].filter(Boolean);
    return `Saved #${sourceDetail?.ordinal ?? ""}${bits.length ? ` · ${bits.join(" · ")}` : ""}`;
  };

  // One Edit save at a time (the confirm's button waits while it runs).
  const editSaving = useRef(false);
  const runEdit = async () => {
    const plan = confirmEdit;
    if (!plan || editSaving.current) return;
    editSaving.current = true;
    try {
      const experimentId = sourceDetail?.id;
      const outcome = await runEditSave(plan, {
        saveMetadata: saveNameAndDescription,
        saveSettings,
      });
      const kept = outcome.done.includes("metadata")
        ? renameSuccessText(outcome.renamed, plan.metadata)
        : null;
      if (kept) {
        refreshAfterRename(queryClient, { datasetId, experimentId });
        // The name is saved: a retry sends only the run settings.
        setSavedMeta((s) => ({ ...s, ...plan.metadata }));
        setConfirmEdit((c) => (c ? { ...c, metadata: null } : c));
      }
      if (outcome.failed?.step === "metadata") {
        const failure = renameErrorOf(outcome.failed.error);
        // No answer: the save may have landed, so the lists read again and
        // the name check does not call that name taken by this experiment.
        if (failure.unknown) {
          refreshAfterRename(queryClient, { datasetId, experimentId });
          if (plan.metadata.name !== undefined)
            setUnconfirmedName(plan.metadata.name);
        }
        // A refused name or description: dev's words under that field, on
        // the step where it is edited (no toast that disappears).
        if (renameFieldErrors(failure, setError)) {
          setConfirmEdit(null);
          setCurrentStep(EXPERIMENT_STEP.CONFIGURATION);
          return;
        }
        enqueueSnackbar(renameErrorText(failure), { variant: "error" });
        return;
      }
      if (outcome.failed) {
        enqueueSnackbar(settingsErrorText(outcome.failed.error, kept), {
          variant: "error",
        });
        return;
      }
      enqueueSnackbar(
        outcome.done.includes("settings")
          ? settingsSavedText(outcome.saved)
          : kept,
        { variant: "success" },
      );
      queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
      invalidate();
      setConfirmEdit(null);
      setFormIsDirty?.(false);
      onSubmitted?.();
    } finally {
      editSaving.current = false;
    }
  };
  const renameOnly = Boolean(confirmEdit?.metadata && !confirmEdit?.config);

  const trySubmit = () => {
    setSubmitRefusal(null);
    const first = issues.findIndex((list) => list.length);
    if (first !== -1) {
      openAfterSubmit.current = false;
      const step = Math.min(first, creationSteps.length - 1);
      markAttempted(step);
      setCurrentStep(step);
      // An issue of the step on screen reads by the button too.
      if (step === currentStep)
        setSubmitRefusal({ step, text: issues[first][0] });
      return;
    }
    handleSubmit(isEditing ? submitEdit : submitCreate, (errors) => {
      enqueueSnackbar("Some fields need attention.", { variant: "warning" });
      // The toast alone left Run looking dead; say what, by the button.
      setSubmitRefusal(refusalOfErrors(errors, getValues()));
    })().catch(() =>
      setSubmitRefusal({ step: currentStep, text: SUBMIT_CHECK_FAILED }),
    );
  };

  const handlePromptConfigChange = (e) => {
    const updatedType = e.target.value;
    setValue(
      "promptConfig",
      updatedType === MODEL_TYPES.LLM
        ? []
        : [getDefaultPromptConfigByModelType(updatedType)],
    );
    let outputFormat = "string";
    if (updatedType === MODEL_TYPES.TTS) outputFormat = "audio";
    if (updatedType === MODEL_TYPES.IMAGE) outputFormat = "image";
    setValue("outputFormat", outputFormat);
    clearErrors(["promptConfig", "experimentType", "outputFormat"]);
  };

  const runsById = useMemo(
    () =>
      Object.fromEntries((sourceDetail?.runs ?? []).map((r) => [r.run_id, r])),
    [sourceDetail],
  );
  const { user: authUser } = useAuthContext();
  const orgId = authUser?.organization?.id ?? null;
  // L7b G2 (edit:estimate): what the experiment had when Edit opened, so
  // the footer prices only the runs a save starts.
  const editBase = useMemo(
    () =>
      isEditing
        ? { values: initialValues, runs: sourceDetail?.runs ?? [] }
        : null,
    [isEditing, initialValues, sourceDetail],
  );
  const context = useMemo(
    () => ({
      datasetId,
      experimentId: isEditing ? sourceDetail?.id ?? null : null,
      runsById: isEditing ? runsById : {},
      isEditing,
      editBase,
      nextOrdinal,
      columns,
      mappingColumns,
      userColumns,
      jsonSchemas,
      rows,
      sampleRow,
      sampleRowLabel: rowLabelOf(sampleRow),
      setSampleRowId,
      exampleCount,
      rowsInScope,
      versions,
      versionsHeld,
      savedVersions,
      runsCurrentRows,
      savedVersionsLoading,
      savedVersionsFailed,
      retrySavedVersions,
      splits: splitData?.splits ?? [],
      startFromPreset: !isEditing ? prefill?.start_from ?? null : null,
      // R4 (lane Ra): what the run-source picker's snippets need.
      datasetName: sample?.datasetName ?? null,
      inputColumn:
        inputColumnFor("input", mappingColumns)?.column ??
        mappingColumns.find((c) => c.role === "input")?.name ??
        null,
      // IT-R2 G1 P6: the voice config's estimate counts the same judges.
      judgeCount: judgesOf(values.userEvalMetrics ?? []).length,
      // IT-R4 G1 P2: the runner shows pass / fail only once an eval exists.
      evalCount: (values.userEvalMetrics ?? []).length,
      // options-truth L1 G5: one account-level pair per org (the Keys page
      // shows the same), never seeded from the dataset.
      prototypeKeys: isPrototypeMode() ? orgKeyPair(orgId) : null,
      // options-truth L1 G2 (C15): the Data step's scope and the chosen
      // evals, so Simulate runs those rows and experiment.yml names them.
      rowIds: values.rowIds ?? [],
      rowRefsNote: rowRefsNote(rowRefsRes, {
        noun: formDatasetKind === "task_suite" ? "task" : "row",
      }),
      splitIds: values.splitIds ?? [],
      evalNames: (values.userEvalMetrics ?? [])
        .map((m) => m?.name ?? m?.evalTemplateName ?? null)
        .filter(Boolean),
    }),
    [
      orgId,
      datasetId,
      sourceDetail,
      runsById,
      isEditing,
      editBase,
      nextOrdinal,
      columns,
      mappingColumns,
      userColumns,
      jsonSchemas,
      rows,
      sampleRow,
      exampleCount,
      rowsInScope,
      versions,
      versionsHeld,
      savedVersions,
      runsCurrentRows,
      savedVersionsLoading,
      savedVersionsFailed,
      retrySavedVersions,
      splitData,
      prefill,
      sample?.datasetName,
      values.userEvalMetrics,
      values.rowIds,
      values.splitIds,
      rowRefsRes,
      formDatasetKind,
    ],
  );

  const pending = create.isPending || update.isPending || readingReceipt;
  const submitRequest = usePickerUi((st) => st.submitRequest);
  const seenSubmit = useRef(submitRequest);
  useEffect(() => {
    if (submitRequest === seenSubmit.current) return;
    seenSubmit.current = submitRequest;
    if (isEditing) return;
    openAfterSubmit.current = true;
    trySubmit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submitRequest]);
  const stepIssuesNow = showErrorsFor(currentStep)
    ? !enhancementsShown && isLastStep
      ? issues.slice(currentStep).flat()
      : issues[currentStep]
    : [];
  keysRef.current = {
    next: () => (isLastStep ? trySubmit() : handleNext()),
    prev: () => currentStep > 0 && prevStep(),
    step: () => !isLastStep && handleNext(),
  };

  return (
    <ExperimentFormContext.Provider value={context}>
      <form
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
        }}
        onSubmit={(e) => {
          e.preventDefault();
          trySubmit();
        }}
        // ⌘↵ before a focused Select / menu trigger eats Enter (MUI opens
        // its menu on any Enter); the wizard key wins inside the form.
        onKeyDownCapture={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            e.stopPropagation();
            keysRef.current.next?.();
          }
        }}
      >
        <Box sx={{ pb: 1.5 }}>
          <StepperForExperiment issues={shownIssues} />
        </Box>
        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            overflowX: "hidden",
            pr: 0.5,
            pt: 1,
            pb: 2,
          }}
        >
          {currentStep === EXPERIMENT_STEP.CONFIGURATION ? (
            <ConfigureStepExperimentCreation
              control={control}
              setValue={setValue}
              allColumns={schemaColumnsRef.current}
              getValues={getValues}
              errors={errors}
              unregister={unregister}
              clearErrors={clearErrors}
              watch={watch}
              setError={setError}
              derivedVariables={derivedVariables}
              jsonSchemas={jsonSchemas}
              isEditing={isEditing}
              handleModelTypeChange={handlePromptConfigChange}
              showErrors={showErrorsFor(EXPERIMENT_STEP.CONFIGURATION)}
              failedNext={failedNext}
              nameState={{
                // shared-edit(F02): never replace a name while its input is being edited.
                onFocus: () => {
                  nameTouched.current = true;
                },
                // IT-R1 G1 P8: the name is a suggestion until typed.
                suggested:
                  !isEditing &&
                  Boolean(values.name) &&
                  !nameTouched.current &&
                  values.name === lastAutoName.current,
                checking:
                  nameEdited &&
                  Boolean(values.name?.trim()) &&
                  (nameCheck.isFetching || debouncedName !== values.name),
                // exp.rename: a name dev refused on Save also marks the
                // field; its helper text is dev's words (errors.name).
                taken: nameTaken || nameRefused,
                valid:
                  nameChecked &&
                  Boolean(values.name?.trim()) &&
                  !nameTaken &&
                  !nameRefused &&
                  nameCheck.data === true &&
                  debouncedName === values.name,
              }}
            />
          ) : null}
          {currentStep === EXPERIMENT_STEP.DATA ? (
            <DataStep
              control={control}
              setValue={setValue}
              isEditing={isEditing}
              showErrors={showErrorsFor(EXPERIMENT_STEP.DATA)}
            />
          ) : null}
          {currentStep === EXPERIMENT_STEP.EVALUATIONS ? (
            <EvaluationStepExperimentCreation
              control={control}
              allColumns={experimentColumns ?? undefined}
              showErrors={showErrorsFor(EXPERIMENT_STEP.EVALUATIONS)}
            />
          ) : null}
          {enhancementsShown && currentStep === EXPERIMENT_STEP.REVIEW ? (
            <ReviewStep
              control={control}
              setValue={setValue}
              isEditing={isEditing}
            />
          ) : null}
          {earlierBlock === -1 && stepServerBlock && !stepIssuesNow.length ? (
            <Stack
              data-testid="server-blocked"
              role="alert"
              direction="row"
              alignItems="center"
              gap={1}
              sx={{
                mt: 1.5,
                px: 1.25,
                py: 0.75,
                borderRadius: 0.5,
                bgcolor: "red.o5",
              }}
            >
              <Iconify
                icon="mdi:alert-circle-outline"
                width={16}
                sx={{ color: "error.main", flexShrink: 0 }}
              />
              <Typography
                typography="s2"
                color="error.main"
                sx={{ minWidth: 0, flex: 1 }}
              >
                {`Not ready: ${stepServerBlock.text}`}
              </Typography>
            </Stack>
          ) : null}
          {earlierBlock !== -1 ? (
            <Stack
              data-testid="review-blocked"
              role="alert"
              direction="row"
              alignItems="center"
              gap={1}
              sx={{
                mt: 1.5,
                px: 1.25,
                py: 0.75,
                borderRadius: 0.5,
                bgcolor: "red.o5",
              }}
            >
              <Iconify
                icon="mdi:alert-circle-outline"
                width={16}
                sx={{ color: "error.main", flexShrink: 0 }}
              />
              <Typography
                typography="s2"
                color="error.main"
                sx={{ minWidth: 0, flex: 1 }}
              >
                {`Not ready: ${creationSteps[earlierBlock].title} · ${issues[earlierBlock][0]}`}
              </Typography>
              <Button
                size="small"
                color="error"
                variant="outlined"
                onClick={() => {
                  markAttempted(earlierBlock);
                  setCurrentStep(earlierBlock);
                }}
                sx={{ typography: "s2", flexShrink: 0 }}
              >
                {`Go to ${creationSteps[earlierBlock].title}`}
              </Button>
            </Stack>
          ) : null}
          {stepIssuesNow.length ? (
            // CR#10 / CR#11: one summary line (the details sit inline on
            // the fields); `Show` scrolls to and focuses the first one.
            <Stack
              data-testid="step-issues"
              role="alert"
              direction="row"
              alignItems="center"
              gap={1}
              sx={{
                mt: 1.5,
                px: 1.25,
                py: 0.75,
                borderRadius: 0.5,
                bgcolor: "red.o5",
              }}
            >
              <Iconify
                icon="mdi:alert-circle-outline"
                width={16}
                sx={{ color: "error.main", flexShrink: 0 }}
              />
              <CustomTooltip
                show
                title={stepIssuesNow.join(" · ")}
                size="small"
                arrow
              >
                <Typography
                  typography="s2"
                  color="error.main"
                  noWrap
                  sx={{ minWidth: 0, flex: 1 }}
                >
                  {stepIssuesNow.length === 1
                    ? stepIssuesNow[0]
                    : `${stepIssuesNow.length} things to fix: ${stepIssuesNow[0]}`}
                </Typography>
              </CustomTooltip>
              <Button
                size="small"
                color="error"
                onClick={() => {
                  const root = document.querySelector(
                    '[data-testid="experiment-drawer"]',
                  );
                  const bad = root?.querySelector(
                    '[aria-invalid="true"], .Mui-error input, .Mui-error, [data-issue="true"]',
                  );
                  bad?.scrollIntoView({ block: "center", behavior: "smooth" });
                  const focusable = bad?.matches?.(
                    "input,textarea,button,[tabindex]",
                  )
                    ? bad
                    : bad?.querySelector?.("input,textarea,button,[tabindex]");
                  focusable?.focus?.({ preventScroll: true });
                }}
                sx={{ typography: "s2", px: 0.75, minWidth: 0 }}
              >
                Show
              </Button>
            </Stack>
          ) : null}
        </Box>

        {enhancementsShown && isLastStep && !isEditing ? (
          <Box sx={{ flexShrink: 0 }}>
            <EstimateBreakdown
              control={control}
              setValue={setValue}
              open={estimate.open}
              onClose={estimate.close}
              id={estimate.id}
              btnRef={estimate.btnRef}
            />
          </Box>
        ) : null}
        {/* Level 3's sticky footer (Try on 1 row · Add to experiment) */}
        {enhancementsShown && (
          <Box
            ref={setFooterEl}
            data-testid="picker-footer-slot"
            sx={{
              display: pickerLevel === 3 ? "flex" : "none",
              alignItems: "center",
              pt: 1.5,
              pr: 6,
              borderTop: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
              mb: "var(--fa-fab-gutter, 0px)",
            }}
          />
        )}
        {/* Why the last Run sent nothing, next to the button. */}
        {submitRefusal && pickerLevel !== 3 ? (
          <Stack
            data-testid="submit-refusal"
            role="alert"
            direction="row"
            alignItems="center"
            gap={1}
            sx={{
              mt: 1,
              mr: 6,
              px: 1.25,
              py: 0.75,
              borderRadius: 0.5,
              bgcolor: "red.o5",
              flexShrink: 0,
            }}
          >
            <Iconify
              icon="mdi:alert-circle-outline"
              width={16}
              sx={{ color: "error.main", flexShrink: 0 }}
            />
            <Typography
              typography="s2"
              color="error.main"
              sx={{ minWidth: 0, flex: 1 }}
            >
              {refusalLine(submitRefusal, creationSteps)}
            </Typography>
            {submitRefusal.step !== currentStep &&
            creationSteps[submitRefusal.step] ? (
              <Button
                size="small"
                color="error"
                variant="outlined"
                onClick={() => {
                  markAttempted(submitRefusal.step);
                  setCurrentStep(submitRefusal.step);
                }}
                sx={{ typography: "s2", flexShrink: 0 }}
              >
                {`Go to ${creationSteps[submitRefusal.step].title}`}
              </Button>
            ) : null}
          </Stack>
        ) : null}
        <Box
          sx={{
            display: pickerLevel === 3 ? "none" : "flex",
            alignItems: "center",
            gap: 1.5,
            pt: 1.5,
            pr: 6,
            borderTop: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          {enhancementsShown && isLastStep && !isEditing ? (
            <EstimateSummary
              control={control}
              open={estimate.open}
              onToggle={estimate.toggle}
              controlsId={estimate.id}
              btnRef={estimate.btnRef}
            />
          ) : (
            <Stack
              direction="row"
              alignItems="center"
              gap={1}
              sx={{ flex: 1, minWidth: 0 }}
            >
              {/* IT-R5 G1 P4: a running total as runs are added. */}
              {enhancementsShown && runsTotal ? (
                <SimpleEstimate control={control} chip />
              ) : null}
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="execution-summary"
                sx={{ minWidth: 0 }}
              >
                {summaryLine ?? ""}
              </Typography>
            </Stack>
          )}
          <Button
            onClick={prevStep}
            disabled={currentStep === 0}
            variant="outlined"
            sx={{
              minWidth: 110,
              color: "text.primary",
              borderColor: "divider",
            }}
          >
            Back
          </Button>
          {!isLastStep ? (
            <CustomTooltip
              show={dataBlocked}
              title={issues[EXPERIMENT_STEP.DATA].join(" · ")}
              size="small"
              placement="top"
            >
              <span>
                <Button
                  onClick={handleNext}
                  disabled={dataBlocked}
                  variant="contained"
                  color="primary"
                  aria-keyshortcuts={
                    enhancementsShown ? "Meta+Enter" : undefined
                  }
                  data-testid="wizard-continue"
                  sx={{ minWidth: 143, gap: 1 }}
                >
                  {/* IT-R3 G1 P3: it only moves to the next step. */}
                  Continue
                  {enhancementsShown && (
                    <Typography
                      component="span"
                      typography="s3"
                      sx={{ opacity: 0.7 }}
                    >{`${MOD_KEY_LABEL}↵`}</Typography>
                  )}
                </Button>
              </span>
            </CustomTooltip>
          ) : runBlockTitle ? (
            <CustomTooltip
              show
              title={runBlockTitle}
              size="small"
              placement="top"
            >
              <span>
                <LoadingButton
                  disabled
                  variant="contained"
                  color="primary"
                  data-testid="wizard-run-blocked"
                  sx={{ minWidth: 143, whiteSpace: "nowrap" }}
                >
                  {isEditing
                    ? "Save changes"
                    : allTerminal
                      ? VOCAB.CREATE_AND_GET_COMMANDS
                      : "Run experiment"}
                </LoadingButton>
              </span>
            </CustomTooltip>
          ) : (
            <LoadingButton
              onClick={trySubmit}
              variant="contained"
              color="primary"
              loading={pending}
              sx={{ minWidth: 143, whiteSpace: "nowrap" }}
              endIcon={
                enhancementsShown && (
                  <Typography
                    component="span"
                    typography="s3"
                    sx={{ opacity: 0.7 }}
                  >
                    {`${MOD_KEY_LABEL}↵`}
                  </Typography>
                )
              }
            >
              {isEditing
                ? "Save changes"
                : allTerminal
                  ? VOCAB.CREATE_AND_GET_COMMANDS
                  : "Run experiment"}
            </LoadingButton>
          )}
        </Box>
      </form>
      <ConfirmDialog
        open={Boolean(confirmEdit)}
        onClose={() => setConfirmEdit(null)}
        title={`Save #${sourceDetail?.ordinal ?? ""}?`}
        content={
          <Stack spacing={1}>
            <Typography typography="s1">
              {renameOnly
                ? "Only the name or description changes. Nothing re-runs."
                : (httpEdit
                    ? httpEditSummaryText(confirmEdit?.summary?.text)
                    : confirmEdit?.summary?.text) ||
                  "Only the name or description changes."}
            </Typography>
            <Typography typography="s2" color="text.secondary">
              {httpEdit
                ? HTTP_RESEND_COPY.editSaveOnly
                : editKeptLine(confirmEdit?.summary?.rerun, sourceDetail?.runs)}
            </Typography>
          </Stack>
        }
        action={
          <LoadingButton
            variant="contained"
            color="primary"
            size="small"
            loading={update.isPending || saveMetadata.isPending}
            onClick={runEdit}
          >
            {renameOnly
              ? "Save"
              : httpEdit
                ? HTTP_RESEND_COPY.editSave
                : "Save & run changes"}
          </LoadingButton>
        }
      />
    </ExperimentFormContext.Provider>
  );
};

RunExperimentForm.propTypes = {
  datasetId: PropTypes.string,
  mode: PropTypes.oneOf(["create", "edit", "duplicate"]),
  sourceDetail: PropTypes.object,
  sourceConfigs: PropTypes.object,
  experimentColumns: PropTypes.array,
  prefill: PropTypes.object,
  setFormIsDirty: PropTypes.func,
  onDraftSummary: PropTypes.func,
  onSubmitted: PropTypes.func,
  onLaunched: PropTypes.func,
  onDatasetName: PropTypes.func,
};

/**
 * New / Edit experiment drawer (PRODUCT-SPEC §3.8), opened in place from
 * every entry point through useRunExperimentStore (initiateCreateMode /
 * initiateEditMode). Also hosts the run-action confirm dialog.
 */
const RunExperiment = ({
  tabRef: _tabRef,
  onSuccessfulSubmit,
  experimentColumns = null,
}) => {
  const { dataset: datasetParam } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    openRunExperiment,
    selectedExperiment,
    duplicateFromExperimentId,
    prefill,
    launched,
    setLaunched,
    reset: resetStore,
  } = useRunExperimentStoreShallow((state) => ({
    openRunExperiment: state.openRunExperiment,
    selectedExperiment: state.selectedExperiment,
    duplicateFromExperimentId: state.duplicateFromExperimentId,
    prefill: state.prefill,
    launched: state.launched,
    setLaunched: state.setLaunched,
    reset: state.reset,
  }));
  const sourceId = selectedExperiment || duplicateFromExperimentId;
  const sourceQuery = useGetExperimentDetails(
    openRunExperiment ? sourceId : null,
  );
  const sourceDetail = sourceQuery.data;
  const mode = selectedExperiment
    ? "edit"
    : duplicateFromExperimentId
      ? "duplicate"
      : "create";
  const datasetId =
    datasetParam || searchParams.get("datasetId") || sourceDetail?.dataset_id;
  // options-truth L3 G8 P6: Edit and Duplicate wait for the R1 configs.
  const sourceConfigs = useRunConfigs(datasetId, {
    enabled: Boolean(
      openRunExperiment && sourceId && isSurfaceShown("exp.new") &&
      !sourceDetail?.http_configs?.length,
    ),
  });
  const [datasetName, setDatasetName] = useState(null);
  // The Edit helper names only the ways to run the server's report allows
  // here; the report is read only while Edit experiment is open.
  const runSources = useRunSourceAvailability({
    enabled: Boolean(openRunExperiment && mode === "edit"),
  });

  // a11y #15: while the drawer is open the page behind it is inert (no
  // focus, no screen reader reading); the drawer itself is portalled out.
  useEffect(() => {
    if (!openRunExperiment) return undefined;
    const root = document.getElementById("root");
    if (!root || root.hasAttribute("inert")) return undefined;
    root.setAttribute("inert", "");
    return () => root.removeAttribute("inert");
  }, [openRunExperiment]);

  // One-shot `newExperiment=bakeoff-v2` (R2-SPEC §5.3.1): open New
  // experiment starting from the latest experiment, then drop the param.
  // R4 deep link (`new_experiment=1&source=&entry=&rows=…`, F's
  // runOnRowsHref): open New experiment with the rows / split / prompt
  // preselected. The keys stay in the URL while the drawer is open.
  const deep = parseRunOnRows(searchParams);
  useEffect(() => {
    if (!deep.open || openRunExperiment) return;
    const st = useRunExperimentStore.getState();
    const prefillNow = st.prefill ?? {};
    st.initiateCreateMode({
      prefill: {
        ...prefillNow,
        // options-truth L3 G8 P1: `rows=` may name labels (T5, 12); the
        // form resolves `row_refs` against the rows once they load.
        ...(Array.isArray(deep.rows)
          ? { row_ids: deep.rows, row_refs: deep.rows }
          : {}),
        ...(deep.split ? { split_name: deep.split } : {}),
        ...(deep.datasetVersionId ? { version_id: deep.datasetVersionId } : {}),
        ...(deep.promptId
          ? { prompt_id: deep.promptId, prompt_version: deep.promptVersion }
          : {}),
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deep.open]);
  // Opened from a button: say so in the URL (a reload reopens it).
  useEffect(() => {
    if (!openRunExperiment || selectedExperiment || duplicateFromExperimentId)
      return;
    if (searchParams.get("new_experiment") === "1") return;
    const next = new URLSearchParams(window.location.search);
    next.set("new_experiment", "1");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRunExperiment]);
  // dataset integration FE-0: the ⌘K verbs `newexp.agent`, `newexp.connect`,
  // `newexp.voice` and `newexp.voice-agent` opened Your code, the live
  // connection and the voice card, which have no real path today; they
  // are absent until those options' rows are green (FE-1).
  // `scenarios` (voice) is not an R2 kind: read the raw kind too.
  const { kind: verbR2Kind, dataset: verbDataset } =
    useDatasetKind(datasetParam);
  const verbKind = verbDataset?.kind ?? verbR2Kind;

  const presetParam = searchParams.get("newExperiment");
  useEffect(() => {
    if (presetParam !== START_FROM_PRESET || openRunExperiment) return;
    useRunExperimentStore
      .getState()
      .initiateCreateMode({ prefill: { start_from: START_FROM_PRESET } });
    const next = new URLSearchParams(searchParams);
    next.delete("newExperiment");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetParam]);

  const [formIsDirty, setFormIsDirty] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [draftLost, setDraftLost] = useState(null);
  // IT-R2 G1 P1: a Level 3 draft (not added yet) also holds work, and a
  // layer on top (the trace drawer) takes Escape first.
  const hold = usePickerUi((st) => st.hold);
  const layers = usePickerUi((st) => st.layers);
  const [formKey, setFormKey] = useState(0);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (openRunExperiment) setFormKey((k) => k + 1);
  }, [openRunExperiment, selectedExperiment, duplicateFromExperimentId]);

  const close = () => {
    setFormIsDirty(false);
    setConfirmClose(false);
    {
      const next = new URLSearchParams(window.location.search);
      let changed = false;
      DRAWER_URL_KEYS.forEach((k) => {
        if (next.has(k)) {
          next.delete(k);
          changed = true;
        }
      });
      if (changed) setSearchParams(next, { replace: true });
    }
    usePickerUi.getState().setDeepEntry(null);
    if (selectedExperiment)
      queryClient.invalidateQueries({
        queryKey: ["experiment", selectedExperiment],
      });
    resetStore();
  };
  const requestClose = () =>
    !launched && (formIsDirty || hold) ? setConfirmClose(true) : close();
  const discardText = discardBody({
    names: [...(draftLost?.names ?? []), hold?.name].filter(Boolean),
    simulated: Boolean(draftLost?.simulated || hold?.simulated),
    tried: Boolean(hold?.tried),
  });

  // IT-R1 G1 P5: `Results in · 24/24` once every waiting run is in.
  const resultsIn = Object.values(launched?.resultsIn ?? {});
  const allIn =
    launched &&
    resultsIn.length > 0 &&
    resultsIn.length >= (launched.runs ?? []).length;
  // the interface contract (review M4): the title prints each launched run's one ledger
  // (the experiment detail the tray and the waiting panel already poll).
  const launchedDetail = useExperimentDetail(launched?.experimentId, {
    enabled: Boolean(allIn),
    meta: { errorHandled: true },
  });
  const launchedLedgers = (launched?.runs ?? []).map(
    (r) =>
      (launchedDetail.data?.runs ?? []).find((x) => x.run_id === r.run_id)
        ?.ledger ?? null,
  );
  const inTitle = allIn
    ? launchedTitleOf(resultsIn, {
        rowNoun: verbKind === "task_suite" ? "task" : "row",
        ledgers: launchedLedgers,
      })
    : null;
  const title = launched
    ? allIn
      ? inTitle.title
      : "Waiting for results"
    : mode === "edit"
      ? "Edit experiment"
      : "New experiment";
  const covered = useDrawerCovered(openRunExperiment);

  return (
    <>
      <Drawer
        anchor="right"
        open={openRunExperiment}
        onClose={requestClose}
        PaperProps={{
          // a11y #15: the drawer is a modal dialog named by its title.
          role: "dialog",
          "aria-modal": true,
          "aria-labelledby": "experiment-drawer-title",
          sx: {
            height: "100vh",
            position: "fixed",
            zIndex: 2,
            borderRadius: "8px",
            overflow: "visible",
            // IT-R2 G1 P9: one width on every step and after Add (no jump).
            width: PICKER_DRAWER_WIDTH,
            maxWidth: "100vw",
            p: 0,
            backgroundColor: "transparent",
          },
        }}
        ModalProps={{
          BackdropProps: { style: { backgroundColor: "transparent" } },
          // Escape belongs to the layer on top (its own listener closes it).
          disableEscapeKeyDown: layers > 0,
        }}
        sx={{ zIndex: 1099, ...coveredDrawerSx(covered) }}
      >
        {openRunExperiment ? (
          <AudioPlaybackProvider>
            <Box
              data-testid="experiment-drawer"
              sx={{
                display: "flex",
                flexDirection: "column",
                height: "100%",
                width: "100%",
                bgcolor: "background.paper",
                p: 2,
                gap: 1.5,
                borderRadius: "8px",
              }}
            >
              <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1 }}>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    id="experiment-drawer-title"
                    fontWeight="fontWeightSemiBold"
                    typography="m3"
                    color="text.primary"
                  >
                    {isSurfaceShown("exp.new")
                      ? title
                      : mode === "edit"
                        ? CLASSIC_EXPERIMENT_COPY.editTitle
                        : CLASSIC_EXPERIMENT_COPY.createTitle}
                  </Typography>
                  <HelperText
                    sx={{
                      color: "text.secondary",
                      typography: "s1",
                      fontWeight: "fontWeightRegular",
                    }}
                    text={
                      !isSurfaceShown("exp.new")
                        ? CLASSIC_EXPERIMENT_COPY.classicHelp
                        : launched
                          ? allIn
                            ? inTitle.helper ??
                              "Every row is in and scored. Open the experiment to see what passed and what failed."
                            : launchedHowLine(launched.r4)
                          : mode === "edit"
                            ? editHelpOf({
                                datasetName,
                                availability: runSources,
                              })
                            : STEP1_COPY.subtitle
                    }
                  />
                </Box>
                <Stack direction="row" alignItems="center" gap={1.5}>
                  {/* IT-R3 G1 P1: the run tray docks here while the drawer
                      is open (the floating pill never covers the footer). */}
                  {isSurfaceShown("dataset.tab.experiments") && <RunTrayDock />}
                  <Link
                    href="https://docs.futureagi.com/docs/dataset/features/experiments"
                    underline="hover"
                    target="_blank"
                    rel="noopener noreferrer"
                    typography="s2"
                    fontWeight="fontWeightMedium"
                    sx={{
                      color: "status.info.fg",
                      textDecorationColor: "status.info.fg",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 0.25,
                      whiteSpace: "nowrap",
                    }}
                    aria-label="Learn more about experiments (opens in a new tab)"
                  >
                    Learn more
                    <Iconify icon="mdi:open-in-new" width={14} />
                  </Link>
                  <IconButton
                    onClick={requestClose}
                    aria-label="Close"
                    sx={{ p: 0.25, color: "text.primary" }}
                  >
                    <Iconify icon="solar:close-linear" />
                  </IconButton>
                </Stack>
              </Box>
              <Divider flexItem />
              {/* hook(F01): keep admission feedback within the active drawer. */}
              <CreateAdmissionNotice datasetId={datasetId} />
              {launched ? (
                <WaitingForResultsPanel launched={launched} onDone={close} />
              ) : (
                <ExperimentSourceGate
                  enabled={Boolean(sourceId)}
                  detail={sourceQuery}
                  configs={
                    isSurfaceShown("exp.new") && !sourceDetail?.http_configs?.length
                      ? sourceConfigs : sourceQuery
                  }
                >
                  {datasetId ? (
                    <RunExperimentForm
                      key={formKey}
                      datasetId={datasetId}
                      mode={mode}
                      sourceDetail={sourceDetail}
                      sourceConfigs={sourceConfigs.data ?? null}
                      experimentColumns={experimentColumns}
                      prefill={prefill}
                      setFormIsDirty={setFormIsDirty}
                      onDraftSummary={setDraftLost}
                      onLaunched={setLaunched}
                      onDatasetName={setDatasetName}
                      onSubmitted={() => {
                        onSuccessfulSubmit?.();
                        close();
                      }}
                    />
                  ) : (
                    <RunExperimentFormSkeleton />
                  )}
                </ExperimentSourceGate>
              )}
            </Box>
          </AudioPlaybackProvider>
        ) : null}
      </Drawer>
      <UxConfirmDialog
        open={confirmClose}
        title={
          mode === "edit" ? "Discard your changes?" : "Discard this draft?"
        }
        body={discardText}
        confirmLabel="Discard"
        cancelLabel="Keep editing"
        danger
        onConfirm={close}
        onClose={() => setConfirmClose(false)}
      />
      {isSurfaceShown("exp.new") && <RunActionConfirmHost />}
    </>
  );
};

RunExperiment.propTypes = {
  tabRef: PropTypes.object,
  onSuccessfulSubmit: PropTypes.func,
  experimentColumns: PropTypes.array,
};

export default RunExperiment;
