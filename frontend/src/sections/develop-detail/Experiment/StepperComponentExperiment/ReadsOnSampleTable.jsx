import React from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import RunLetterBadge from "../RunLetterBadge";
import { renderPromptOnRow } from "../readsOnSampleModel";
import { useEvalMappingPreview } from "src/sections/common/EvaluationDrawer/useEvalMappingPreview";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { useExperimentForm } from "../ExperimentFormContext";

// v2.1 (tts:prompts): a TTS / Image prompt's {{variable}} with no mapping
// reads the column of its own name, as the run fills it
// (mediaRuns.promptTextOf); a text run's variables always carry a mapping.
const MEDIA_TYPES = new Set(["tts", "image"]);

const CLIP = 220;
const text = (value) =>
  typeof value === "object" && value !== null
    ? JSON.stringify(value)
    : String(value ?? "");
const clip = (value) => {
  const valueText = text(value);
  return valueText.length > CLIP ? `${valueText.slice(0, CLIP)}…` : valueText;
};

const box = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 0.5,
  px: 1.25,
  py: 1,
};

/** Saved inputs and future run inputs are distinguished without running a judge. */
const ReadsOnSampleTableInner = ({
  control,
  sampleRow,
  sampleRowLabel,
  datasetId,
  letters,
}) => {
  const promptConfig = useWatch({ control, name: "promptConfig" }) ?? [];
  const evals = (useWatch({ control, name: "userEvalMetrics" }) ?? []).filter(
    (evaluation) =>
      (evaluation.templateType ??
        evaluation.template_type ??
        evaluation.templateDetails?.template_type) !== "composite",
  );
  const experimentType = useWatch({ control, name: "experimentType" });
  const { columns } = useExperimentForm() ?? {};
  // v2.1 (wiz:reads-table): a new row that is not saved yet has no row on
  // the server for the scorer to read: no request, and the table says so.
  const pendingRow = Boolean(sampleRow?.pending_no);
  const reads = useEvalMappingPreview({
    evals,
    datasetId,
    rowId: pendingRow ? null : sampleRow?.row_id,
  });
  const byName = MEDIA_TYPES.has(String(experimentType ?? ""))
    ? Object.fromEntries((columns ?? []).map((c) => [c.name, c.id]))
    : null;
  const prompts = promptConfig
    .map((item, i) => ({
      i,
      name: `${item?.name ?? ""}${item?.versionLabel ? ` ${item.versionLabel}` : ""}`.trim(),
      messages: renderPromptOnRow(
        item?.messages?.length ? item.messages : item?.templateMessages,
        byName
          ? {
              ...byName,
              // a variable left unmapped reads its own column, as the run
              ...Object.fromEntries(
                Object.entries(item?.variableMapping ?? {}).filter(
                  ([, v]) => v != null && v !== "",
                ),
              ),
            }
          : item?.variableMapping,
        sampleRow,
      ),
    }))
    .filter((p) => p.messages.length);
  if (!sampleRow || (!prompts.length && !evals.length)) return null;
  return (
    <Box data-testid="reads-on-sample">
      <Typography typography="s1" fontWeight="fontWeightSemiBold">
        What each run and eval reads
      </Typography>
      <Typography typography="s2" color="text.secondary" sx={{ mb: 1 }}>
        {`On ${sampleRowLabel ?? "the sample row"}: the prompt each run sends, and each evaluation's inputs.`}
      </Typography>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns:
            prompts.length && evals.length
              ? "minmax(0, 1fr) minmax(0, 1fr)"
              : "minmax(0, 1fr)",
          gap: 1,
        }}
      >
        {prompts.length ? (
          <Stack spacing={1} data-testid="reads-prompts">
            {prompts.map((p) => (
              <Box key={p.i} sx={box}>
                <Stack
                  direction="row"
                  gap={0.5}
                  alignItems="center"
                  sx={{ mb: 0.5 }}
                >
                  {(letters?.[p.i] ?? []).map((l) => (
                    <RunLetterBadge key={l} letter={l} />
                  ))}
                  <Typography typography="s2" fontWeight="fontWeightMedium">
                    {`${p.name || "Prompt"} sends`}
                  </Typography>
                </Stack>
                {p.messages.map((m, k) => (
                  <Box key={k} sx={{ mb: 0.5 }}>
                    <Typography typography="s3" color="text.disabled">
                      {m.role}
                    </Typography>
                    <Typography
                      typography="s2"
                      sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                    >
                      {clip(m.text)}
                    </Typography>
                    {m.missing.length ? (
                      <Typography typography="s3" color="error.main">
                        {`Empty on this row: ${m.missing.map((v) => `{{${v}}}`).join(", ")}`}
                      </Typography>
                    ) : null}
                  </Box>
                ))}
              </Box>
            ))}
          </Stack>
        ) : null}
        {evals.length ? (
          <Stack spacing={1} data-testid="reads-evals">
            {evals.map((e, k) => (
              <Box key={e.id ?? e.name ?? k} sx={box} data-testid="reads-eval">
                <Typography
                  typography="s2"
                  fontWeight="fontWeightMedium"
                  sx={{ mb: 0.25 }}
                >
                  {e.name ?? e.evalTemplateName ?? "Evaluation"}
                </Typography>
                {pendingRow ? (
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    data-testid="reads-eval-pending"
                  >
                    This row is not saved yet: what this evaluation reads shows
                    once it is saved.
                  </Typography>
                ) : null}
                {reads[k]?.isLoading ? (
                  <Skeleton variant="text" width="60%" />
                ) : null}
                {reads[k]?.isError ? (
                  <Typography typography="s2" color="error.main">
                    Could not read what this evaluation reads on this row
                  </Typography>
                ) : null}
                {Object.entries(reads[k]?.data?.inputs_used ?? {}).map(
                  ([input, r]) => (
                    <Box
                      key={input}
                      sx={{ display: "flex", gap: 0.75, minWidth: 0 }}
                    >
                      <Typography
                        typography="s2"
                        color={
                          r.status === "missing" ? "error.main" : "text.primary"
                        }
                        sx={{ flexShrink: 0 }}
                      >
                        {`${input} ← ${r.column ?? "none"}`}
                      </Typography>
                      <Typography
                        typography="s2"
                        color={
                          r.status === "missing"
                            ? "error.main"
                            : "text.secondary"
                        }
                        noWrap
                        title={text(r.value ?? r.note)}
                        sx={{ minWidth: 0 }}
                      >
                        {r.status === "ok"
                          ? `= ${clip(r.value)}`
                          : `· ${r.note}`}
                      </Typography>
                    </Box>
                  ),
                )}
              </Box>
            ))}
          </Stack>
        ) : null}
      </Box>
    </Box>
  );
};

// Unclaimed builds render nothing and issue no preview requests.
const ReadsOnSampleTable = (props) =>
  isSurfaceShown("exp.new.savedInputReads") ? (
    <ReadsOnSampleTableInner {...props} />
  ) : null;

ReadsOnSampleTableInner.propTypes = {
  control: PropTypes.object.isRequired,
  sampleRow: PropTypes.object,
  sampleRowLabel: PropTypes.string,
  datasetId: PropTypes.string,
  letters: PropTypes.array,
};

ReadsOnSampleTable.propTypes = ReadsOnSampleTableInner.propTypes;

export default ReadsOnSampleTable;
