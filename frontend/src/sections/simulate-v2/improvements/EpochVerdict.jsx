import PropTypes from "prop-types";
import { Fragment, useState } from "react";
import { Box, Link, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { checksCountOf, winnerSentenceOf } from "./epochCandidates";

const RED = "status.danger.fg";
const num = { fontVariantNumeric: "tabular-nums" };
const panel = {
  mb: 2,
  flexShrink: 0,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  bgcolor: "background.paper",
  overflow: "hidden",
};
const linkSx = {
  typography: "s2",
  fontWeight: 500,
  verticalAlign: "baseline",
  flexShrink: 0,
};

/**
 * The top of a finished epoch, winner first: one sentence on what the
 * winner did, one line of checks (the full list and the numbers behind the
 * sentence on demand), then the winner — what it changed — and the other
 * candidates that passed, one line each. Every candidate the epoch paid
 * for is in the Candidates tab.
 */
export default function EpochVerdict({
  outcome,
  result,
  winner,
  winnerNote,
  changeSummary,
  alsoPassed = [],
  evidence = [],
  onSeeDiff,
  onSeeChange,
  onOpenRun,
  onShowCandidates,
}) {
  const [allChecks, setAllChecks] = useState(false);
  if (!result?.winner && !outcome?.headline) return null;
  const sentence =
    winnerSentenceOf(outcome, result, winner) || outcome.headline;
  const checks = outcome.checks || [];
  const { passed, unknown, failed } = checksCountOf(checks);
  const extra =
    evidence.length > 0 ||
    !!outcome.strength ||
    !!outcome.alongTheWay?.fixed?.length ||
    !!outcome.alongTheWay?.still?.length;
  const SHOWN = 5;

  return (
    <>
      {/* ── the verdict: one sentence, one line of checks ── */}
      <Box sx={panel}>
        <Typography
          sx={{
            px: 2,
            pt: 1.25,
            typography: "s1",
            fontWeight: 600,
            lineHeight: 1.45,
          }}
        >
          {sentence}
        </Typography>
        {checks.length > 0 || extra ? (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{ px: 2, pt: 0.5, pb: 1.25, minWidth: 0 }}
          >
            {checks.length > 0 && (
              <>
                <Iconify
                  icon={
                    failed.length
                      ? "solar:close-circle-bold"
                      : "solar:check-circle-linear"
                  }
                  width={14}
                  sx={{
                    color: failed.length ? RED : "text.secondary",
                    flexShrink: 0,
                  }}
                />
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    minWidth: 0,
                  }}
                >
                  {[
                    `${passed} ${passed === 1 ? "check" : "checks"} passed`,
                    unknown ? `${unknown} couldn't be measured` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  {failed.length > 0 && (
                    <Box component="span" sx={{ color: RED }}>
                      {` · ${failed.length} failed: ${failed
                        .map(
                          (c) =>
                            `${c.label}${c.actual ? ` (${c.actual})` : ""}`,
                        )
                        .join(" · ")}`}
                    </Box>
                  )}
                </Typography>
              </>
            )}
            <Link
              component="button"
              type="button"
              underline="hover"
              onClick={() => setAllChecks((o) => !o)}
              aria-expanded={allChecks}
              sx={{ ...linkSx, ml: checks.length ? 0.5 : 0 }}
            >
              {allChecks ? "Hide" : "Show all"}
            </Link>
          </Stack>
        ) : (
          <Box sx={{ pb: 1.25 }} />
        )}

        {allChecks && (
          <Box
            sx={{
              borderTop: "1px solid",
              borderColor: "divider",
              px: 2,
              py: 1,
            }}
          >
            {/* What the held-back result is worth, so a count never reads as proof on its own. */}
            {outcome.strength && (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                <Box
                  component="span"
                  sx={{ fontWeight: 600, color: "text.primary" }}
                >
                  {outcome.strength.label}
                </Box>
                {` · ${outcome.strength.note.charAt(0).toUpperCase()}${outcome.strength.note.slice(1)}.`}
              </Typography>
            )}
            {/* The numbers behind the sentence, one per row. */}
            {evidence.length > 0 && (
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "160px minmax(0, 1fr)",
                  columnGap: 2,
                  rowGap: 0.25,
                  mt: outcome.strength ? 0.75 : 0,
                  mb: 0.75,
                }}
              >
                {evidence.map(([k, v]) => (
                  <Fragment key={k}>
                    <Typography
                      sx={{ typography: "s2", color: "text.tertiary" }}
                    >
                      {k}
                    </Typography>
                    <Typography sx={{ typography: "s2", ...num, minWidth: 0 }}>
                      {v}
                    </Typography>
                  </Fragment>
                ))}
              </Box>
            )}
            {/* What else failed today, as the winner left it. Never a target. */}
            {outcome.alongTheWay?.fixed?.length > 0 && (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                <Box
                  component="span"
                  sx={{ fontWeight: 600, color: "text.primary" }}
                >
                  Also fixed along the way
                </Box>
                {` · ${outcome.alongTheWay.fixed.map((f) => `“${f.title}”`).join(", ")} — failing today, passing with this change. It wasn't aimed at ${outcome.alongTheWay.fixed.length === 1 ? "it" : "them"}.`}
              </Typography>
            )}
            {outcome.alongTheWay?.still?.length > 0 && (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                <Box
                  component="span"
                  sx={{ fontWeight: 600, color: "text.primary" }}
                >
                  Still failing, not part of this epoch
                </Box>
                {` · ${outcome.alongTheWay.still.length} ${outcome.alongTheWay.still.length === 1 ? "scenario" : "scenarios"} (${outcome.alongTheWay.still
                  .slice(0, 2)
                  .map((f) => `“${f.title}”`)
                  .join(
                    ", ",
                  )}${outcome.alongTheWay.still.length > 2 ? "…" : ""}) failed before this change too — fix ${outcome.alongTheWay.still.length === 1 ? "it" : "them"} separately.`}
              </Typography>
            )}
            {checks.length > 0 && (
              <Box
                sx={{
                  mt: extra ? 1 : 0,
                  display: "grid",
                  gridTemplateColumns: {
                    xs: "1fr",
                    md: "repeat(2, minmax(0, 1fr))",
                  },
                  columnGap: 3,
                  rowGap: 0.5,
                }}
              >
                {checks.map((c) => (
                  <Stack
                    key={c.id}
                    direction="row"
                    alignItems="center"
                    spacing={0.75}
                    sx={{ minWidth: 0 }}
                  >
                    <Iconify
                      icon={
                        c.pass === null
                          ? "solar:question-circle-linear"
                          : c.pass
                            ? "solar:check-circle-linear"
                            : "solar:close-circle-bold"
                      }
                      width={14}
                      sx={{
                        color:
                          c.pass === false
                            ? RED
                            : c.pass === null
                              ? "text.disabled"
                              : "text.secondary",
                        flexShrink: 0,
                      }}
                    />
                    <Typography
                      noWrap
                      sx={{
                        typography: "s2",
                        fontWeight: 500,
                        color: c.pass === false ? RED : "text.primary",
                        flexShrink: 0,
                      }}
                    >
                      {c.label}
                    </Typography>
                    <Tooltip arrow title={c.actual || ""} enterDelay={400}>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          minWidth: 0,
                        }}
                      >
                        {c.actual}
                      </Typography>
                    </Tooltip>
                  </Stack>
                ))}
              </Box>
            )}
          </Box>
        )}
      </Box>

      {/* ── the winner, and the others that passed ── */}
      {winner && (
        <Box sx={panel}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 2,
              minHeight: 36,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={{ typography: "s1", fontWeight: 500 }}>
              {winner.name}
            </Typography>
            {winnerNote && (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {winnerNote}
              </Typography>
            )}
            <Box flex={1} />
            {onOpenRun && (
              <Tooltip
                arrow
                describeChild
                title={`Opens ${winner.name}'s run: its calls on the target scenarios.`}
              >
                <Link
                  component="button"
                  type="button"
                  underline="hover"
                  onClick={() => onOpenRun(winner)}
                  sx={{ ...linkSx, color: "text.secondary" }}
                >
                  Open its run
                </Link>
              </Tooltip>
            )}
            <StatusBadge status="passed" role="success" label="Winner" />
          </Stack>
          <Stack
            direction="row"
            alignItems="baseline"
            spacing={2}
            sx={{ px: 2, py: 0.75 }}
          >
            <Typography
              sx={{
                typography: "s2",
                color: "text.tertiary",
                width: 112,
                flexShrink: 0,
              }}
            >
              What changed
            </Typography>
            <Typography sx={{ typography: "s2", minWidth: 0 }}>
              {changeSummary || "No change recorded"}
              {changeSummary && onSeeDiff && (
                <>
                  {"  "}
                  <Link
                    component="button"
                    type="button"
                    underline="hover"
                    onClick={onSeeDiff}
                    sx={{ ...linkSx, ml: 0.75 }}
                  >
                    See the diff
                  </Link>
                </>
              )}
            </Typography>
          </Stack>

          {alsoPassed.length > 0 && (
            <Box
              sx={{
                borderTop: "1px solid",
                borderColor: "divider",
                px: 2,
                py: 0.75,
              }}
            >
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mb: 0.25 }}
              >
                {`Also passed, not picked (${alsoPassed.length}) · winner ${winner.score}% on target · held back is pass or fail, so the best on target wins`}
              </Typography>
              {alsoPassed.slice(0, SHOWN).map((c) => (
                <Stack
                  key={c.n}
                  direction="row"
                  alignItems="center"
                  spacing={1.5}
                  sx={{ minHeight: 28, minWidth: 0 }}
                >
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      fontWeight: 500,
                      width: 96,
                      flexShrink: 0,
                    }}
                  >
                    {c.name}
                  </Typography>
                  <Tooltip arrow title={c.tried || ""} enterDelay={400}>
                    <Typography
                      noWrap
                      sx={{
                        typography: "s2",
                        color: "text.secondary",
                        flex: 1,
                        minWidth: 0,
                      }}
                    >
                      {c.tried || "—"}
                    </Typography>
                  </Tooltip>
                  {/* Why it wasn't kept, when its numbers alone don't say. */}
                  {c.reason && (
                    <Tooltip arrow title={c.reason}>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          flexShrink: 0,
                        }}
                      >
                        {String(c.result || "").replace(/^Passed · /, "")}
                      </Typography>
                    </Tooltip>
                  )}
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      ...num,
                      width: 96,
                      flexShrink: 0,
                      textAlign: "right",
                    }}
                  >
                    {`${c.score}% on target`}
                  </Typography>
                  <Tooltip
                    arrow
                    title={c.reason || ""}
                    disableHoverListener={!c.reason}
                  >
                    <Typography
                      noWrap
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        ...num,
                        width: 150,
                        flexShrink: 0,
                      }}
                    >
                      {c.held
                        ? `held back ${c.held.from}% → ${c.held.to}%`
                        : "held back not run"}
                    </Typography>
                  </Tooltip>
                  <Link
                    component="button"
                    type="button"
                    underline="hover"
                    onClick={() => onSeeChange(c)}
                    sx={{ ...linkSx, width: 104, textAlign: "left" }}
                  >
                    See its change
                  </Link>
                </Stack>
              ))}
              {alsoPassed.length > SHOWN && onShowCandidates && (
                <Link
                  component="button"
                  type="button"
                  underline="hover"
                  onClick={onShowCandidates}
                  sx={{ ...linkSx, color: "text.secondary", mt: 0.25 }}
                >
                  {`${alsoPassed.length - SHOWN} more in Candidates`}
                </Link>
              )}
            </Box>
          )}
        </Box>
      )}
    </>
  );
}

EpochVerdict.propTypes = {
  outcome: PropTypes.object.isRequired,
  result: PropTypes.object,
  /* The kept candidate's row (candidateRowsOf), when the epoch has one. */
  winner: PropTypes.object,
  /* "your pick" / "not verified yet", when someone chose it over the search. */
  winnerNote: PropTypes.string,
  changeSummary: PropTypes.string,
  alsoPassed: PropTypes.array,
  /* [[label, value]] — the numbers behind the sentence, under Show all. */
  evidence: PropTypes.array,
  onSeeDiff: PropTypes.func,
  onSeeChange: PropTypes.func,
  onOpenRun: PropTypes.func,
  onShowCandidates: PropTypes.func,
};
