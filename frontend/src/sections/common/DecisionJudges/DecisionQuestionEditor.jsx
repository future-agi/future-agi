import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { isSurfaceShown } from "src/launch/launchSwitch";
import {
  FORMAT_LABEL,
  JEV_INFO_LINE,
  buildDecisionBody,
  decisionConfigErrors,
  decisionModelOf,
  priceLabel,
  questionTypeOf,
  routeDecision,
  stripTemplate,
} from "./decisionModels";
import { useDecisionModels } from "./useDecisionModels";

const TYPE_LABEL = {
  noul: "Yes / no · the probability of yes (noul)",
  predicate: "Yes / no · a predicate (no probability in this format)",
  choice: "One label from a list",
  score: "A score on ordered rubric levels",
};

const small = { "& .MuiInputBase-input": { fontSize: 13 } };

/**
 * The decision question a decision-model judge asks (W1, PR #3289 parity):
 * what yes and no mean, the labels (with descriptions and the score each
 * label counts as) or the rubric levels, and the route. Its instructions
 * are the eval's own prompt (mapping.py build_question), so there is no
 * second copy to edit here. The question type follows the eval's output
 * type and the model's format. `errors` are the picker's (the options the
 * call cannot use and the question's own problems).
 */
export default function DecisionQuestionEditor({
  value,
  onChange,
  outputType,
  variables = null,
  instructions = "",
  errors: errorsIn = null,
}) {
  const [showBody, setShowBody] = useState(false);
  const dm = decisionModelOf(value?.model);
  const format = dm?.format ?? value?.format;
  const q = questionTypeOf(outputType ?? value?.output_kind, format);
  const errors = useMemo(
    () => errorsIn ?? decisionConfigErrors(value, { instructions }),
    [errorsIn, value, instructions],
  );
  const set = (patch) => onChange({ ...value, ...patch });
  const labels = value?.choice?.labels ?? [];
  const levels = value?.score?.levels ?? [];
  // The state each row sends: the eval's own variables, as placeholders.
  const varKey = (variables ?? []).join("|");
  // No body when the format asks no such question (the error says why).
  const noQuestion = Boolean(q.error);
  const body = useMemo(() => {
    if (!value || !dm || noQuestion) return null;
    const names = varKey ? varKey.split("|") : ["input", "output"];
    return buildDecisionBody(
      value,
      Object.fromEntries(names.map((n) => [n, `{{${n}}}`])),
      { instructions },
    );
  }, [value, dm, varKey, noQuestion, instructions]);
  // What the gateway answers this body on this route with the workspace's
  // keys (the same checks the decision call goes through).
  const { rows: modelRows, isLoading: modelsLoading } = useDecisionModels();
  const gateway = useMemo(() => {
    if (!body || modelsLoading) return null;
    const keyed = [
      ...new Set(modelRows.filter((r) => r.ready).map((r) => r.provider)),
    ];
    return routeDecision({
      route: value?.route ?? "/v1/decisions",
      body,
      keyedProviders: keyed,
    });
  }, [body, modelRows, modelsLoading, value?.route]);
  // The gateway's own line already says a refusal it shares a code with.
  const shownErrors = gateway?.error
    ? errors.filter(
        (e) => !e.startsWith(`${gateway.error.status} ${gateway.error.code}`),
      )
    : errors;
  if (!isSurfaceShown("models.decision") || !value || !dm) return null;

  const setLabel = (i, next) => {
    const old = labels[i];
    const nextLabels = labels.map((l, j) => (j === i ? next : l));
    const rename = (obj = {}) =>
      Object.fromEntries(
        nextLabels.map((l) => [l, l === next ? obj[old] ?? obj[l] : obj[l]]),
      );
    set({
      choice: {
        labels: nextLabels,
        descriptions: rename(value.choice?.descriptions),
        scores: rename(value.choice?.scores),
      },
    });
  };
  const setChoiceField = (field, label, v) =>
    set({
      choice: {
        ...value.choice,
        [field]: { ...(value.choice?.[field] ?? {}), [label]: v },
      },
    });
  const removeLabel = (i) => {
    const gone = labels[i];
    const strip = (obj = {}) =>
      Object.fromEntries(Object.entries(obj).filter(([k]) => k !== gone));
    set({
      choice: {
        labels: labels.filter((_, j) => j !== i),
        descriptions: strip(value.choice?.descriptions),
        scores: strip(value.choice?.scores),
      },
    });
  };
  const addLabel = () => {
    let n = labels.length + 1;
    while (labels.includes(`label ${n}`)) n += 1;
    const name = `label ${n}`;
    set({
      choice: {
        labels: [...labels, name],
        descriptions: { ...(value.choice?.descriptions ?? {}), [name]: "" },
        scores: { ...(value.choice?.scores ?? {}), [name]: 0 },
      },
    });
  };
  const setLevels = (next) => set({ score: { levels: next } });
  const moveLevel = (i, d) => {
    const next = [...levels];
    const j = i + d;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    setLevels(next);
  };

  return (
    <Box
      data-testid="decision-question-editor"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
        display: "flex",
        flexDirection: "column",
        gap: 1.25,
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Iconify icon="mdi:scale-balance" width={18} />
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          Decision question
        </Typography>
        <Typography typography="s3" color="text.secondary">
          {`${dm.id} · ${FORMAT_LABEL[dm.format]} · ${priceLabel({
            input_per_mtok: dm.inputPerMTok,
            output_per_mtok: 0,
          })}`}
        </Typography>
      </Stack>
      <Typography typography="s3" color="text.secondary">
        {`Runs on your ${dm.keyName} key and bills it per token; no Future AGI credits. Each row's mapped values are the ${
          dm.format === "openai" ? "input" : "state"
        }; the question below is asked once per row.`}
      </Typography>
      {dm.provider === "typesafe" ? (
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="decision-jev-info"
        >
          {JEV_INFO_LINE}
        </Typography>
      ) : null}

      <Box>
        <Typography typography="s3" fontWeight="fontWeightMedium">
          Question type
        </Typography>
        <Typography
          typography="s2"
          data-testid="decision-question-type"
          color={q.error ? "error.main" : "text.primary"}
        >
          {q.error ?? TYPE_LABEL[q.type]}
        </Typography>
        <Typography typography="s3" color="text.secondary">
          Set by the eval&apos;s output type and the model&apos;s format.
        </Typography>
      </Box>

      <Box data-testid="decision-instructions">
        <Typography typography="s3" fontWeight="fontWeightMedium">
          Instructions
        </Typography>
        <Typography typography="s3" color="text.secondary">
          The eval&apos;s prompt above, with each row&apos;s values filled in
          and a line naming the state&apos;s variables. Edit the prompt to
          change the question.
        </Typography>
        <Typography
          typography="s3"
          data-testid="decision-instructions-preview"
          sx={{
            mt: 0.5,
            p: 0.75,
            bgcolor: "background.neutral",
            borderRadius: 0.5,
            whiteSpace: "pre-wrap",
            maxHeight: 96,
            overflow: "auto",
          }}
        >
          {stripTemplate(instructions) ||
            "No prompt yet: write the eval's prompt."}
        </Typography>
      </Box>

      {(q.type === "noul" || q.type === "predicate") && (
        <Stack direction={{ xs: "column", sm: "row" }} gap={1}>
          <TextField
            label="Yes means"
            size="small"
            fullWidth
            value={value.pass?.criteria_true ?? ""}
            onChange={(e) =>
              set({ pass: { ...value.pass, criteria_true: e.target.value } })
            }
            inputProps={{ "data-testid": "decision-yes-means" }}
            sx={small}
          />
          <TextField
            label="No means"
            size="small"
            fullWidth
            value={value.pass?.criteria_false ?? ""}
            onChange={(e) =>
              set({ pass: { ...value.pass, criteria_false: e.target.value } })
            }
            inputProps={{ "data-testid": "decision-no-means" }}
            sx={small}
          />
        </Stack>
      )}
      {(q.type === "noul" || q.type === "predicate") && (
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="decision-defs-reference"
        >
          The answer comes from the judge&apos;s stored verdicts on these rows;
          Yes means / No means are shown for reference and do not re-judge them.
        </Typography>
      )}

      {q.type === "choice" && (
        <Box data-testid="decision-labels">
          <Typography typography="s3" fontWeight="fontWeightMedium">
            Labels · what each means · the score it counts as (0 to 1)
          </Typography>
          {labels.map((l, i) => (
            <Stack
              key={`label-${i}`}
              direction="row"
              gap={1}
              alignItems="center"
              sx={{ mt: 0.75 }}
            >
              <TextField
                size="small"
                value={l}
                onChange={(e) => setLabel(i, e.target.value)}
                inputProps={{ "data-testid": `decision-label-${i}` }}
                sx={{ ...small, width: 140 }}
              />
              <TextField
                size="small"
                placeholder="What this label means"
                value={value.choice?.descriptions?.[l] ?? ""}
                onChange={(e) =>
                  setChoiceField("descriptions", l, e.target.value)
                }
                inputProps={{ "data-testid": `decision-label-desc-${i}` }}
                sx={{ ...small, flex: 1 }}
              />
              <TextField
                size="small"
                type="number"
                value={value.choice?.scores?.[l] ?? 0}
                onChange={(e) =>
                  setChoiceField(
                    "scores",
                    l,
                    Math.max(0, Math.min(1, Number(e.target.value))),
                  )
                }
                inputProps={{
                  step: 0.1,
                  min: 0,
                  max: 1,
                  "data-testid": `decision-label-score-${i}`,
                }}
                sx={{ ...small, width: 84 }}
              />
              <IconButton
                size="small"
                aria-label={`Remove label ${l}`}
                onClick={() => removeLabel(i)}
              >
                <Iconify icon="mdi:close" width={16} />
              </IconButton>
            </Stack>
          ))}
          <Button
            size="small"
            sx={{ mt: 0.5 }}
            onClick={addLabel}
            startIcon={<Iconify icon="mdi:plus" width={14} />}
          >
            Add label
          </Button>
        </Box>
      )}

      {q.type === "score" && (
        <Box data-testid="decision-levels">
          <Typography typography="s3" fontWeight="fontWeightMedium">
            Rubric levels, lowest first (2 to 10)
          </Typography>
          <Typography typography="s3" color="text.secondary">
            {`${levels.length} / 10 levels · minimum 2. Write each level; the normalized score is the raw score / (level count − 1).`}
          </Typography>
          {levels.map((l, i) => (
            <Stack
              key={`level-${i}`}
              direction="row"
              gap={0.5}
              alignItems="center"
              sx={{ mt: 0.75 }}
            >
              <Typography
                typography="s3"
                color="text.secondary"
                sx={{ width: 18 }}
              >
                {i}
              </Typography>
              <TextField
                size="small"
                fullWidth
                value={l}
                placeholder={`Level ${i}: what it means`}
                onChange={(e) =>
                  setLevels(
                    levels.map((x, j) => (j === i ? e.target.value : x)),
                  )
                }
                inputProps={{
                  maxLength: 2000,
                  "data-testid": `decision-level-${i}`,
                }}
                sx={small}
              />
              <IconButton
                size="small"
                aria-label={`Move level ${i} up`}
                onClick={() => moveLevel(i, -1)}
                disabled={i === 0}
              >
                <Iconify icon="mdi:arrow-up" width={16} />
              </IconButton>
              <IconButton
                size="small"
                aria-label={`Move level ${i} down`}
                onClick={() => moveLevel(i, 1)}
                disabled={i === levels.length - 1}
              >
                <Iconify icon="mdi:arrow-down" width={16} />
              </IconButton>
              <IconButton
                size="small"
                aria-label={`Remove level ${i}`}
                onClick={() => setLevels(levels.filter((_, j) => j !== i))}
              >
                <Iconify icon="mdi:close" width={16} />
              </IconButton>
            </Stack>
          ))}
          <Button
            size="small"
            sx={{ mt: 0.5 }}
            onClick={() => setLevels([...levels, ""])}
            startIcon={<Iconify icon="mdi:plus" width={14} />}
            disabled={levels.length >= 10}
          >
            Add level
          </Button>
        </Box>
      )}

      <TextField
        select
        size="small"
        label="Gateway route"
        value={value.route ?? "/v1/decisions"}
        onChange={(e) => set({ route: e.target.value })}
        inputProps={{ "data-testid": "decision-route" }}
        sx={{ ...small, maxWidth: 360 }}
        helperText={
          value.route === "/v1/systemone"
            ? "Takes System One bodies only (questions keyed by id)."
            : "Takes either format; the body goes to a provider that speaks it."
        }
      >
        <MenuItem value="/v1/decisions">/v1/decisions</MenuItem>
        <MenuItem value="/v1/systemone">/v1/systemone</MenuItem>
      </TextField>

      <Box>
        <Button
          size="small"
          disabled={!body}
          onClick={() => setShowBody((v) => !v)}
          startIcon={
            <Iconify
              icon={showBody ? "mdi:chevron-down" : "mdi:chevron-right"}
              width={14}
            />
          }
        >
          {`Request body (${FORMAT_LABEL[dm.format]})`}
        </Button>
        {showBody && body && (
          <Box
            component="pre"
            data-testid="decision-request-preview"
            sx={{
              m: 0,
              mt: 0.5,
              p: 1,
              bgcolor: "background.neutral",
              borderRadius: 0.5,
              fontSize: 12,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 220,
              overflow: "auto",
            }}
          >
            {`POST ${value.route ?? "/v1/decisions"}\n${JSON.stringify(body, null, 2)}`}
          </Box>
        )}
      </Box>

      {gateway && (
        <Typography
          typography="s3"
          data-testid="decision-gateway-check"
          color={gateway.error ? "error.main" : "success.dark"}
        >
          {gateway.error
            ? `Gateway answers ${gateway.error.status} ${gateway.error.code}: ${gateway.error.message}`
            : `Gateway accepts: POST ${value.route ?? "/v1/decisions"} → ${dm.providerName} (${FORMAT_LABEL[gateway.format]}), question ${gateway.question_types.join(", ")}`}
        </Typography>
      )}

      {shownErrors.length > 0 && (
        <Box data-testid="decision-errors">
          {shownErrors.map((e) => (
            <Typography key={e} typography="s3" color="error.main">
              {e}
            </Typography>
          ))}
        </Box>
      )}
    </Box>
  );
}

DecisionQuestionEditor.propTypes = {
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  outputType: PropTypes.string,
  variables: PropTypes.arrayOf(PropTypes.string),
  instructions: PropTypes.string,
  errors: PropTypes.arrayOf(PropTypes.string),
};
