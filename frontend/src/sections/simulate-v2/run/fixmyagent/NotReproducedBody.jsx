import PropTypes from "prop-types";
import { Fragment } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { issueDraftsOf } from "./improvementView";
import { REPEATS } from "../../_mock/optimizationRuns";
import { scenarioTitle } from "../../_mock/scenarios";

/*
  Could not reproduce — the fix's first step re-ran the issue's failing
  conversations on today's agent and none failed reliably, so it stopped
  before trying any change (RSI PRD, Flow 3: "the run stops and says so,
  with options to edit scenarios or continue anyway").

  Read as: what happened, in a sentence; each scenario and its repeats, so
  "1 inconclusive" is something you can see; then the two reasons it can
  happen, each with what to do about it.
*/
const NUM = { fontVariantNumeric: "tabular-nums" };
const head = {
  typography: "s3",
  fontWeight: 600,
  color: "text.tertiary",
  textTransform: "none",
  letterSpacing: 0.5,
};

/* The PRD's three marks: Reproduced, Not reproduced, Inconclusive. */
const RESULT = {
  reproduced: (f, of) => `Reproduced · failed ${f} of ${of}`,
  inconclusive: (f, of) => `Inconclusive · failed ${f} of ${of}`,
  passed: (f, of) => `Not reproduced · passed ${of} of ${of}`,
};

export default function NotReproducedBody({
  record,
  envName,
  spent,
  onEdit,
  onContinue,
  onOpenIssue,
}) {
  const issueId = record.trigger?.issueId;
  const version = record.baseAgentVersion || "today's version";
  const per = record.reproduce?.perScenario || {};
  const drafts = issueDraftsOf(record);
  const rows = drafts.map((d) => ({
    id: d.id,
    title: d.title,
    ...(per[d.id] || { fails: 0, of: REPEATS, state: "passed" }),
  }));
  const reps = rows[0]?.of || REPEATS;
  const inconclusive = rows.filter((r) => r.state === "inconclusive").length;
  const passed = rows.filter((r) => r.state === "passed").length;

  const options = [
    {
      key: "edit",
      title: "The scenarios miss what triggers it",
      body: "A caller detail, live data or timing they don't capture. Edit them and reproduce again.",
      action: onEdit && {
        label: "Edit scenarios",
        onClick: onEdit,
        strong: true,
      },
    },
    {
      key: "fixed",
      title: `${version} no longer does it`,
      body: `Resolve ${issueId} on its page. If it comes back in production, the Error Feed reopens it.`,
      action: onOpenIssue && { label: `Open ${issueId}`, onClick: onOpenIssue },
    },
    {
      key: "anyway",
      title: "You want a fix anyway",
      body: inconclusive
        ? `Search with the inconclusive ${inconclusive === 1 ? "scenario" : "scenarios"} as the target. With nothing reproduced, a gain may be noise.`
        : "Search with nothing reproduced. Any gain may be noise.",
      action: onContinue && { label: "Continue anyway", onClick: onContinue },
    },
  ];

  return (
    <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 2.5 }}>
      <Box sx={{ maxWidth: 880 }}>
        {/* ── what happened ── */}
        <Stack direction="row" alignItems="flex-start" spacing={1.25}>
          <Iconify
            icon="solar:close-circle-linear"
            width={20}
            sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
          />
          <Box>
            <Typography
              sx={{ typography: "m3", fontWeight: 600, lineHeight: 1.35 }}
            >
              {`${issueId} didn't happen on ${version}`}
            </Typography>
            <Typography
              sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}
            >
              {`Its ${rows.length} scenarios ran ${reps} times each${envName ? ` in ${envName}` : ""}. None failed reliably, so no change was tried and the fix stopped here.`}
            </Typography>
          </Box>
        </Stack>

        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "116px minmax(0, 1fr)",
            columnGap: 2,
            rowGap: 0.5,
            mt: 1.5,
            pl: 4,
          }}
        >
          {[
            ["Reproduced", `0 of ${rows.length}`],
            [
              "Inconclusive",
              inconclusive
                ? `${inconclusive} — failed once, which can be chance`
                : "None",
            ],
            ["Not reproduced", `${passed} — passed every repeat`],
            spent ? ["Spent", `${spent} — this check only`] : null,
          ]
            .filter(Boolean)
            .map(([k, v]) => (
              <Fragment key={k}>
                <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                  {k}
                </Typography>
                <Typography
                  sx={{ typography: "s2", color: "text.secondary", ...NUM }}
                >
                  {v}
                </Typography>
              </Fragment>
            ))}
        </Box>

        {/* ── each scenario and its repeats ── */}
        <Typography
          sx={{ ...head, mt: 3, mb: 1 }}
        >{`Scenarios from ${issueId}`}</Typography>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {rows.map((r) => (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{ px: 1.75, py: 1 }}
            >
              <Typography
                noWrap
                sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}
              >
                {scenarioTitle(r.title)}
              </Typography>
              {/* One mark per repeat, failures first. */}
              <Stack
                direction="row"
                spacing={0.5}
                sx={{ flexShrink: 0 }}
                aria-label={`${r.fails} of ${r.of} repeats failed`}
              >
                {Array.from({ length: r.of }, (_, i) => (
                  <Iconify
                    key={i}
                    icon={
                      i < r.fails
                        ? "solar:close-circle-linear"
                        : "solar:check-circle-linear"
                    }
                    width={16}
                    sx={{
                      color: i < r.fails ? "text.primary" : "text.disabled",
                    }}
                  />
                ))}
              </Stack>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color:
                    r.state === "passed" ? "text.tertiary" : "text.primary",
                  fontWeight: r.state === "passed" ? 400 : 600,
                  width: 236,
                  flexShrink: 0,
                  textAlign: "right",
                  ...NUM,
                }}
              >
                {(RESULT[r.state] || RESULT.passed)(r.fails, r.of)}
              </Typography>
            </Stack>
          ))}
        </Box>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
          Reproduced means it failed at least twice. Once is inconclusive: kept
          as a scenario, never a target.
        </Typography>

        {/* ── why, and what to do ── */}
        <Typography sx={{ ...head, mt: 3, mb: 1 }}>What to do</Typography>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {options.map((o) => (
            <Stack
              key={o.key}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{ px: 1.75, py: 1.25 }}
            >
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {o.title}
                </Typography>
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
                >
                  {o.body}
                </Typography>
              </Box>
              {o.action && (
                <Button
                  size="small"
                  variant={o.action.strong ? "contained" : "outlined"}
                  color={o.action.strong ? "primary" : "inherit"}
                  onClick={o.action.onClick}
                  sx={{
                    flexShrink: 0,
                    minWidth: 136,
                    ...(!o.action.strong && {
                      color: "text.primary",
                      borderColor: "divider",
                    }),
                  }}
                >
                  {o.action.label}
                </Button>
              )}
            </Stack>
          ))}
        </Box>
      </Box>
    </Box>
  );
}

NotReproducedBody.propTypes = {
  record: PropTypes.object.isRequired,
  envName: PropTypes.string,
  /* What reproducing cost, already formatted. */
  spent: PropTypes.string,
  onEdit: PropTypes.func,
  onContinue: PropTypes.func,
  onOpenIssue: PropTypes.func,
};
