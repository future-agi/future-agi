import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  Popover,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { VERDICT_CAUSES } from "../_mock/decoupled";
import { CAUSE_ORDER } from "../_mock/runCauses";
import { causeCountsLine } from "../_mock/runRollup";
import { creditsLabel } from "../_mock/metering";

const colHead = {
  typography: "s3",
  fontWeight: 600,
  color: "text.tertiary",
  textTransform: "none",
  letterSpacing: 0.4,
};
const num = { fontVariantNumeric: "tabular-nums" };
/* Column headers read like the product's DataTable: no fill, 13px, quiet. */

/* Only a pass^1 under the user's own bar earns colour — red, as a fail of
   their bar. It colours the report; it never blocks anything. */
const chipSx = (below) =>
  below
    ? {
        color: "status.danger.fg",
        bgcolor: "status.danger.bg",
        border: "1px solid",
        borderColor: "status.danger.border",
      }
    : {
        color: "text.primary",
        bgcolor: "transparent",
        border: "1px solid",
        borderColor: "divider",
      };

/**
 * The run summary, compact — for the run header, beside the status chip:
 * "pass^1 66% · pass^3 51%". Execution health sits in its own chip.
 */
export default function RunSummaryChip({ summary }) {
  if (!summary) return null;
  const causes = causeCountsLine(summary);
  return (
    <Tooltip
      classes={{ tooltip: "ds-hovercard" }}
      arrow
      title={
        <Stack spacing={0.5} sx={{ py: 0.25 }}>
          <b>Run summary</b>
          <span>{`${summary.passed} passed · ${summary.failed} failed of ${summary.scored} scored${summary.k > 1 ? `, ${summary.k} repeats each` : ""}`}</span>
          {summary.k > 1 && !summary.lowVolume && summary.scored > 0 && (
            <span>{`${summary.pass1Pct}% of all conversations passed; ${summary.passKPct}% of scenarios passed all ${summary.k} times — the gap is flakiness.`}</span>
          )}
          {causes && <span>{causes}</span>}
          {!summary.lowVolume && summary.scored > 0 && (
            <span>{`Your bar: ${Math.round(summary.bar.pass1 * 100)}% passed — it colours this report, nothing more.`}</span>
          )}
          {summary.unreliable && (
            <span>{`Unreliable: ${Math.round(summary.simulatorShare * 100)}% test invalid · simulator (over 10%).`}</span>
          )}
          {summary.pulled?.count > 0 && (
            <span>{`${summary.pulled.count} pulled for review after testing invalid twice — outside every count.`}</span>
          )}
          {summary.stale?.line && <span>{`${summary.stale.line}.`}</span>}
          {summary.caveat && <span>{summary.caveat}</span>}
        </Stack>
      }
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{
          px: 0.75,
          height: 22,
          borderRadius: "4px",
          flexShrink: 0,
          cursor: "default",
          ...chipSx(summary.belowBar),
        }}
      >
        <Typography
          noWrap
          sx={{ typography: "s3", fontWeight: 600, color: "inherit", ...num }}
        >
          {!summary.scored ? (
            "Nothing scored"
          ) : summary.lowVolume ? (
            summary.line
          ) : (
            <>
              {/* Plain words: the pass rate, then how many passed every repeat. */}
              {`${summary.pass1Pct}% passed`}
              {summary.k > 1 &&
                ` · ${summary.passKPct}% all ${summary.k} repeats`}
            </>
          )}
        </Typography>
        {summary.unreliable && (
          <Typography
            noWrap
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.secondary",
              pl: 0.25,
            }}
          >
            · unreliable
          </Typography>
        )}
      </Stack>
    </Tooltip>
  );
}
RunSummaryChip.propTypes = { summary: PropTypes.object };

/**
 * Execution health — ours, not the agent's — said as what it cost: "11 not
 * scored". Never red: a dead sandbox is our failure. Nothing shows when every
 * conversation was graded.
 */
export function HealthChip({ health }) {
  if (!health || !health.lost) return null;
  return (
    <Tooltip
      arrow
      title={`${health.id === "infra_failed" ? "Our test setup failed. " : ""}${health.note.replace(/\. Our failure, not the agent's\.$/, ".")} A problem on our side, not your agent's.`}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{
          px: 0.75,
          height: 22,
          borderRadius: "4px",
          flexShrink: 0,
          cursor: "default",
          color: "text.tertiary",
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify icon={health.icon} width={12} />
        <Typography
          noWrap
          sx={{ typography: "s3", fontWeight: 600, color: "inherit", ...num }}
        >
          {`${health.lost} not scored${health.id === "infra_failed" ? " · setup failed" : ""}`}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
HealthChip.propTypes = { health: PropTypes.object };

/**
 * Sealed · Live · Prompt only — where the run's agent ran and how its tools
 * were handled — beside the harness chip, with the note on hover. Live says
 * so on the chip: its tools reached real systems.
 */
export function ProofKindChip({ proof }) {
  if (!proof?.label) return null;
  return (
    <Tooltip
      classes={{ tooltip: "ds-hovercard" }}
      arrow
      title={
        <Stack spacing={0.5} sx={{ py: 0.25 }}>
          <b>{`Proof · ${proof.label}${proof.tierLabel ? ` · ${proof.tierLabel}` : ""}`}</b>
          <span>{proof.note}</span>
          {proof.pinned === false && (
            <span>
              Read from the agent as it is now — this run started before the
              proof kind was recorded.
            </span>
          )}
        </Stack>
      }
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{
          px: 0.75,
          height: 22,
          borderRadius: "4px",
          flexShrink: 0,
          cursor: "default",
          color: "text.tertiary",
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon={
            proof.kind === "live"
              ? "solar:global-linear"
              : proof.kind === "prompt-only"
                ? "solar:document-text-linear"
                : "solar:box-linear"
          }
          width={12}
        />
        <Typography
          noWrap
          sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}
        >
          {proof.kind === "live" ? "Live · real systems" : proof.label}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
ProofKindChip.propTypes = { proof: PropTypes.object };

const linkSx = (on) => ({
  color: "text.primary",
  fontWeight: 500,
  cursor: on ? "pointer" : "default",
  textDecoration: on ? "underline" : "none",
  textDecorationColor: (t) => alpha(t.palette.text.primary, 0.3),
  textUnderlineOffset: "3px",
});

/* A count that filters the Scenarios tab to the scenarios behind it. */
function Count({ n, ids, label, onFilter, sx }) {
  const on = !!onFilter && n > 0;
  return (
    <Box
      component="span"
      role={on ? "link" : undefined}
      tabIndex={on ? 0 : undefined}
      onClick={() => on && onFilter(label, ids)}
      onKeyDown={(e) => {
        if (on && e.key === "Enter") onFilter(label, ids);
      }}
      sx={{ ...linkSx(on), ...num, ...sx }}
    >
      {n}
    </Box>
  );
}
Count.propTypes = {
  n: PropTypes.number,
  ids: PropTypes.array,
  label: PropTypes.string,
  onFilter: PropTypes.func,
  sx: PropTypes.object,
};

/* The user's bar, set in place. A percentage of pass^1; it only colours. */
function BarEditor({ bar, onSetBar }) {
  const [anchor, setAnchor] = useState(null);
  const [value, setValue] = useState(String(Math.round(bar.pass1 * 100)));
  const n = Number(value);
  const ok = Number.isFinite(n) && n >= 0 && n <= 100;
  return (
    <>
      <Box
        component="span"
        role="button"
        tabIndex={0}
        onClick={(e) => {
          setValue(String(Math.round(bar.pass1 * 100)));
          setAnchor(e.currentTarget);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") setAnchor(e.currentTarget);
        }}
        sx={{ ...linkSx(true), fontWeight: 600 }}
      >
        Change
      </Box>
      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{
          paper: { sx: { p: 1.75, width: 320, borderRadius: "8px", mt: 0.5 } },
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Your bar
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 1.25 }}>
          A pass rate under it reads red on every run in this environment. It
          never blocks or ships anything — that call is yours.
        </Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          <TextField
            size="small"
            type="number"
            value={value}
            autoFocus
            onChange={(e) => setValue(e.target.value)}
            inputProps={{ min: 0, max: 100, step: 5 }}
            InputProps={{
              endAdornment: (
                <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                  %
                </Typography>
              ),
            }}
            sx={{ width: 110 }}
          />
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!ok}
            onClick={() => {
              onSetBar(n / 100);
              setAnchor(null);
            }}
          >
            Save
          </Button>
        </Stack>
      </Popover>
    </>
  );
}
BarEditor.propTypes = { bar: PropTypes.object, onSetBar: PropTypes.func };

/* What the strip doesn't say: the scenarios that ended outside the rate, by
   cause. Nothing on a clean run. */
function NotScored({ summary, onFilter }) {
  const causes = CAUSE_ORDER.filter(
    (c) => summary.counts[c] > 0 && !VERDICT_CAUSES[c]?.scored,
  );
  if (!causes.length) return null;
  return (
    <Stack
      direction="row"
      flexWrap="wrap"
      alignItems="baseline"
      columnGap={2}
      rowGap={0.5}
      sx={{
        px: 2,
        py: 1.25,
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography sx={{ ...colHead, flexShrink: 0 }}>Not scored</Typography>
      {causes.map((c) => {
        const meta = VERDICT_CAUSES[c];
        return (
          <Tooltip key={c} arrow title={meta.note || ""}>
            <Typography
              noWrap
              sx={{
                typography: "s2",
                color: "text.secondary",
                cursor: "default",
                ...num,
              }}
            >
              {`${meta.label} `}
              <Count
                n={summary.counts[c]}
                ids={summary.causeIds[c]}
                label={meta.label}
                onFilter={onFilter}
                sx={{ color: "text.primary", fontWeight: 600 }}
              />
            </Typography>
          </Tooltip>
        );
      })}
    </Stack>
  );
}
NotScored.propTypes = { summary: PropTypes.object, onFilter: PropTypes.func };

const fmtMs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);

/**
 * The first block of Insights: the run summary — pass^1 and pass^k, pass and
 * fail counts by verdict cause, what the numbers don't cover — with execution
 * health beside it, not inside it. A report: nothing here blocks, promotes or
 * ships an agent version (PRD v7 T58).
 */
export function RunSummaryBlock({
  summary,
  onFilter,
  onSetBar,
  envId = null,
  readOnly = false,
  eyebrow = null,
  headline = null,
  extraCells = [],
  children = null,
  footer = null,
}) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  if (!summary) return null;
  const { health, bar } = summary;
  const barPct = Math.round(bar.pass1 * 100);

  /* The notes under the issues — only when one applies. */
  const hasNotes = !!(
    summary.exploration?.count ||
    summary.pulled?.count ||
    summary.stale?.count ||
    summary.retried?.count ||
    summary.critical.failed.length
  );
  /* A cell of the metric strip: a label, the number, one quiet caption. */
  const cell = (key, label, value, caption, { tone, tip } = {}) => (
    <Box key={key} sx={{ px: 2, py: 1.5, minWidth: 0 }}>
      <Typography noWrap sx={{ ...colHead, mb: 0.5 }}>
        {label}
      </Typography>
      <Tooltip
        arrow
        title={tip || ""}
        disableHoverListener={!tip}
        placement="bottom-start"
      >
        <Typography
          noWrap
          component="div"
          sx={{
            typography: "m3",
            fontWeight: 600,
            color: tone || "text.primary",
            lineHeight: 1.25,
            ...num,
          }}
        >
          {value}
        </Typography>
      </Tooltip>
      {caption && (
        <Typography
          noWrap
          component="div"
          sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
        >
          {caption}
        </Typography>
      )}
    </Box>
  );
  const cells = [
    summary.lowVolume
      ? cell(
          "rate",
          "Result",
          summary.scored ? summary.line : "Nothing scored",
          summary.scored ? "too few scored for a rate" : "no scored verdict",
        )
      : cell(
          "p1",
          "Passed",
          `${summary.pass1Pct}%`,
          summary.scored > 0 ? (
            <>
              {`${summary.belowBar ? "Under" : "Clears"} your ${barPct}% bar`}
              {onSetBar && (
                <>
                  {" · "}
                  <BarEditor bar={bar} onSetBar={onSetBar} />
                </>
              )}
            </>
          ) : null,
          {
            tone: summary.belowBar ? "status.danger.fg" : undefined,
            tip: "The mean share of repeats each scored scenario passed.",
          },
        ),
    !summary.lowVolume &&
      summary.k > 1 &&
      cell(
        "pk",
        `All ${summary.k} repeats`,
        `${summary.passKPct}%`,
        `Passed all ${summary.k} repeats`,
      ),
    cell(
      "pf",
      "Passed / failed",
      <>
        <Count
          n={summary.passed}
          ids={summary.causeIds.pass}
          label="Passed"
          onFilter={onFilter}
          sx={{ fontWeight: 600 }}
        />
        <Box
          component="span"
          sx={{ color: "text.disabled", mx: 0.5, fontWeight: 400 }}
        >
          /
        </Box>
        <Count
          n={summary.failed}
          ids={summary.outcomeIds?.failed || []}
          label="Failed"
          onFilter={onFilter}
          sx={{ fontWeight: 600 }}
        />
      </>,
      `${summary.scored} scored`,
    ),
    cell("health", "Test setup", health.label, null, { tip: health.note }),
    summary.proofKind &&
      cell(
        "proof",
        "Proof",
        summary.proofKind.kind === "live" ? "Live" : summary.proofKind.label,
        summary.proofKind.kind === "live"
          ? "Real systems"
          : summary.proofKind.tierLabel || null,
        { tip: summary.proofKind.note },
      ),
    summary.speed &&
      cell(
        "cost",
        "Cost",
        /* Credits over the costs the agent's side reported; unverified ones are never summed. */
        summary.speed.costUnverified && !(summary.speed.cost > 0)
          ? "unverified"
          : `${creditsLabel(summary.speed.cost || 0)}${summary.speed.costUnverified ? ` + ${summary.speed.costUnverified} unverified` : ""}`,
        summary.speed.responseMs != null
          ? `Median response ${fmtMs(summary.speed.responseMs)}`
          : "Response time unverified",
        {
          tip: `Over ${summary.speed.conversations} scenarios that reached a verdict. Unverified means the agent's side didn't report it — it's left out, not counted as met.${summary.speed.costUnverified ? ` ${summary.speed.costUnverified} cost unverified.` : ""}`,
        },
      ),
    ...extraCells.map((c) =>
      cell(c.key, c.label, c.value, c.caption, { tone: c.tone, tip: c.tip }),
    ),
  ].filter(Boolean);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        breakInside: "avoid",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={2}
        sx={{ px: 2, pt: 1.5, pb: 1.25 }}
      >
        {/* The answer first: one sentence, then the numbers behind it. */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {eyebrow && (
            <Typography sx={{ ...colHead, mb: 0.5 }}>{eyebrow}</Typography>
          )}
          <Typography
            sx={{
              typography: headline ? "m3" : "s1",
              fontWeight: 600,
              lineHeight: 1.35,
            }}
          >
            {headline || "Run summary"}
          </Typography>
        </Box>
        <Typography
          role="button"
          tabIndex={0}
          onClick={() => setOpen((v) => !v)}
          onKeyDown={(e) => {
            if (e.key === "Enter") setOpen((v) => !v);
          }}
          className="summary-how"
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "text.tertiary",
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: 0.25,
            "&:hover": { color: "text.primary" },
          }}
        >
          How this is read
          <Iconify
            icon={
              open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
            }
            width={12}
          />
        </Typography>
      </Stack>
      {open && (
        <Box sx={{ px: 2, pb: 1.25 }}>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", maxWidth: 900 }}
          >
            Passed is the mean share of repeats each scored scenario passed; all
            repeats is the share of scenarios that passed every repeat. Every
            conversation ends passed, failed, inconclusive, test invalid or
            error. Scored means passed and failed. Inconclusive never counts as
            a pass; test invalid (the test broke, not the agent) and error (our
            infrastructure or the connection) count neither for nor against the
            agent — each is retried once on new runs, and a scenario test
            invalid twice is pulled for review. Outdated, Re-check and Stale
            scenarios run but count toward no decision; proposed scenarios show
            separately. The test setup reports — it never blocks, promotes or
            ships an agent version.
          </Typography>
        </Box>
      )}

      {/* The numbers, as a strip of equal cells — read across, not down. */}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: `repeat(auto-fit, minmax(140px, 1fr))`,
          borderTop: "1px solid",
          borderBottom: "1px solid",
          borderColor: "divider",
          "& > *:not(:first-of-type)": {
            borderLeft: "1px solid",
            borderColor: "divider",
          },
        }}
      >
        {cells}
      </Box>

      <Box>
        {/* Passed and failed are in the strip; only what ended outside the
            rate needs saying, and only when it happened. */}
        <NotScored summary={summary} onFilter={onFilter} />
        {summary.unreliable && (
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{
              px: 2,
              py: 1.25,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Iconify
              icon="solar:danger-triangle-linear"
              width={14}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              <Box
                component="span"
                sx={{ fontWeight: 600, color: "text.primary" }}
              >
                Unreliable ·{" "}
              </Box>
              <Count
                n={summary.counts["inc-simulator"]}
                ids={summary.causeIds["inc-simulator"]}
                label={VERDICT_CAUSES["inc-simulator"].label}
                onFilter={onFilter}
              />
              {` test invalid · simulator (${Math.round(summary.simulatorShare * 100)}%). Over 10%, the rates may be flattering.`}
            </Typography>
          </Stack>
        )}
      </Box>

      {children}

      {hasNotes && (
        <Stack
          spacing={0.5}
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {summary.exploration?.count > 0 && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              <Box component="span" sx={{ color: "text.tertiary" }}>
                Proposed ·{" "}
              </Box>
              <Count
                n={summary.exploration.count}
                ids={summary.exploration.ids}
                label="Proposed"
                onFilter={onFilter}
              />
              {` scenarios, not yet added to the environment — ${summary.exploration.passed} passed, `}
              <Count
                n={summary.exploration.failedIds.length}
                ids={summary.exploration.failedIds}
                label="Proposed · failed"
                onFilter={onFilter}
              />
              {` failed${summary.exploration.inconclusive ? `, ${summary.exploration.inconclusive} inconclusive` : ""}. Shown separately, outside the rates above.`}
            </Typography>
          )}
          {summary.pulled?.count > 0 && (
            <Box>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                <Box component="span" sx={{ color: "text.tertiary" }}>
                  Pulled for review ·{" "}
                </Box>
                <Count
                  n={summary.pulled.count}
                  ids={summary.pulled.ids}
                  label="Pulled for review"
                  onFilter={onFilter}
                />
                {` test invalid twice, so ${summary.pulled.count === 1 ? "it was" : "they were"} taken out of this run and left out of every count.`}
              </Typography>
              <Stack spacing={0.25} sx={{ mt: 0.5, pl: 1.25 }}>
                {summary.pulled.items.map((p) => (
                  <Stack
                    key={p.id}
                    direction="row"
                    spacing={1}
                    alignItems="baseline"
                    sx={{ minWidth: 0 }}
                  >
                    <Tooltip arrow title={p.reason}>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s3",
                          color: "text.primary",
                          minWidth: 0,
                          flex: 1,
                        }}
                      >
                        {p.title}
                      </Typography>
                    </Tooltip>
                    {envId && !readOnly && (
                      <Box
                        component="span"
                        role="link"
                        tabIndex={0}
                        onClick={() =>
                          navigate(
                            `${paths.dashboard.simulate.environmentStep(envId, "scenarios")}?scenario=${encodeURIComponent(p.id)}`,
                          )
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Enter")
                            navigate(
                              `${paths.dashboard.simulate.environmentStep(envId, "scenarios")}?scenario=${encodeURIComponent(p.id)}`,
                            );
                        }}
                        sx={{
                          ...linkSx(true),
                          typography: "s3",
                          fontWeight: 600,
                          flexShrink: 0,
                        }}
                      >
                        Review
                      </Box>
                    )}
                  </Stack>
                ))}
              </Stack>
            </Box>
          )}
          {summary.stale?.count > 0 && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              <Box component="span" sx={{ color: "text.tertiary" }}>
                Stale scenarios ·{" "}
              </Box>
              <Count
                n={summary.stale.count}
                ids={summary.stale.ids}
                label="Outdated, re-check or stale"
                onFilter={onFilter}
              />
              {` ${summary.stale.line.replace(/^\d+ /, "")}. Left out of the pass rates and the counts above.`}
            </Typography>
          )}
          {summary.retried?.count > 0 && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              <Box component="span" sx={{ color: "text.tertiary" }}>
                Retried once ·{" "}
              </Box>
              <Count
                n={summary.retried.count}
                ids={summary.retried.ids}
                label="Retried once"
                onFilter={onFilter}
              />
              {` ended test invalid or error and ran again — ${summary.retried.recovered} came back with a result.`}
            </Typography>
          )}
          {summary.critical.failed.length > 0 && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              <Box component="span" sx={{ color: "text.tertiary" }}>
                Critical scenarios that didn&apos;t pass ·{" "}
              </Box>
              <Count
                n={summary.critical.failed.length}
                ids={summary.critical.failed.map((x) => x.id)}
                label="Critical scenarios that didn't pass"
                onFilter={onFilter}
              />
              {` of ${summary.critical.total}`}
            </Typography>
          )}
        </Stack>
      )}
      {footer && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          {footer}
        </Box>
      )}
    </Box>
  );
}
RunSummaryBlock.propTypes = {
  summary: PropTypes.object,
  onFilter: PropTypes.func,
  onSetBar: PropTypes.func,
  envId: PropTypes.string,
  readOnly: PropTypes.bool,
  /* The verdict: a small label and the one-sentence answer above the strip. */
  eyebrow: PropTypes.node,
  headline: PropTypes.node,
  /* More strip cells: [{ key, label, value, caption, tone, tip }]. */
  extraCells: PropTypes.array,
  /* Under the bar — the issues to fix first. */
  children: PropTypes.node,
  /* The card's last row — Show details, and what the run metered and sent. */
  footer: PropTypes.node,
};
