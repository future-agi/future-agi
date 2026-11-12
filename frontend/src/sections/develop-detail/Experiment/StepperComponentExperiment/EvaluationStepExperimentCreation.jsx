import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Section from "src/components/section";
import { checkSentence, parseChecks } from "../../TaskSuite/checkTypes";
import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useFieldArray, useWatch } from "react-hook-form";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import { useJudgeMediaWarnings } from "src/sections/common/RunSettings/modelCapabilities";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import DraftJudgeDialog from "./DraftJudgeDialog";
import StarterEvaluation from "./StarterEvaluation";
import { starterJudgeModelOf } from "./starterJudge";
import DecisionCallPanel from "src/sections/common/DecisionJudges/DecisionCallPanel";
import { isDecisionModel } from "src/sections/common/DecisionJudges/decisionModels";
import { getVersionedEvalName } from "src/components/run-tests/common";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useEvalDetail } from "src/sections/evals/hooks/useEvalDetail";
import {
  cardChipsOf,
  cardTestBodyOf,
  refusalTextOf,
  namedReferenceColumnOf,
} from "./evalCardModel-g7";
import { useExperimentForm } from "../ExperimentFormContext";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { CLASSIC_EXPERIMENT_COPY } from "src/launch/classicExperimentCopy";
import { useRunEvaluationStore } from "../../states";
import {
  autoMapEvalKeys,
  evalDisplayName,
  mappedValueOnRow,
} from "../variableMapping";
import { tableRequestFor } from "../../DataTab/Versions/versionedSource";
import { toneSx } from "../toneSx";
import { useChangeSetStore } from "../../DataTab/Versions/changeSetStore";
import { useUnsavedSelection } from "../../DataTab/Versions/useUnsavedSelection";
import UnsavedRowsBanner from "../../DataTab/Versions/UnsavedRowsBanner";
import { useSelectedSampleContext } from "../../DataTab/Versions/selectedSample";
import {
  productionEval,
  productionOfSelection,
  retestEvals,
} from "../../DataTab/Versions/retestEvals";
import { ExperimentFormContext } from "../ExperimentFormContext";
import AddEvalGroupButton from "src/sections/common/EvalHandoffs/AddEvalGroupButton";
import {
  emptyReferenceSentence,
  emptyReferenceWarnings,
  foldedEvalNames,
  selectedRowValues,
} from "../../DataTab/Versions/evalCoverage";
import {
  REFERENCE_ROLE,
  mappingWithNames,
  remapReference,
} from "../readsOnSampleModel";

const OUTPUT = "output";
// L5 G1: eval keys that read the run's output / the row's reference.
const OUTPUT_ROLE =
  /^(?:output|response|actual(?:_output)?|completion|generated)$/i;
// REFERENCE_ROLE: readsOnSampleModel (one rule for both steps).
const SKIPPED_NO_REFERENCE = "Skipped · no reference column";
// IT-R2 G5 P1: drawer forms whose re-test defaults were applied (once per
// form + selection; a new New experiment drawer is a new form).
const RETEST_APPLIED = new WeakMap();
const VIRTUAL = {
  output: "Run output",
  prompt_chain: "Prompt chain",
};
// L7c g7 (wiz:card-mapping-preview): a mapping that names no dataset
// column is not sent as written: the one scorer reads it as unmapped. A
// context input reads the policy knowledge base (and the row's expected
// reply, when it has one); an input reads the row's input column.
const CONTEXT_INPUT = /^(?:context|knowledge|kb|policy|documents?)\w*$/i;
const QUESTION_INPUT =
  /^(?:input|question|message|query|request|prompt|instruction)\w*$/i;
const literalMappingNoteOf = (input) =>
  CONTEXT_INPUT.test(input)
    ? "Not a dataset column · reads the policy knowledge base (and the row's expected reply)"
    : QUESTION_INPUT.test(input)
      ? "Not a dataset column · reads the row's input column"
      : "Not a dataset column";
// The eval type a picked eval carries (llm | code | agent), or null.
const evalTypeOfConfig = (c = {}) =>
  c.evalType ??
  c.eval_type ??
  c.evalTemplate?.eval_type ??
  c.evalTemplate?.evalType ??
  null;
const outputTypeLabel = (t) =>
  !t
    ? null
    : t === "pass_fail" || t === "Pass/Fail"
      ? "Pass/fail"
      : t === "percentage" || t === "score"
        ? "Score 0–1"
        : t === "choices"
          ? "Choices"
          : null;

const Pill = ({ children, tone = "neutral", testId }) => (
  <Box
    component="span"
    data-testid={testId}
    sx={{
      display: "inline-flex",
      alignItems: "center",
      gap: 0.25,
      height: 20,
      px: 0.75,
      borderRadius: 0.5,
      typography: "s3",
      fontWeight: "fontWeightMedium",
      whiteSpace: "nowrap",
      ...toneSx(tone === "default" ? "accent" : tone),
    }}
  >
    {children}
  </Box>
);
Pill.propTypes = {
  children: PropTypes.node,
  tone: PropTypes.string,
  testId: PropTypes.string,
};

// The picker names a new eval `task_completion_experiment_15_oct_2026_04_04`;
// the card (and the results columns) read better as "Task completion".
const AUTO_NAME = /^(.+?)_experiment_\d+_[a-z]{3}_\d{4}(_\d+)*$/;
const displayEvalName = (name) => {
  const m = AUTO_NAME.exec(String(name ?? ""));
  if (!m) return name;
  const words = m[1].replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

const iconBtn = {
  width: 26,
  height: 26,
  border: "1px solid",
  borderColor: "divider",
  color: "text.action",
};

/**
 * P4 (dev-9): task checks read as sentences with chips; the raw JSON sits
 * behind `Raw` on the row, in place.
 */
const checksOf = (value) => {
  const list = parseChecks(value);
  return list.length && list.every((c) => c.assert || c.kind) ? list : [];
};

function ChecksValue({ value }) {
  const [raw, setRaw] = useState(false);
  const checks = checksOf(value);
  if (!checks?.length)
    return (
      <Typography typography="s2" color="text.secondary" noWrap>
        {typeof value === "string" ? value : JSON.stringify(value)}
      </Typography>
    );
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      gap={0.5}
      sx={{ minWidth: 0 }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {raw ? (
          <Typography
            component="pre"
            typography="s3"
            sx={{
              m: 0,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontFamily: "monospace",
            }}
          >
            {JSON.stringify(checks, null, 2)}
          </Typography>
        ) : (
          <Stack
            direction="row"
            flexWrap="wrap"
            gap={0.5}
            data-testid="check-sentences"
          >
            {checks.map((c, i) => (
              <Typography
                key={c.key ?? i}
                component="span"
                typography="s3"
                sx={{
                  px: 0.75,
                  py: 0.125,
                  borderRadius: 0.5,
                  bgcolor: "background.neutral",
                  color: "text.primary",
                }}
              >
                {`${c.name ?? c.key ?? "Check"}: ${checkSentence(c)}`}
              </Typography>
            ))}
          </Stack>
        )}
      </Box>
      <Button
        size="small"
        onClick={() => setRaw((v) => !v)}
        sx={{
          minWidth: 0,
          px: 0.5,
          typography: "s3",
          color: "text.secondary",
          flexShrink: 0,
        }}
        aria-pressed={raw}
      >
        Raw
      </Button>
    </Stack>
  );
}
ChecksValue.propTypes = { value: PropTypes.any };

/** One eval card: chips, mapping preview on the sample row, Test. */
/** IT-R3 G5 P3: the rows of the picked split(s) (up to 50), for production. */
function useSplitRows(datasetId, splitIds) {
  const key = (splitIds ?? []).join(",");
  const q = useQuery({
    queryKey: ["experiment-create", "split-rows", datasetId, key],
    queryFn: () => {
      const { url, params } = tableRequestFor({
        datasetId,
        params: {
          filters: JSON.stringify([
            {
              column_id: "system:splits",
              filter_config: {
                filter_type: "categorical",
                filter_op: "in",
                filter_value: splitIds,
              },
            },
          ]),
          page_size: 50,
          current_page_index: 0,
        },
      });
      return axios.get(url, { params });
    },
    select: (res) => res?.data?.result?.table ?? [],
    enabled: Boolean(datasetId) && Boolean(key),
    staleTime: 30_000,
  });
  return q.data ?? [];
}

const EvalCard = ({
  item,
  onEdit,
  onRemove,
  warning,
  includes = [],
  testBlockedReason,
}) => {
  const { datasetId, sampleRow, sampleRowLabel, columns } = useExperimentForm();
  const mapping = item.config?.mapping ?? item.mapping ?? {};
  const outputCol = (columns ?? []).find(
    (c) =>
      c.role === "output" ||
      String(c.origin_type).toLowerCase() === "run_prompt",
  );
  const sampleOutput =
    (outputCol && mappedValueOnRow(sampleRow, outputCol.id)) ?? "";
  const [testOpen, setTestOpen] = useState(false);
  const [output, setOutput] = useState(null);
  const [result, setResult] = useState(null);
  // L7c g7 (wiz:card-chips): a card added from the picker, a group or a
  // draft carries no chips of its own: they come from its template.
  const templateId = item.templateId ?? item.template_id ?? item.evalId;
  const needsMeta =
    !(item.outputType ?? item.output_type) || item.version == null;
  const { data: detail } = useEvalDetail(needsMeta ? templateId : null);
  const chips = cardChipsOf(item, detail);
  // L7c g7 (test:card, editor:allow-na, editor:error-localization,
  // editor:few-shot): the card's Test sends the card's whole definition
  // (bar, prompt, N/A switch, error localization, few-shot, agent options),
  // so the one scorer scores what the run will.
  const test = useMutation({
    mutationFn: (args) =>
      axios.post(endpoints.develop.eval.evalPlayground, cardTestBodyOf(args)),
    // The card says why the test did not run (a refused judge, …); no
    // second, generic toast.
    meta: { errorHandled: true },
    onSuccess: (res) => setResult({ ok: true, data: res?.data?.result }),
    onError: (err) =>
      setResult({
        ok: false,
        message: refusalTextOf(
          getRequestErrorMessage(err, "The test did not run"),
          err?.response?.data?.code ?? null,
        ),
        // W1: the gateway's refusal of a decision call (status, code).
        decision: err?.response?.data?.decision ?? null,
      }),
  });
  const outputText = output ?? sampleOutput;
  const runTest = () => {
    if (!isSurfaceShown("exp.new")) return;
    const resolved = Object.fromEntries(
      Object.entries(mapping).map(([k, v]) => [
        k,
        v === OUTPUT ? outputText : mappedValueOnRow(sampleRow, v) ?? "",
      ]),
    );
    setResult(null);
    const bar = Number(
      item.config?.threshold ??
        item.passThreshold ??
        item.config?.pass_threshold,
    );
    test.mutate({
      templateId,
      model: item.model,
      mapping: resolved,
      columnMapping: mapping,
      threshold: Number.isFinite(bar) ? bar : null,
      outputType: item.outputType ?? item.output_type ?? null,
      datasetId,
      rowId: sampleRow?.row_id,
      output: outputText,
      decision: isDecisionModel(item.model)
        ? item.config?.decision ?? null
        : null,
      instructions: item.config?.rule_prompt ?? null,
      card: item,
    });
  };
  const columnName = (v) =>
    VIRTUAL[v] ?? (columns ?? []).find((c) => c.id === v)?.name ?? v;
  const display = result?.data?.output?.display;
  const verdict = result?.data?.output?.passed;
  const passed =
    verdict === true ||
    (verdict == null && (display === "Passed" || display === "Pass"));
  const typeLabel = outputTypeLabel(chips.outputType);

  return (
    <Section
      variant="panel"
      data-testid={`eval-card-${item.name}`}
      sx={{ "& > .MuiBox-root": { p: 0 } }}
    >
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, px: 1.5, py: 1 }}
      >
        <Box
          sx={{
            minWidth: 0,
            // The production card hides its hint, so its name keeps its width.
            flexShrink: item.hint && !item.fromProduction ? 1 : 0,
          }}
        >
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            noWrap
            sx={{ maxWidth: 200 }}
          >
            {item.name}
          </Typography>
          {item.hint && !item.fromProduction ? (
            // IT-R2 G5 P1: why this check is here (a re-test of imports).
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="eval-hint"
            >
              {item.hint}
            </Typography>
          ) : null}
        </Box>
        {/* F67 owns only the Default badge, not the unproven sibling chips. */}
        {isSurfaceShown("dataset.experimentDefaults") && item.isDefault ? (
          <CustomTooltip
            show
            title="Use in new experiments is on for this evaluation on the dataset"
            size="small"
            type="black"
          >
            <span>
              <Pill tone="default" testId="eval-default-chip">
                Default
              </Pill>
            </span>
          </CustomTooltip>
        ) : null}
        {/* The chips wrap at 1280 instead of squeezing the name or pushing
            Edit / Remove past the card's edge. */}
        {isSurfaceShown("exp.new.evalCardChips") ? (
          <Box
            data-testid="eval-card-chips"
            sx={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              columnGap: 1,
              rowGap: 0.5,
              flex: 1,
              minWidth: 0,
            }}
          >
            {item.fromProduction ? (
              // IT-R3 G5 P3: the check that flagged these rows, added for you
              // and kept while these rows are in the run.
              <CustomTooltip
                show
                title={`Added because these rows failed ${item.name} in production. It scores the re-test the same way (a judge; no expected answer needed).`}
                size="small"
                type="black"
              >
                <span>
                  <Pill tone="default" testId="eval-auto-chip">
                    <Iconify icon="mdi:lock-outline" width={12} />
                    {`Auto · these rows failed ${item.name}`}
                  </Pill>
                </span>
              </CustomTooltip>
            ) : null}
            {typeLabel ? <Pill>{typeLabel}</Pill> : null}
            {chips.higherIsBetter != null ? (
              <Pill>
                <DirectionIcon
                  higherIsBetter={chips.higherIsBetter}
                  size={12}
                />
                {chips.higherIsBetter ? "Higher is better" : "Lower is better"}
              </Pill>
            ) : null}
            {chips.passAt != null ? (
              <Pill testId="eval-pass-at-chip">
                {`Pass at ≥ ${Number(chips.passAt).toFixed(2)}${
                  chips.judge ? ` · judge ${chips.judge}` : ""
                }`}
              </Pill>
            ) : chips.judge ? (
              <Pill testId="eval-judge-chip">{`judge ${chips.judge}`}</Pill>
            ) : null}
            {chips.version ? <Pill>{chips.version}</Pill> : null}
            {isDecisionModel(item.model) ? (
              <Pill testId="eval-decision-chip">
                {`Decision · ${item.model}${
                  item.config?.decision?.question_type
                    ? ` · ${item.config.decision.question_type}`
                    : ""
                }`}
              </Pill>
            ) : null}
          </Box>
        ) : (
          <Box sx={{ flex: 1 }} />
        )}
        {isSurfaceShown("exp.new") && (
          <CustomTooltip
            show={Boolean(testBlockedReason || !sampleRow)}
            title={testBlockedReason ?? "No row to test on yet"}
            size="small"
            type="black"
          >
            <span>
              <Button
                size="small"
                variant="text"
                disabled={!sampleRow || Boolean(testBlockedReason)}
                startIcon={
                  <Iconify icon="mdi:play-circle-outline" width={16} />
                }
                onClick={() => setTestOpen((v) => !v)}
                sx={{ px: 1, whiteSpace: "nowrap" }}
              >
                Test on{" "}
                {testBlockedReason ? "a row" : sampleRowLabel || "a row"}
              </Button>
            </span>
          </CustomTooltip>
        )}
        <CustomTooltip show title="Edit" size="small" type="black">
          <IconButton
            size="small"
            onClick={onEdit}
            sx={iconBtn}
            aria-label="Edit evaluation"
          >
            <SvgColor
              src="/assets/icons/ic_edit.svg"
              sx={{ width: 16, height: 16 }}
            />
          </IconButton>
        </CustomTooltip>
        {item.fromProduction ? null : (
          <CustomTooltip show title="Remove" size="small" type="black">
            <IconButton
              size="small"
              onClick={onRemove}
              sx={iconBtn}
              aria-label="Remove evaluation"
            >
              <SvgColor
                src="/assets/icons/ic_delete.svg"
                sx={{ width: 16, height: 16 }}
              />
            </IconButton>
          </CustomTooltip>
        )}
      </Box>

      {warning ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          data-testid="eval-empty-reference"
          sx={{
            px: 1.5,
            py: 0.5,
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "rgba(255, 171, 0, 0.10)",
          }}
        >
          <Iconify
            icon="mdi:alert-outline"
            width={16}
            sx={{ color: "warning.dark", flexShrink: 0 }}
          />
          <Typography typography="s2" sx={{ flex: 1, minWidth: 0 }}>
            {emptyReferenceSentence(warning)}
          </Typography>
          <Button
            size="small"
            color="error"
            onClick={onRemove}
            sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
          >
            {`Remove ${warning.name}`}
          </Button>
        </Stack>
      ) : null}

      {isSurfaceShown("exp.new.readsTable") && Object.keys(mapping).length ? (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "120px 150px minmax(0, 1fr)",
            borderTop: "1px solid",
            borderColor: "divider",
            "& > *": {
              px: 1.5,
              py: 0.5,
              minWidth: 0,
              borderBottom: "1px solid",
              borderColor: "divider",
            },
            "& > *:nth-last-of-type(-n+3)": { borderBottom: "none" },
          }}
        >
          {Object.entries(mapping).map(([key, v]) => {
            const isColumn =
              v === OUTPUT ||
              Boolean(VIRTUAL[v]) ||
              (columns ?? []).some((c) => c.id === String(v).split("::")[0]);
            const value =
              v === OUTPUT || !isColumn ? null : mappedValueOnRow(sampleRow, v);
            return (
              <React.Fragment key={key}>
                <Typography
                  noWrap
                  sx={{ fontFamily: "monospace", fontSize: 12 }}
                >
                  {key}
                </Typography>
                <Typography
                  typography="s2"
                  fontWeight="fontWeightMedium"
                  noWrap
                >
                  {v == null ? "None" : columnName(v)}
                </Typography>
                {v == null ? (
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    noWrap
                    data-testid="eval-skipped-no-reference"
                  >
                    {SKIPPED_NO_REFERENCE}
                  </Typography>
                ) : isColumn &&
                  v !== OUTPUT &&
                  value &&
                  checksOf(value).length ? (
                  <ChecksValue value={value} />
                ) : (
                  <Typography
                    typography="s2"
                    color={
                      v === OUTPUT || !value
                        ? "text.disabled"
                        : "text.secondary"
                    }
                    noWrap
                  >
                    {v === OUTPUT
                      ? "Produced by each run"
                      : !isColumn
                        ? literalMappingNoteOf(key)
                        : value || `empty on ${sampleRowLabel || "this row"}`}
                  </Typography>
                )}
              </React.Fragment>
            );
          })}
        </Box>
      ) : null}

      {isSurfaceShown("exp.new") && includes.length ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          data-testid="eval-includes"
          sx={{
            px: 1.5,
            py: 0.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ flex: 1, minWidth: 0 }}
          >
            {`${includes.map((x) => x.name).join(", ")} ${includes.length === 1 ? "is" : "are"} one of these checks and also ${includes.length === 1 ? "gets its" : "get their"} own column in results.`}
          </Typography>
          {includes.map((x) => (
            <Button
              key={x.name}
              size="small"
              color="inherit"
              onClick={x.onRemove}
              sx={{ whiteSpace: "nowrap", flexShrink: 0, typography: "s3" }}
            >
              {includes.length === 1
                ? "Remove its column"
                : `Remove ${x.name} column`}
            </Button>
          ))}
        </Stack>
      ) : null}

      {isSurfaceShown("exp.new") && testOpen ? (
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.default",
            px: 1.5,
            py: 1.25,
            display: "flex",
            flexDirection: "column",
            gap: 1,
          }}
        >
          <TextField
            label={
              outputCol && output == null
                ? `Sample output · ${outputCol.name} on ${sampleRowLabel}`
                : "Sample output"
            }
            size="small"
            multiline
            minRows={2}
            maxRows={5}
            fullWidth
            value={outputText}
            placeholder="Paste an output to test with"
            onChange={(e) => setOutput(e.target.value)}
            sx={{ bgcolor: "background.paper" }}
          />
          <Box sx={{ display: "flex", alignItems: "center", gap: 1.25 }}>
            <LoadingButton
              size="small"
              variant="outlined"
              loading={test.isPending}
              onClick={runTest}
              data-testid="eval-test-run"
            >
              Run test
            </LoadingButton>
            {result?.ok ? (
              <>
                <Pill
                  tone={
                    result.data?.output?.skipped
                      ? "neutral"
                      : verdict != null || typeLabel === "Pass/fail"
                        ? passed
                          ? "pass"
                          : "fail"
                        : "neutral"
                  }
                  testId="eval-test-score"
                >
                  {verdict != null &&
                  typeLabel !== "Pass/fail" &&
                  !result.data?.output?.skipped
                    ? `${display} · ${passed ? "Pass" : "Fail"}`
                    : display}
                </Pill>
                <Typography typography="s3" color="text.secondary">
                  {[
                    result.data?.metadata?.threshold != null &&
                      `pass ≥ ${result.data.metadata.threshold}`,
                    result.data?.metadata?.judge_model &&
                      `judge ${result.data.metadata.judge_model}`,
                    result.data?.metadata?.latency_ms != null &&
                      `${(result.data.metadata.latency_ms / 1000).toFixed(1)} s`,
                    result.data?.metadata?.cost_usd != null &&
                      `$${result.data.metadata.cost_usd}`,
                    result.data?.metadata?.judged &&
                      `judged ${result.data.metadata.judged}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </Typography>
              </>
            ) : result && !result.ok ? (
              <Typography typography="s2" color="error.main">
                {result.message}
              </Typography>
            ) : (
              <Typography typography="s3" color="text.secondary">
                Scores this output on {sampleRowLabel} with the mapping above.
                Nothing is saved.
              </Typography>
            )}
          </Box>
          {result?.ok && result.data?.reason ? (
            <Typography typography="s2" color="text.primary">
              {result.data.reason}
            </Typography>
          ) : null}
          {result?.ok && result.data?.metadata?.decision ? (
            <DecisionCallPanel decision={result.data.metadata.decision} />
          ) : null}
          {result && !result.ok && result.decision ? (
            <DecisionCallPanel decision={result.decision} />
          ) : null}
        </Box>
      ) : null}
    </Section>
  );
};
EvalCard.propTypes = {
  item: PropTypes.object.isRequired,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  warning: PropTypes.object,
  includes: PropTypes.array,
  testBlockedReason: PropTypes.string,
};

/**
 * Step 3 · Evaluations (PRODUCT-SPEC §3.8). Evaluations with `Use in new
 * experiments` on come preselected with a `Default` chip; `Manage` opens
 * the dataset's Evaluate drawer. Every run is scored by the same set; at
 * least one is required (C29).
 */
const EvaluationStepExperimentCreation = ({
  control,
  allColumns,
  showErrors = false,
}) => {
  const { dataset: datasetParam } = useParams();
  // IT-R2 G5 P2 (S8): the selected rows first; Sample = the first of them.
  const sampleCtx = useSelectedSampleContext(control);
  const { datasetId, columns, rows, exampleCount, sampleRow, versions } =
    sampleCtx;
  // The dataset version the experiment runs on (the draft judge reads it).
  const datasetVersionId = useWatch({ control, name: "datasetVersionId" });
  const runVersion = useMemo(() => {
    const v = (versions ?? []).find((x) => x.id === datasetVersionId);
    return v ? { id: v.id, number: v.number } : null;
  }, [versions, datasetVersionId]);
  const setOpenRunEvaluation = useRunEvaluationStore(
    (s) => s.setOpenRunEvaluation,
  );
  // L7c g7 (test:editor): the editor's Test reads the column the card's
  // Test judges (the dataset's reply column) for Output on the row on
  // screen; a run scores its own reply.
  const sampleOutputCol = (columns ?? []).find(
    (c) =>
      c.role === "output" ||
      String(c.origin_type).toLowerCase() === "run_prompt",
  );
  const sampleFrom = sampleOutputCol?.id ?? null;
  const sampleName = sampleOutputCol?.name ?? null;
  const experimentVirtualColumns = useMemo(
    () => [
      {
        field: "output",
        headerName: "Output",
        dataType: "text",
        ...(sampleFrom ? { sampleFrom, sampleName } : {}),
      },
      { field: "prompt_chain", headerName: "Prompt Chain", dataType: "text" },
    ],
    [sampleFrom, sampleName],
  );
  const sourceColumns = [
    ...experimentVirtualColumns,
    ...(allColumns?.length
      ? allColumns
      : (columns ?? []).map((c) => ({
          field: c.id,
          headerName: c.name,
          dataType: c.data_type,
        }))),
  ];
  const [pickerOpen, setPickerOpen] = useState(false);
  const [editingEval, setEditingEval] = useState(null);
  // W1-2 (LS-01): Draft a judge from the expected column (when one is set).
  const [draftOpen, setDraftOpen] = useState(false);
  const {
    fields: evalFields,
    append,
    remove,
    update,
    replace,
    insert,
  } = useFieldArray({
    control,
    name: "userEvalMetrics",
  });
  const evalValues = useWatch({ control, name: "userEvalMetrics" });
  const judgeMedia = useJudgeMediaWarnings(evalValues, columns); // W6 C3
  const referenceColumnId = useWatch({ control, name: "columnId" });
  const splitIdsInScope = useWatch({ control, name: "splitIds" }) ?? [];

  // L5 G1: Expected answers on the Data step decides each reference-role
  // key: changing it remaps every key that pointed at the previous column
  // (None = null, "Skipped · no reference column"); the create body then
  // carries the chosen column in each eval's mapping.
  const referenceColumn = useMemo(() => {
    if (!referenceColumnId) return null;
    const c = sourceColumns.find((x) => x.field === referenceColumnId);
    return c ? { id: c.field, name: c.headerName ?? c.field } : null;
    // sourceColumns is rebuilt each render; the reference id decides.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [referenceColumnId, allColumns, columns]);
  const prevReference = useRef(referenceColumnId);
  useEffect(() => {
    const prev = prevReference.current;
    prevReference.current = referenceColumnId;
    if ((prev ?? null) === (referenceColumnId ?? null)) return;
    const evals = evalValues ?? [];
    const next = remapReference(evals, prev, referenceColumnId);
    next.forEach((f, i) => {
      if (f !== evals[i]) update(i, f);
    });
    // Only Expected answers drives this; evalValues are read at that time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [referenceColumnId]);

  const handleAddEvaluation = (evalConfig) => {
    // The picker maps variables to column names; the API takes column ids.
    const translated = {};
    // IT-R3 G5 P3: `Add` on an existing eval skips the form; its keys map
    // themselves (output → Run output, expected → the expected column,
    // the rest by name). The pencil edits them.
    const autoMapped =
      !editingEval && !Object.keys(evalConfig.mapping || {}).length
        ? autoMapEvalKeys(
            evalConfig.requiredKeys ??
              evalConfig.evalTemplate?.requiredKeys ??
              [],
            columns ?? [],
            { referenceColumnId },
          )
        : null;
    if (autoMapped) Object.assign(translated, autoMapped);
    Object.entries(evalConfig.mapping || {}).forEach(([variable, colName]) => {
      const col = sourceColumns.find(
        (c) =>
          c.headerName === colName || c.field === colName || c.name === colName,
      );
      translated[variable] = col?.field || colName;
    });
    // L7c g7 (wiz:add-evaluation): a code check whose definition names the
    // column it compares with (Tool call accuracy: expected_tool_calls)
    // reads that column, unless Expected answers is None (a judge's
    // reference keys follow Expected answers).
    const isCodeCheck =
      (evalConfig.evalType ??
        evalConfig.eval_type ??
        evalConfig.evalTemplate?.eval_type) === "code";
    const namedReference =
      !editingEval && isCodeCheck && referenceColumnId !== null
        ? namedReferenceColumnOf(
            evalConfig.description ?? evalConfig.evalTemplate?.description,
            columns ?? [],
          )
        : null;
    // L5 G1: a created eval's output-role key reads the run's output unless
    // the form mapped it; reference-role keys follow Expected answers.
    [
      ...(evalConfig.requiredKeys ??
        evalConfig.evalTemplate?.requiredKeys ??
        []),
      ...Object.keys(translated),
    ].forEach((k) => {
      if (OUTPUT_ROLE.test(k) && !translated[k]) translated[k] = OUTPUT;
      if (!REFERENCE_ROLE.test(k)) return;
      if (namedReference && !evalConfig.mapping?.[k]) {
        translated[k] = namedReference.id;
        return;
      }
      // Expected answers = None (null): the eval is skipped on every row.
      if (referenceColumnId === null) translated[k] = null;
      else if (!translated[k] && referenceColumnId)
        translated[k] = referenceColumnId;
    });
    const templateConfig =
      evalConfig.config || evalConfig.evalTemplate?.config || {};
    // options-truth L2 G8 (P6): the card re-reads what the editor saved
    // (threshold, judge model), and the run scores with the same values.
    const savedThreshold = Number(
      evalConfig.pass_threshold ?? templateConfig.pass_threshold,
    );
    const thresholdOf = Number.isFinite(savedThreshold)
      ? { threshold: savedThreshold, pass_threshold: savedThreshold }
      : {};
    // L7b G5: the editor's knobs travel on the card's config, and the run
    // scores by them: a composite's per-binding weights, error localization
    // and the eval agent's options (run_config).
    const weightOverrides =
      evalConfig.templateType === "composite"
        ? evalConfig.compositeWeightOverrides ??
          evalConfig.composite_weight_overrides ??
          null
        : null;
    const errorLocalizer = Boolean(
      evalConfig.errorLocalizer ?? evalConfig.error_localizer_enabled,
    );
    const runConfig =
      evalConfig.run_config ??
      (evalConfig.agent_mode != null
        ? {
            agent_mode: evalConfig.agent_mode,
            check_internet: Boolean(evalConfig.check_internet),
            summary: evalConfig.summary ?? null,
            knowledge_bases: evalConfig.knowledge_bases ?? [],
            data_injection: evalConfig.data_injection ?? null,
          }
        : null);
    const knobConfig = {
      ...(weightOverrides
        ? { composite_weight_overrides: weightOverrides }
        : {}),
      ...(errorLocalizer ? { error_localizer_enabled: true } : {}),
      ...(runConfig ? { run_config: runConfig } : {}),
    };
    const entry = {
      evalId: evalConfig.templateId,
      evalTemplateName: evalConfig.name,
      templateId: evalConfig.templateId,
      mapping: translated,
      model: evalConfig.model,
      ...(Number.isFinite(savedThreshold)
        ? { passThreshold: savedThreshold }
        : {}),
      config: {
        ...templateConfig,
        ...thresholdOf,
        ...knobConfig,
        mapping: translated,
      },
      ...(errorLocalizer ? { errorLocalizer: true } : {}),
      templateDetails: evalConfig.evalTemplate,
      templateType: evalConfig.templateType,
      // L7c g7 (wiz:eval-step): the eval's type, so the one estimator
      // prices a code eval (Tool call accuracy) at no judge call, as the
      // run engine bills it.
      ...(evalTypeOfConfig(evalConfig)
        ? { evalType: evalTypeOfConfig(evalConfig) }
        : {}),
      // W1-3: the version the card is pinned to ("V4 · pinned").
      ...(evalConfig.versionPinLabel
        ? { version: evalConfig.versionPinLabel }
        : {}),
      outputType:
        evalConfig.evalTemplate?.output_type ??
        evalConfig.evalTemplate?.config?.output ??
        null,
      requiredKeys:
        evalConfig.evalTemplate?.requiredKeys ||
        templateConfig.requiredKeys ||
        [],
      ...(weightOverrides ? { compositeWeightOverrides: weightOverrides } : {}),
      // integration S13b: a typed eval's judge calls a row, for the estimate.
      ...(evalConfig.judgeModels
        ? { judgeModels: evalConfig.judgeModels }
        : {}),
    };
    if (editingEval) {
      const idx = evalFields.findIndex(
        (f) =>
          (f.actualEvalCreatedId || f.evalId || f.id) ===
          editingEval.userEvalId,
      );
      if (idx !== -1)
        update(idx, {
          ...evalFields[idx],
          ...entry,
          name: evalDisplayName(displayEvalName(evalConfig.name)),
        });
    } else {
      append({
        ...entry,
        name: getVersionedEvalName(
          evalDisplayName(displayEvalName(evalConfig.name)),
          evalFields,
          evalConfig.templateId,
        ),
      });
    }
    setEditingEval(null);
    setPickerOpen(false);
  };

  const handleEditEval = (item) => {
    setEditingEval({
      id: item.templateId || item.template_id || item.evalId || item.id,
      userEvalId: item.actualEvalCreatedId || item.evalId || item.id,
      name: item.name || item.evalTemplateName,
      templateType: item.templateType || item.template_type,
      // W1-5: the editor shows column names, never raw column ids.
      mapping: mappingWithNames(
        item.config?.mapping || item.mapping,
        sourceColumns.slice(experimentVirtualColumns.length),
      ),
      model: item.model || item.selected_model,
      run_config: item.config,
      compositeWeightOverrides:
        item.compositeWeightOverrides ||
        item.composite_weight_overrides ||
        item.config?.composite_weight_overrides,
      composite_weight_overrides:
        item.compositeWeightOverrides ||
        item.composite_weight_overrides ||
        item.config?.composite_weight_overrides,
    });
    setPickerOpen(true);
  };

  const missing = showErrors && !evalFields.length;
  // IT-R1 G5 P6: what the selected rows hold for each eval, said once.
  const unsaved = useUnsavedSelection(control, {
    applyEvals: (fn) => replace(fn(evalValues ?? [])),
  });
  const rowIds = useWatch({ control, name: "rowIds" }) ?? [];
  const ops = useChangeSetStore((st) => st.sets?.[datasetId]?.ops);
  const selected = selectedRowValues({ rowIds, ops, rows, exampleCount });
  // P1: saved rows imported from production failures (no save on the way
  // here): the check that failed there replaces the dataset defaults once.
  // IT-R3 G5 P3: a split of them (`prod-failures-oct-18`) counts too.
  const splitRows = useSplitRows(
    datasetId,
    rowIds.length ? [] : splitIdsInScope,
  );
  const production =
    productionOfSelection({ rowIds, ops, rows }) ??
    (splitRows.length
      ? productionOfSelection({
          rowIds: splitRows.map((r) => r.row_id),
          rows: splitRows,
        })
      : null);
  const retestKey = production
    ? `${datasetId}|${rowIds.join(",")}|${splitIdsInScope.join(",")}`
    : null;
  useEffect(() => {
    if (!isSurfaceShown("exp.new") || !retestKey) return;
    const applied = RETEST_APPLIED.get(control) ?? new Set();
    RETEST_APPLIED.set(control, applied);
    if (applied.has(retestKey)) return;
    applied.add(retestKey);
    const current = evalValues ?? [];
    if (!current.every((e) => e?.isDefault)) return;
    const next = retestEvals(current, {
      production,
      columns,
      referenceColumnId,
      selected,
    });
    if (next !== current) replace(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retestKey, Boolean(selected)]);
  // IT-R3 G5 P3: Remove says what it removed, with Undo.
  // L7c g7 (wiz:card-remove): the Undo sits in the step itself (a toast's
  // Undo lay under the drawer's backdrop, so a click there asked to discard
  // the draft); Ctrl+Z / ⌘Z still undoes while it shows.
  const [removed, setRemoved] = useState(null);
  const undoRemove = () => {
    if (!removed) return;
    if (removed.value)
      insert(Math.min(removed.index, evalFields.length), removed.value);
    setRemoved(null);
  };
  useEffect(() => {
    if (!isSurfaceShown("exp.new") || !removed) return undefined;
    const t = setTimeout(() => setRemoved(null), 8000);
    const onKey = (e) => {
      const el = document.activeElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
      if (
        (e.metaKey || e.ctrlKey) &&
        !e.shiftKey &&
        String(e.key).toLowerCase() === "z"
      ) {
        e.preventDefault();
        e.stopPropagation();
        undoRemove();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      clearTimeout(t);
      window.removeEventListener("keydown", onKey, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [removed]);
  const removeEval = (i) => {
    const value = (evalValues ?? [])[i];
    const name = value?.name ?? evalFields[i]?.name ?? "the evaluation";
    remove(i);
    if (isSurfaceShown("exp.new")) setRemoved({ index: i, value, name });
  };
  const productionAdded = Boolean(
    production &&
      (evalValues ?? []).some(
        (e) =>
          (e?.templateId ?? e?.template_id ?? e?.evalId) ===
            production.template_id ||
          String(e?.name ?? "").toLowerCase() ===
            String(production.criterion).toLowerCase(),
      ),
  );
  const addProductionEval = () => {
    if (!production || productionAdded) return;
    append(productionEval(production, columns));
  };
  const flaggedLine =
    isSurfaceShown("exp.new") && production && !productionAdded ? (
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        data-testid="eval-flagged-line"
        sx={{
          px: 1.25,
          py: 0.75,
          borderRadius: 0.5,
          bgcolor: "background.neutral",
        }}
      >
        <Iconify
          icon="mdi:flag-outline"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography typography="s2" sx={{ flex: 1, minWidth: 0 }}>
          {`Used to flag these rows: ${production.criterion}`}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={addProductionEval}
          sx={{ flexShrink: 0 }}
        >
          Add
        </Button>
      </Stack>
    ) : null;
  const warnings = emptyReferenceWarnings({
    evals: evalFields,
    columns,
    referenceColumnId,
    selected,
  });
  // L5 G1: with Expected answers = None a reference eval is skipped, so it
  // does not need the column filled in either.
  const skipsReference = (i) =>
    Object.entries(
      evalValues?.[i]?.config?.mapping ?? evalValues?.[i]?.mapping ?? {},
    ).some(([k, v]) => REFERENCE_ROLE.test(k) && v == null);
  const warningOf = (i) =>
    skipsReference(i) ? null : warnings.find((w) => w.evalIndex === i) ?? null;
  // An eval that is also one of another eval's checks shows once, inside.
  const cards = evalFields.map((f) => {
    const mapping = f.config?.mapping ?? f.mapping ?? {};
    const checkNames = Object.values(mapping).flatMap((v) =>
      v === OUTPUT
        ? []
        : checksOf(mappedValueOnRow(sampleRow, v)).map((c) => c.name),
    );
    return { name: f.name, checkNames };
  });
  const folded = isSurfaceShown("exp.new") ? foldedEvalNames(cards) : {};
  const testBlockedReason =
    sampleCtx.sampleIsPending ||
    (rowIds.length && !rowIds.includes(sampleRow?.row_id))
      ? "Save the selected rows first; Test then runs on the first of them."
      : null;
  // P4: a kept seeded local code run against a task suite with an
  // Environment: its end-state checks read the sandbox state it recorded.
  const { dataset: dsEntry } = useDatasetKind(datasetId);
  const promptConfig = useWatch({ control, name: "promptConfig" }) ?? [];
  const localCode = promptConfig.some(
    (c) => c?.r4?.kind === "code" && c?.r4?.mode === "local",
  );
  // JRN-EXP-NO-PROMPT-DEADEND: an experiment needs an evaluation, and a
  // first one has none to pick, so the empty step offers one ready judge,
  // judged by the model the prompt run uses (starterJudge.js).
  const starterModel = isSurfaceShown("exp.new")
    ? starterJudgeModelOf(promptConfig)
    : null;

  return (
    <ExperimentFormContext.Provider value={sampleCtx}>
      <Stack spacing={1.5}>
        {isSurfaceShown("exp.new") && (
          <UnsavedRowsBanner
            sentence={unsaved.sentence}
            saveLabel={unsaved.saveLabel}
            onSave={unsaved.saveAndContinue}
            note={unsaved.note}
            onEditNote={unsaved.editNote}
            saving={unsaved.saving}
          />
        )}
        {isSurfaceShown("exp.new") && localCode && dsEntry?.environment ? (
          <Stack
            direction="row"
            gap={0.75}
            data-testid="end-state-note"
            sx={{
              px: 1,
              py: 0.75,
              borderRadius: 0.5,
              bgcolor: "background.neutral",
            }}
          >
            <Iconify
              icon="mdi:information-outline"
              width={16}
              sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }}
            />
            <Typography typography="s2" color="text.secondary">
              End-state checks read the sandbox state these runs recorded.
            </Typography>
          </Stack>
        ) : null}
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Box>
            <Typography typography="s1_2" fontWeight="fontWeightMedium">
              Evaluations
            </Typography>
            <Typography typography="s2" color="text.secondary">
              {isSurfaceShown("dataset.experimentDefaults")
                ? "Every run is scored by the same evaluations. Dataset defaults are preselected."
                : CLASSIC_EXPERIMENT_COPY.evaluationHelp}
            </Typography>
          </Box>
          <Box sx={{ flex: 1 }} />
          {isSurfaceShown("exp.new") && datasetParam ? (
            <Button
              size="small"
              variant="text"
              onClick={() => setOpenRunEvaluation(true)}
              startIcon={<Iconify icon="mdi:tune-variant" width={16} />}
            >
              Manage
            </Button>
          ) : null}
          <Button
            variant="outlined"
            color="primary"
            size="small"
            onClick={() => {
              setEditingEval(null);
              setPickerOpen(true);
            }}
            startIcon={
              <SvgColor
                width={16}
                src="/assets/icons/action_buttons/ic_add.svg"
              />
            }
            sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
          >
            Add evaluation
          </Button>
          <AddEvalGroupButton
            existing={evalFields}
            onAdd={handleAddEvaluation}
          />
          {isSurfaceShown("exp.new") && referenceColumn ? (
            <Button
              variant="text"
              size="small"
              onClick={() => setDraftOpen(true)}
              startIcon={
                <Iconify icon="mdi:text-box-check-outline" width={16} />
              }
              sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
              data-testid="draft-judge-open"
            >
              {`Draft a judge from ${referenceColumn.name}`}
            </Button>
          ) : null}
        </Box>

        {
          isSurfaceShown("exp.new") && judgeMedia.map((w) => <Typography key={w.text} typography="s2" color="error.main" data-testid="judge-media-warning">{w.text}</Typography>) /* prettier-ignore */
        }
        {flaggedLine}
        {isSurfaceShown("exp.new") && removed ? (
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            role="status"
            data-testid="eval-removed-line"
            sx={{
              px: 1.25,
              py: 0.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
              bgcolor: "background.neutral",
            }}
          >
            <Typography typography="s2" sx={{ flex: 1 }}>
              {`Removed ${removed.name}`}
            </Typography>
            <Button
              size="small"
              onClick={undoRemove}
              data-testid="eval-removed-undo"
            >
              Undo
            </Button>
          </Stack>
        ) : null}
        {evalFields.length ? (
          <Stack spacing={1}>
            {evalFields.map((item, i) =>
              folded[item.name] ? null : (
                <EvalCard
                  key={item.id}
                  item={item}
                  onEdit={() => handleEditEval(item)}
                  onRemove={() => removeEval(i)}
                  warning={isSurfaceShown("exp.new") ? warningOf(i) : null}
                  testBlockedReason={testBlockedReason}
                  includes={evalFields
                    .map((f, j) => ({ f, j }))
                    .filter(({ f }) => folded[f.name] === item.name)
                    .map(({ f, j }) => ({
                      name: f.name,
                      onRemove: () => removeEval(j),
                    }))}
                />
              ),
            )}
          </Stack>
        ) : (
          <Box
            sx={{
              border: "1px dashed",
              borderColor: missing ? "error.main" : "divider",
              borderRadius: 0.5,
              p: 2,
              textAlign: "center",
            }}
          >
            <Typography
              typography="s2"
              color={missing ? "error.main" : "text.secondary"}
            >
              {isSurfaceShown("dataset.experimentDefaults")
                ? 'Add at least one evaluation. Evaluations with "Use in new experiments" on are preselected.'
                : CLASSIC_EXPERIMENT_COPY.evaluationRequired}
            </Typography>
            {starterModel ? (
              <StarterEvaluation
                model={starterModel}
                onAdd={handleAddEvaluation}
              />
            ) : null}
          </Box>
        )}

        {isSurfaceShown("exp.new") && (
          <DraftJudgeDialog
            open={draftOpen}
            onClose={() => setDraftOpen(false)}
            datasetId={datasetId}
            column={referenceColumn}
            version={runVersion}
            onCreated={(evalConfig) =>
              handleAddEvaluation({
                ...evalConfig,
                name: `${evalConfig.displayName} (draft)`,
              })
            }
          />
        )}
        <EvalPickerDrawer
          open={pickerOpen}
          onClose={() => {
            setPickerOpen(false);
            setEditingEval(null);
          }}
          source="experiment"
          sourceId={datasetId}
          sourceColumns={sourceColumns}
          extraColumns={experimentVirtualColumns}
          existingEvals={evalFields}
          onEvalAdded={handleAddEvaluation}
          initialEval={editingEval}
          // IT-R3 G5 P3: Add attaches an existing eval (no form); the
          // pencil opens the form. Rows read like the cards.
          skipConfig={!editingEval}
          formatEvalName={evalDisplayName}
          headerAction={
            isSurfaceShown("exp.new") && production && !productionAdded ? (
              <Button
                size="small"
                variant="outlined"
                data-testid="picker-flagged-add"
                onClick={() => {
                  addProductionEval();
                  setPickerOpen(false);
                }}
                sx={{ whiteSpace: "nowrap" }}
              >
                {`Used to flag these rows: ${production.criterion} · Add`}
              </Button>
            ) : null
          }
        />
      </Stack>
    </ExperimentFormContext.Provider>
  );
};

export default EvaluationStepExperimentCreation;

EvaluationStepExperimentCreation.propTypes = {
  control: PropTypes.object,
  allColumns: PropTypes.array,
  showErrors: PropTypes.bool,
};
