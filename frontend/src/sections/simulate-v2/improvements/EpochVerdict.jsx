import PropTypes from "prop-types";
import { Fragment, useState } from "react";
import { Box, Link, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import {
  checksCountOf,
  winnerFiguresOf,
  winnerSentenceOf,
} from "./epochCandidates";

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
  onOpenRun,
  onShowCandidates,
  /* { cause, more: [causes] } — what the epoch set out to fix. */
  diagnosis = null,
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

  /* The checks line and, on demand, everything behind it. */
  const checksLine = (
    <>
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
                        (c) => `${c.label}${c.actual ? ` (${c.actual})` : ""}`,
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
    </>
  );
  const checksDetail = (
    <>
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
                  <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
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
    </>
  );

  /* No kept candidate: the epoch's own headline, its checks, its cause. */
  if (!winner) {
    return (
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
        {checksLine}
        <DiagnosisLine diagnosis={diagnosis} labelWidth={112} />
        {checksDetail}
      </Box>
    );
  }

  /*
    Winner first, in one card (24 Nov — the verdict and the winner were two
    cards about the same candidate, and the also-passed list repeated the
    Candidates tab): the winner and its badge; what it did as three figures;
    the checks; what changed and the diagnosis as two rows; then one link to
    the others that passed.
  */
  const figures = winnerFiguresOf(outcome, result, winner);
  return (
    <Box sx={panel}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, pt: 1.25 }}
      >
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {winner.name}
        </Typography>
        <StatusBadge status="passed" role="success" label="Winner" />
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
      </Stack>
      {figures.length > 0 && (
        <Tooltip arrow placement="bottom-start" title={sentence}>
          <Typography
            sx={{
              px: 2,
              pt: 0.375,
              typography: "s2",
              ...num,
              cursor: "default",
            }}
          >
            {figures.map((f, k) => (
              <Fragment key={f.text}>
                {k > 0 && (
                  <Box component="span" sx={{ color: "text.disabled" }}>
                    {" · "}
                  </Box>
                )}
                <Box
                  component="span"
                  sx={{
                    color: f.bad ? RED : "text.primary",
                    fontWeight: f.bad ? 600 : 500,
                  }}
                >
                  {f.text}
                </Box>
              </Fragment>
            ))}
          </Typography>
        </Tooltip>
      )}
      {checksLine}
      {checksDetail}
      <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
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
              <Link
                component="button"
                type="button"
                underline="hover"
                onClick={onSeeDiff}
                sx={{ ...linkSx, ml: 1.5 }}
              >
                See the diff
              </Link>
            )}
          </Typography>
        </Stack>
        <DiagnosisLine
          diagnosis={diagnosis}
          labelWidth={112}
          sx={{ borderTop: 0, pt: 0 }}
        />
      </Box>
      {alsoPassed.length > 0 && (
        <Stack
          direction="row"
          alignItems="baseline"
          spacing={1}
          sx={{
            px: 2,
            py: 0.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {`${alsoPassed.length} other${alsoPassed.length === 1 ? "" : "s"} also passed`}
          </Typography>
          {onShowCandidates && (
            <Link
              component="button"
              type="button"
              underline="hover"
              onClick={onShowCandidates}
              sx={linkSx}
            >
              See all candidates
            </Link>
          )}
        </Stack>
      )}
    </Box>
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
  diagnosis: PropTypes.object,
};

/*
  What a finished epoch set out to fix, as one folded line: "Diagnosis ·
  <top cause>  +2 more". Kept on every finished epoch — a winner or a failed
  one — so whoever reviews the fix sees the cause beside it (agreed with dev,
  24 Nov). The other causes open on demand.
*/
export function DiagnosisLine({ diagnosis, labelWidth = null, sx = {} }) {
  const [allCauses, setAllCauses] = useState(false);
  if (!diagnosis?.cause) return null;
  return (
    <Box
      sx={{
        borderTop: "1px solid",
        borderColor: "divider",
        px: 2,
        py: 0.875,
        ...sx,
      }}
    >
      <Stack direction="row" alignItems="baseline" spacing={0.75}>
        <Typography
          sx={{
            typography: "s2",
            color: "text.tertiary",
            flexShrink: 0,
            ...(labelWidth ? { width: labelWidth, mr: 1.25 } : {}),
          }}
        >
          Diagnosis
        </Typography>
        <Typography
          sx={{
            typography: "s2",
            color: "text.primary",
            minWidth: 0,
            ...(allCauses
              ? {}
              : {
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }),
          }}
          title={diagnosis.cause}
        >
          {diagnosis.cause}
        </Typography>
        {(diagnosis.more || []).length > 0 && (
          <Link
            component="button"
            type="button"
            onClick={() => setAllCauses((o) => !o)}
            aria-expanded={allCauses}
            sx={{ ...linkSx, flexShrink: 0 }}
          >
            {allCauses ? "Hide" : `+${diagnosis.more.length} more`}
          </Link>
        )}
      </Stack>
      {allCauses &&
        diagnosis.more.map((c) => (
          <Typography
            key={c}
            sx={{
              typography: "s2",
              color: "text.secondary",
              pl: labelWidth ? `${labelWidth + 16}px` : "calc(4.5em + 6px)",
              mt: 0.25,
            }}
          >
            {c}
          </Typography>
        ))}
    </Box>
  );
}
DiagnosisLine.propTypes = {
  diagnosis: PropTypes.object,
  labelWidth: PropTypes.number,
  sx: PropTypes.object,
};
