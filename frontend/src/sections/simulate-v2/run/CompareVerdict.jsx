import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  Collapse,
  Tooltip,
  FormControlLabel,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { neutralCheckboxSx } from "../components/primitives";
import { textTone } from "../components/textTone";
import { noiseBandText, PAIR_FLOOR } from "../_mock/pairs";
import { clickable, focusRing } from "../components/clickable";

/* Red is for regressions only. Everything else stays neutral. */

const signed = (v) =>
  v == null
    ? "—"
    : `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toFixed(1)}`;

/* What a differing pin does to the pairs it touches — never a refusal. */
const PIN_EFFECT = {
  env: "rechecked where a scenario touches what changed",
  suite: "paired on stable ids",
  catalog: "re-graded, or criteria changed for a policy edit",
  eval: "re-graded to one set of evaluations",
  simulator: "changed until re-run",
  toolEnv: "rechecked on scenarios calling those tools",
  limits: "a trial may end differently at its timeout or turn limit",
};

/* Each part in plain words. The internal labels underneath (world, set,
   catalogue and eval numbers, the dial's fingerprint) are never shown — only
   whether the part matched; the simulated caller is named by its model. */
const PIN_LABEL = {
  env: "World and tools",
  suite: "Scenarios",
  catalog: "Sub-goals",
  eval: "Evaluations",
  simulator: "Simulated caller",
  toolEnv: "Tool fidelity",
};
const pinLabel = (r) => PIN_LABEL[r.id] || r.label;
const pinValue = (r) => {
  if (r.id === "simulator" || r.id === "limits")
    return r.same
      ? `same${r.values[0] ? ` · ${r.values[0]}` : ""}`
      : r.values.join(" → ");
  return r.same ? "same" : "changed";
};

/**
 * The verdict a comparison earns (PRD v7 §4.10.7, AC-14.8 – 14.13).
 *
 * The pair-class breakdown first — what the delta is computed over — then the
 * delta with its 95% interval and a label that carries the uncertainty. A
 * mismatch between the runs is a warning with a priced offer, never a reason
 * to refuse; what each run pinned sits one click away.
 */
export default function CompareVerdict({
  read,
  held,
  runs,
  identityOf,
  onExcludeRechecked,
  onFix,
  onWarning,
  questions = [],
  onAnswer,
  envHeadline = null,
}) {
  const [pinsOpen, setPinsOpen] = useState(false);
  if (!read) return null;
  const { counts, interval: iv, label, fixes, warnings, trend, band } = read;
  const floor = label.id === "rows";
  const side = label.id === "side";
  const inconclusive = label.id === "inconclusive" || floor;
  const cheapest = inconclusive ? fixes.find((f) => f.can) || fixes[0] : null;
  const others = inconclusive
    ? fixes.filter((f) => f !== cheapest && f.can)
    : [];
  const criteria = [
    ...new Map(
      read.pairs.flatMap((p) => p.criteria || []).map((c) => [c.id, c]),
    ).values(),
  ];
  const criteriaScenarios = read.pairs.filter(
    (p) => (p.criteria || []).length,
  ).length;
  const measuredNote = iv.n
    ? ` · over ${iv.n} measured pair${iv.n === 1 ? "" : "s"}`
    : "";
  const unmeasured =
    counts.exact +
    counts.regraded +
    counts.rechecked -
    (read.excludeRechecked ? counts.rechecked : 0) -
    iv.n;
  const [head, ...rest] = read.sentence.split(" · ");
  const differs = held?.differs || [];

  return (
    <Box
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {/* The verdict in one line: the label, the change, what it's over. The
          interval, the no-change band, the simulator's own noise and the
          environment both runs sat on are one hover away. */}
      <Box sx={{ px: 2, py: 1.5 }}>
        {side ? (
          <>
            <Typography
              sx={{ typography: "s1", fontWeight: 600 }}
            >{`${read.total} scenarios side by side`}</Typography>
            <Typography
              sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
            >
              {label.note}
            </Typography>
          </>
        ) : floor ? (
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            <Box
              component="span"
              sx={{ fontWeight: 600, color: "text.primary" }}
            >{`Only ${iv.n} comparable pair${iv.n === 1 ? "" : "s"}.`}</Box>
            {` Below ${PAIR_FLOOR} there's no aggregate — read the rows below, regressions first.`}
          </Typography>
        ) : (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{ flexWrap: "wrap", rowGap: 0.75 }}
          >
            <Tooltip
              arrow
              title={`${label.note} Improved or Regressed when the 95% interval excludes zero${band && band.source !== "estimate" && band.source !== "default" ? " and the change beats the noise" : ""}; No meaningful change when it sits inside the noise; Inconclusive otherwise. ${noiseBandText(band) || ""}`}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.5}
                sx={{
                  px: 0.875,
                  py: 0.25,
                  borderRadius: "4px",
                  border: "1px solid",
                  cursor: "default",
                  borderColor: (t) =>
                    label.id === "regressed"
                      ? "status.danger.border"
                      : t.palette.divider,
                  bgcolor: (t) =>
                    label.id === "regressed"
                      ? t.palette.status.danger.bg
                      : "transparent",
                }}
              >
                <Iconify
                  icon={
                    {
                      improved: "solar:arrow-up-linear",
                      regressed: "solar:arrow-down-linear",
                      same: "solar:minus-circle-linear",
                    }[label.id] || "solar:question-circle-linear"
                  }
                  width={14}
                  sx={{
                    color:
                      label.id === "regressed"
                        ? "status.danger.fg"
                        : "text.secondary",
                  }}
                />
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    color:
                      label.id === "regressed"
                        ? "status.danger.fg"
                        : "text.primary",
                  }}
                >
                  {label.label}
                </Typography>
              </Stack>
            </Tooltip>
            <Typography
              sx={{
                typography: "s1",
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
                color:
                  label.id === "regressed"
                    ? "status.danger.fg"
                    : "text.primary",
              }}
            >
              {`${signed(iv.delta)} pts`}
            </Typography>
            <Tooltip
              arrow
              title={[
                `95% interval ${signed(iv.lo)} to ${signed(iv.hi)} pts${measuredNote}${unmeasured > 0 ? ` · ${unmeasured} not measured` : ""}.`,
                `${head}${rest.length ? ` · ${rest.join(" · ")}` : ""}.`,
                envHeadline?.text ? `${envHeadline.text}.` : null,
                band
                  ? `${noiseBandText(band)} This change is ${Math.abs(iv.delta) > band.points ? "outside" : "inside"} it.`
                  : null,
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  fontVariantNumeric: "tabular-nums",
                  cursor: "default",
                  borderBottom: "1px dotted",
                  borderColor: "text.disabled",
                }}
              >
                {`pass rate, on ${iv.n} of ${read.total} scenarios${band ? ` · noise ±${band.points}` : ""}`}
              </Typography>
            </Tooltip>
            <Box flex={1} />
            {counts.rechecked > 0 && (
              <Tooltip
                arrow
                title="Rechecked pairs count in the delta, badged: a world element they touch changed, so the delta may mix agent and world effects."
              >
                <FormControlLabel
                  sx={{
                    m: 0,
                    "& .MuiFormControlLabel-label": {
                      typography: "s3",
                      color: "text.secondary",
                    },
                  }}
                  control={
                    <Checkbox
                      size="small"
                      checked={!read.excludeRechecked}
                      onChange={(e) => onExcludeRechecked(!e.target.checked)}
                      sx={{ p: 0.5, mr: 0.25, ...neutralCheckboxSx }}
                    />
                  }
                  label={`Include ${counts.rechecked} rechecked`}
                />
              </Tooltip>
            )}
          </Stack>
        )}

        {/* Undecided: the cheapest way to tell them apart, priced; nothing runs until clicked. */}
        {cheapest && (
          <Stack
            direction={{ xs: "column", sm: "row" }}
            alignItems={{ sm: "center" }}
            spacing={1}
            sx={{ mt: 1 }}
          >
            <Tooltip
              arrow
              title={`${cheapest.line}.${others.map((f) => ` Or ${f.line.charAt(0).toLowerCase()}${f.line.slice(1)}.`).join("")}`}
            >
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  flex: 1,
                  minWidth: 0,
                  cursor: "default",
                }}
              >
                Not enough evidence to tell these runs apart yet.
              </Typography>
            </Tooltip>
            <Stack direction="row" spacing={0.75} sx={{ flexShrink: 0 }}>
              {[cheapest, ...others]
                .filter((f) => f.can)
                .map((f) => (
                  <Button
                    key={f.id}
                    size="small"
                    variant="outlined"
                    onClick={() => onFix(f)}
                    startIcon={
                      <Iconify
                        icon={
                          f.kind === "raise-k"
                            ? "solar:repeat-linear"
                            : "solar:play-linear"
                        }
                        width={14}
                      />
                    }
                    sx={OUTLINE_SX}
                  >
                    {`${f.label} · ${f.calls} calls`}
                  </Button>
                ))}
            </Stack>
          </Stack>
        )}
      </Box>

      {/* Run-level mismatches — said, with the offer that resolves them. */}
      {(warnings.length > 0 || questions.length > 0 || criteria.length > 0) && (
        <Stack
          divider={
            <Box sx={{ borderTop: "1px solid", borderColor: "divider" }} />
          }
          sx={{ borderTop: "1px solid", borderColor: "divider" }}
        >
          {warnings.map((w) => (
            <Line
              key={w.id}
              icon={
                w.done
                  ? "solar:check-circle-linear"
                  : "solar:info-circle-linear"
              }
              title={w.title}
              body={w.body}
              action={
                w.action ? (
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => onWarning(w)}
                    sx={OUTLINE_SX}
                  >
                    {w.action}
                  </Button>
                ) : null
              }
            />
          ))}
          {questions.map((q) => (
            <Line
              key={q.version.label + q.impact.rule.id}
              icon="solar:question-circle-linear"
              title={`Fix or policy change? ${q.version.label} changed how the agent handles ${q.impact.rule.id}`}
              body={`Not answered yet, so its ${q.impact.subGoals.length} sub-goal${q.impact.subGoals.length === 1 ? "" : "s"} grade under the old rule. A policy answer reports them as criteria changed, outside the delta.`}
              action={
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => onAnswer(q.version)}
                  sx={OUTLINE_SX}
                >
                  Answer
                </Button>
              }
            />
          ))}
          {criteria.length > 0 && (
            <Line
              icon="solar:checklist-minimalistic-linear"
              title={`Criteria changed on ${criteria.length} sub-goal${criteria.length === 1 ? "" : "s"}: ${criteria.map((c) => c.name).join(", ")}`}
              body={`Changed as policy — out of the delta. The ${criteriaScenarios} scenario${criteriaScenarios === 1 ? "" : "s"} using ${criteria.length === 1 ? "it" : "them"} still pair on their other sub-goals.`}
            />
          )}
        </Stack>
      )}

      {/* Trend across several agent versions (AC-14.13) — on one environment version only. */}
      {trend && !side && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2,
            py: 1,
            borderTop: "1px solid",
            borderColor: "divider",
            flexWrap: "wrap",
            rowGap: 0.5,
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
            }}
          >
            Trend
          </Typography>
          {trend.n
            ? trend.points.map((p, i) => (
                <Stack
                  key={p.runId}
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                >
                  {i > 0 && (
                    <Iconify
                      icon="solar:arrow-right-linear"
                      width={12}
                      sx={{ color: "text.disabled" }}
                    />
                  )}
                  <Letter letter={p.letter} color={p.color} />
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >{`${p.agent} ${p.rate}%`}</Typography>
                </Stack>
              ))
            : null}
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {trend.n
              ? `on the ${trend.n} scenario${trend.n === 1 ? "" : "s"} comparable across all ${trend.points.length} runs`
              : `no scenario is comparable across all ${trend.points.length} runs`}
          </Typography>
        </Stack>
      )}

      {held?.drift && (
        <Typography
          sx={{
            px: 2,
            py: 1,
            typography: "s3",
            color: "text.secondary",
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
            Moved with nothing changed.
          </Box>
          {` ${held.drift.text}`}
        </Typography>
      )}

      {/* What each run pinned — the old held-fixed box, as a disclosure. */}
      {held && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            {...clickable(() => setPinsOpen((o) => !o), { expanded: pinsOpen })}
            sx={{
              px: 2,
              py: 0.875,
              cursor: "pointer",
              "&:hover": { bgcolor: "action.hover" },
              ...focusRing,
            }}
          >
            <Iconify
              icon={
                pinsOpen
                  ? "solar:alt-arrow-down-linear"
                  : "solar:alt-arrow-right-linear"
              }
              width={14}
              sx={{ color: "text.tertiary" }}
            />
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.secondary",
              }}
            >
              What each run pinned
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
            >
              {differs.length
                ? `${differs.length} differ${differs.length === 1 ? "s" : ""}: ${differs.map((d) => pinLabel(d).toLowerCase()).join(", ")}`
                : held.agents.length > 1
                  ? "only the agent varies"
                  : "everything matches"}
              {` · ${held.agents.length > 1 ? held.agents.join(" → ") : `agent ${held.agents[0] || "—"}`}`}
            </Typography>
          </Stack>
          <Collapse in={pinsOpen} unmountOnExit>
            <Box
              sx={{
                px: 2,
                pb: 1.5,
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
                columnGap: 2.5,
                rowGap: 1,
              }}
            >
              {held.rows.map((r) => (
                <Box key={r.id} minWidth={0}>
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {pinLabel(r)}
                  </Typography>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      fontWeight: r.same ? 500 : 600,
                      minWidth: 0,
                    }}
                  >
                    {pinValue(r)}
                  </Typography>
                  {!r.same && PIN_EFFECT[r.id] && (
                    <Typography
                      noWrap
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {PIN_EFFECT[r.id]}
                    </Typography>
                  )}
                </Box>
              ))}
            </Box>
            <Typography
              sx={{ px: 2, pb: 1.5, typography: "s3", color: "text.tertiary" }}
            >
              {`Runs from before a pin was recorded read it as it stands now. Compared runs: ${runs.map((r) => `${r.letter} ${identityOf(r).label}`).join(", ")}.`}
            </Typography>
          </Collapse>
        </Box>
      )}
    </Box>
  );
}
CompareVerdict.propTypes = {
  read: PropTypes.object,
  held: PropTypes.object,
  runs: PropTypes.array,
  identityOf: PropTypes.func,
  onExcludeRechecked: PropTypes.func,
  onFix: PropTypes.func,
  onWarning: PropTypes.func,
  questions: PropTypes.array,
  onAnswer: PropTypes.func,
  envHeadline: PropTypes.object,
};

const OUTLINE_SX = {
  flexShrink: 0,
  color: "text.primary",
  whiteSpace: "nowrap",
  borderColor: "divider",
  "&:hover": { borderColor: "text.disabled", bgcolor: "action.hover" },
};

function Line({ icon, title, body, action }) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      alignItems={{ sm: "center" }}
      spacing={1.25}
      sx={{ px: 2, py: 1 }}
    >
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "text.tertiary", flexShrink: 0 }}
      />
      <Typography
        sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}
      >
        <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
          {title}.
        </Box>
        {body ? ` ${body}` : ""}
      </Typography>
      {action}
    </Stack>
  );
}
Line.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.string,
  body: PropTypes.string,
  action: PropTypes.node,
};

function Letter({ letter, color }) {
  return (
    <Box
      sx={{
        minWidth: 20,
        height: 20,
        px: 0.25,
        borderRadius: "4px",
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        typography: "s3",
        fontWeight: 600,
        color: textTone(color),
        bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.22 : 0.14),
      }}
    >
      {letter}
    </Box>
  );
}
Letter.propTypes = { letter: PropTypes.string, color: PropTypes.string };
