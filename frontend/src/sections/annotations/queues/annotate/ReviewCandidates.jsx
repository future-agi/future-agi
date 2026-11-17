import { formatExactMoney } from "src/sections/common/format/money";
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Collapse,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
// W4 C2: each eval's reason and the trace, next to the output.
import CandidateEvidence from "./CandidateEvidence";
import useValueViewMode from "src/sections/common/ValueViewer/useValueViewMode";
import { runColorAt } from "src/sections/common/Runs/runColors";
import Truncate from "src/sections/common/ux/Truncate";
import { VOCAB } from "src/sections/common/vocab";
import { checklistOf, taskDetailsSummary, taskOneLiner } from "./reviewLayout";

const isBlank = (v) =>
  v == null ||
  (typeof v === "string" && ["", "[]", "{}"].includes(v.trim())) ||
  (Array.isArray(v) && v.length === 0);

const money = (v) => formatExactMoney(v, { missing: null });
const seconds = (ms) =>
  Number.isFinite(ms)
    ? ms < 1000
      ? `${Math.round(ms)} ms`
      : `${(ms / 1000).toFixed(1)} s`
    : null;

function SectionTitle({ children, action }) {
  return (
    <Stack direction="row" alignItems="center" sx={{ mb: 0.75 }}>
      <Typography
        variant="caption"
        fontWeight={700}
        color="text.secondary"
        sx={{ textTransform: "uppercase", letterSpacing: 0.4, flex: 1 }}
      >
        {children}
      </Typography>
      {action}
    </Stack>
  );
}

SectionTitle.propTypes = { children: PropTypes.node, action: PropTypes.node };

function Fields({ values, mode }) {
  const entries = Object.entries(values ?? {}).filter(([, v]) => !isBlank(v));
  if (!entries.length)
    return (
      <Typography
        variant="body2"
        color="text.disabled"
        sx={{ fontStyle: "italic" }}
      >
        Empty
      </Typography>
    );
  return (
    <Stack spacing={1.25}>
      {entries.map(([name, value]) => (
        <Box key={name}>
          {entries.length > 1 ? (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mb: 0.25 }}
            >
              {name}
            </Typography>
          ) : null}
          <Box
            sx={{ p: 1.25, borderRadius: 0.5, bgcolor: "background.neutral" }}
          >
            <ValueViewer value={value} mode={mode} clampChars={600} />
          </Box>
        </Box>
      ))}
    </Stack>
  );
}

Fields.propTypes = { values: PropTypes.object, mode: PropTypes.string };

function ScoreChip({ score }) {
  const tone = score.pass == null ? null : score.pass ? "success" : "error";
  return (
    <Box
      component="span"
      sx={(t) => ({
        display: "inline-flex",
        alignItems: "center",
        height: 22,
        px: 0.75,
        borderRadius: 0.5,
        typography: "caption",
        fontWeight: 500,
        whiteSpace: "nowrap",
        bgcolor: tone ? alpha(t.palette[tone].main, 0.12) : "action.hover",
        color: tone
          ? t.palette.mode === "dark"
            ? t.palette[tone].light
            : t.palette[tone].dark
          : "text.secondary",
      })}
    >
      {score.name} · {score.display}
    </Box>
  );
}

ScoreChip.propTypes = { score: PropTypes.object.isRequired };

function Candidate({ candidate, blind, mode, trajectoryUrl, colorIndex }) {
  const color = runColorAt(colorIndex);
  const title = blind ? `Response ${candidate.slot}` : candidate.run_label;
  const meta = [
    seconds(candidate.latency_ms),
    Number.isFinite(candidate.tokens)
      ? `${Math.round(candidate.tokens).toLocaleString()} tok`
      : null,
    money(candidate.cost_usd),
    candidate.steps?.steps ? `${candidate.steps.steps} steps` : null,
    candidate.steps?.tool_errors
      ? `${candidate.steps.tool_errors} tool errors`
      : null,
    candidate.trials > 1 ? `${candidate.trials} trials` : null,
  ].filter(Boolean);
  return (
    <Stack
      spacing={1}
      sx={{
        minWidth: 0,
        p: 1.5,
        borderRadius: 0.5,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        {blind ? (
          <Box
            sx={{
              width: 22,
              height: 22,
              borderRadius: 0.5,
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              bgcolor: "action.hover",
              typography: "caption",
              fontWeight: 700,
            }}
          >
            {candidate.slot}
          </Box>
        ) : (
          <Box
            sx={{
              width: 3,
              height: 16,
              borderRadius: 1,
              bgcolor: color.solid,
              flexShrink: 0,
            }}
          />
        )}
        <Typography variant="subtitle2" noWrap>
          {title}
        </Typography>
        {!blind && candidate.run_name ? (
          <Typography
            variant="caption"
            color="text.secondary"
            noWrap
            sx={{ minWidth: 0 }}
          >
            {candidate.run_name}
          </Typography>
        ) : null}
      </Stack>
      <Box sx={{ minHeight: 80 }}>
        {candidate.status === "error" ? (
          <Typography variant="body2" color="error.main">
            {candidate.error || "This run errored on this row."}
          </Typography>
        ) : candidate.status === "missing" ? (
          <Typography
            variant="body2"
            color="text.disabled"
            sx={{ fontStyle: "italic" }}
          >
            This run did not run this row.
          </Typography>
        ) : (
          <ValueViewer
            value={candidate.output}
            mode={mode}
            clampChars={2400}
            emptyLabel="Empty output"
          />
        )}
      </Box>
      {!blind && meta.length ? (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {meta.join(" · ")}
        </Typography>
      ) : null}
      {!blind && candidate.scores?.length ? (
        <Stack direction="row" gap={0.5} flexWrap="wrap">
          {candidate.scores.map((s) => (
            <ScoreChip key={s.key} score={s} />
          ))}
        </Stack>
      ) : null}
      <CandidateEvidence
        candidate={candidate}
        blind={blind}
        traceUrl={
          trajectoryUrl
            ? trajectoryUrl.replace(
                /run=[^&]+&dtab=trajectory/,
                `run=${candidate.run_id}&dtab=trace`,
              )
            : null
        }
      />
      {trajectoryUrl && candidate.target_type !== "external" ? (
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={
            blind
              ? "Opens the steps in a new tab (shows run names)"
              : "Opens the steps in a new tab"
          }
        >
          <Link
            component="button"
            type="button"
            variant="caption"
            underline="hover"
            onClick={() => window.open(trajectoryUrl, "_blank", "noopener")}
            sx={{
              alignSelf: "flex-start",
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
            }}
          >
            <Iconify icon="mdi:source-branch" width={14} />
            View trajectory
            <Iconify icon="mdi:open-in-new" width={12} />
          </Link>
        </CustomTooltip>
      ) : null}
    </Stack>
  );
}

Candidate.propTypes = {
  candidate: PropTypes.object.isRequired,
  blind: PropTypes.bool,
  mode: PropTypes.string,
  trajectoryUrl: PropTypes.string,
  colorIndex: PropTypes.number,
};

function Checklist({ list }) {
  return (
    <Stack component="ul" spacing={0.75} sx={{ m: 0, p: 0, listStyle: "none" }}>
      {list.items.map((c) => (
        <Stack
          component="li"
          key={c.key}
          direction="row"
          alignItems="flex-start"
          gap={1}
        >
          <Iconify
            icon="mdi:checkbox-marked-circle-outline"
            width={16}
            sx={{ color: "text.disabled", mt: "2px", flexShrink: 0 }}
          />
          <Box sx={{ minWidth: 0 }}>
            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              flexWrap="wrap"
            >
              <Typography variant="body2" fontWeight={600}>
                {c.name}
              </Typography>
              {c.type === "judge" ? (
                <Chip
                  size="small"
                  variant="outlined"
                  label={VOCAB.judgeChip}
                  sx={{ height: 18, borderRadius: 0.5, typography: "caption" }}
                />
              ) : null}
              {c.draft ? (
                <CustomTooltip
                  show
                  size="small"
                  placement="top"
                  title="Drafted from a production session; not reviewed yet"
                >
                  <Chip
                    size="small"
                    color="warning"
                    variant="outlined"
                    label="Draft"
                    sx={{
                      height: 18,
                      borderRadius: 0.5,
                      typography: "caption",
                    }}
                  />
                </CustomTooltip>
              ) : null}
            </Stack>
            {c.description ? (
              <Typography variant="body2" color="text.secondary">
                {c.description}
              </Typography>
            ) : null}
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}

Checklist.propTypes = { list: PropTypes.object.isRequired };

/**
 * Content panel for an experiment-review item (§3.12, R3 analysis fix):
 * the task in one line, then the replies being judged side by side
 * (`Response 1 / 2` when blind, run chips otherwise) so they are on the
 * first screen; the full input, the expected checks (as a checklist) and
 * the metadata sit in a collapsed `Task details`.
 */
export default function ReviewCandidates({ context, datasetName }) {
  const [mode, setMode] = useValueViewMode();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const ex = context.example ?? {};
  const ds = context.dataset_id;
  const isPair = context.mode === "pairwise";
  const blind = isPair && context.blind !== false;
  const rowUrl = `/dashboard/develop/${ds}?tab=data&row=${ex.example_id}`;
  const trajectoryUrl = (c) =>
    isPair
      ? `/dashboard/develop/${ds}/compare/data?runs=${context.run_a},${context.run_b}&baseline=${context.run_a}&row=${ex.example_id}&run=${context.run_b}&dtab=trajectory`
      : c.experiment_id
        ? `/dashboard/develop/experiment/${c.experiment_id}/data?row=${ex.example_id}&run=${c.run_id}&dtab=trajectory`
        : null;
  const task = taskOneLiner(ex.input);
  const checks = checklistOf(ex.expected);
  const detailsSummary = taskDetailsSummary(ex);
  const metaEntries = Object.entries(ex.metadata ?? {}).filter(
    ([, v]) => !isBlank(v),
  );
  const expectedRest = checks
    ? Object.fromEntries(
        Object.entries(ex.expected ?? {}).filter(([k]) => k !== checks.field),
      )
    : ex.expected;

  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography
          variant="body2"
          color="text.secondary"
          noWrap
          sx={{ flex: 1, minWidth: 0 }}
        >
          {datasetName ? `Dataset: ${datasetName} · ` : ""}Row #{ex.row_order} ·{" "}
          {isPair
            ? blind
              ? "Blind pairwise review"
              : `Pairwise · ${context.candidates.find((c) => c.side === "B")?.run_label} vs ${context.candidates.find((c) => c.side === "A")?.run_label}`
            : `Rubric · ${context.candidates?.[0]?.run_label ?? ""}`}
        </Typography>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={mode}
          onChange={(_, v) => v && setMode(v)}
          aria-label="Value format"
        >
          <ToggleButton
            value="formatted"
            sx={{ px: 1, py: 0.25, typography: "caption" }}
          >
            Formatted
          </ToggleButton>
          <ToggleButton
            value="raw"
            sx={{ px: 1, py: 0.25, typography: "caption" }}
          >
            Raw
          </ToggleButton>
        </ToggleButtonGroup>
      </Stack>

      {task ? (
        <Box data-testid="review-task-line">
          <SectionTitle>
            Task
            <Box
              component="span"
              sx={{
                ml: 0.75,
                textTransform: "none",
                fontWeight: 400,
                letterSpacing: 0,
                color: "text.disabled",
              }}
            >
              {task.name}
            </Box>
          </SectionTitle>
          <Box
            sx={{
              px: 1.25,
              py: 1,
              borderRadius: 0.5,
              bgcolor: "background.neutral",
              typography: "body2",
            }}
          >
            <Truncate text={task.text} lines={2} expandable />
          </Box>
        </Box>
      ) : null}

      <Box data-testid="review-responses">
        <SectionTitle>
          {isPair ? "Which reply is better?" : "Response"}
        </SectionTitle>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: isPair
              ? "minmax(0,1fr) minmax(0,1fr)"
              : "minmax(0,1fr)",
            gap: 1.5,
          }}
        >
          {(context.candidates ?? []).map((c) => (
            <Candidate
              key={`${c.slot}-${c.run_id}`}
              candidate={c}
              blind={blind}
              mode={mode}
              trajectoryUrl={trajectoryUrl(c)}
              colorIndex={c.side === "B" ? 1 : 0}
            />
          ))}
        </Box>
      </Box>

      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
        }}
      >
        <Button
          fullWidth
          color="inherit"
          onClick={() => setDetailsOpen((o) => !o)}
          aria-expanded={detailsOpen}
          data-testid="review-task-details"
          startIcon={
            <Iconify
              icon={detailsOpen ? "mdi:chevron-down" : "mdi:chevron-right"}
              width={18}
            />
          }
          sx={{
            justifyContent: "flex-start",
            px: 1.25,
            py: 0.75,
            gap: 0.5,
            typography: "body2",
            fontWeight: 600,
          }}
        >
          Task details
          {detailsSummary ? (
            <Typography
              component="span"
              variant="body2"
              color="text.secondary"
              noWrap
              sx={{ ml: 0.5, minWidth: 0 }}
            >
              · {detailsSummary}
            </Typography>
          ) : null}
        </Button>
        <Collapse in={detailsOpen} unmountOnExit>
          <Stack spacing={2} sx={{ px: 1.5, pb: 1.5, pt: 0.5 }}>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
                gap: 2,
                alignItems: "start",
              }}
            >
              <Box sx={{ minWidth: 0 }}>
                <SectionTitle>Input</SectionTitle>
                <Fields values={ex.input} mode={mode} />
              </Box>
              <Box sx={{ minWidth: 0 }}>
                <SectionTitle
                  action={
                    <Link
                      component="button"
                      type="button"
                      variant="caption"
                      underline="hover"
                      onClick={() =>
                        window.open(
                          `${rowUrl}&edit=expected`,
                          "_blank",
                          "noopener",
                        )
                      }
                      sx={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 0.5,
                      }}
                    >
                      Edit in dataset
                      <Iconify icon="mdi:open-in-new" width={12} />
                    </Link>
                  }
                >
                  {checks ? "Expected · checks on the end state" : "Expected"}
                </SectionTitle>
                {checks ? (
                  <Stack spacing={1.25}>
                    {mode === "raw" ? (
                      <Fields
                        values={{ [checks.field]: ex.expected[checks.field] }}
                        mode={mode}
                      />
                    ) : (
                      <Checklist list={checks} />
                    )}
                    {Object.keys(expectedRest ?? {}).length ? (
                      <Fields values={expectedRest} mode={mode} />
                    ) : null}
                  </Stack>
                ) : (
                  <Fields values={ex.expected} mode={mode} />
                )}
              </Box>
            </Box>
            {metaEntries.length ? (
              <Box>
                <SectionTitle>Metadata</SectionTitle>
                <Stack direction="row" gap={0.75} flexWrap="wrap">
                  {metaEntries.map(([k, v]) => (
                    <Chip
                      key={k}
                      size="small"
                      variant="outlined"
                      label={`${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`}
                      sx={{ borderRadius: 0.5 }}
                    />
                  ))}
                </Stack>
              </Box>
            ) : null}
          </Stack>
        </Collapse>
      </Box>
    </Stack>
  );
}

ReviewCandidates.propTypes = {
  context: PropTypes.shape({
    mode: PropTypes.string,
    blind: PropTypes.bool,
    left: PropTypes.string,
    dataset_id: PropTypes.string,
    run_a: PropTypes.string,
    run_b: PropTypes.string,
    example: PropTypes.object,
    candidates: PropTypes.array,
  }).isRequired,
  datasetName: PropTypes.string,
};
