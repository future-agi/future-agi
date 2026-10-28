import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Link, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useSimStore } from "../store";
import { improvementPath } from "../improvements/improvementNav";
import { addIssueScenariosPatch } from "./useFixPlan";

/* The issue's scenarios a run carries, as plain scenarios to add. */
const carriedOf = (run) =>
  (run?.fixScenarios || []).map(
    ({ forcedSamples: _f, fixScenario: _x, ...d }) => d,
  );

const linkSx = {
  typography: "s3",
  fontWeight: 600,
  color: "text.primary",
  verticalAlign: "baseline",
};

/* "Add them to Customer Support Line" — the run's issue scenarios, joining
   the environment only when someone chooses to. */
function AddToEnvironment({ envId, run }) {
  const { state, commit } = useSimStore();
  const es = state.byEnv?.[envId];
  const env = (state.myEnvironments || []).find((e) => e.id === envId);
  const have = new Set((es?.scenarios || []).map((x) => x.id));
  const missing = carriedOf(run).filter((d) => !have.has(d.id));
  if (!missing.length) return null;
  return (
    <>
      {" · "}
      <Link
        component="button"
        type="button"
        underline="hover"
        onClick={() => {
          const patch = addIssueScenariosPatch(
            es,
            missing,
            run.measuredFor?.issueId,
          );
          if (patch) commit({ type: "patchEnvState", envId, patch });
        }}
        sx={linkSx}
      >
        {`Add ${missing.length === 1 ? "it" : "them"} to ${env?.name || "the environment"}`}
      </Link>
    </>
  );
}
AddToEnvironment.propTypes = { envId: PropTypes.string, run: PropTypes.object };

/** In a finished run's header: an issue-only run, and its add action. */
export function IssueRunOrigin({ envId, run }) {
  if (run?.measuredFor?.kind !== "issue") return null;
  return (
    <>
      {" · "}
      {`${run.measuredFor.issueId}'s scenarios only`}
      <AddToEnvironment envId={envId} run={run} />
    </>
  );
}
IssueRunOrigin.propTypes = { envId: PropTypes.string, run: PropTypes.object };

/**
 * In a live run's notes row: what a fix's first run (or an issue-only run)
 * is for, how the issue's own scenarios are going, and the way on. Context,
 * not news — plain grey, text links, no tinted banner.
 */
export default function FixRunNote({ envId, run, issue = null }) {
  const navigate = useNavigate();
  const { state } = useSimStore();
  const kind = run?.measuredFor?.kind;
  const issueId =
    kind === "fix" || kind === "issue" ? run.measuredFor.issueId : null;
  if (!issueId) return null;
  const fix =
    kind === "fix"
      ? (state.byEnv?.[envId]?.optimizations || []).find(
          (o) => o.fromRunId === run.id,
        )
      : null;
  const progress = !(issue?.total > 0)
    ? ""
    : issue.done
      ? ` The issue's ${issue.total}: ${issue.failed} of ${issue.done} finished are failing.`
      : ` The issue's ${issue.total} haven't finished yet.`;
  return (
    <Stack direction="row" alignItems="center" spacing={0.625}>
      <Iconify icon="fagi:fix" width={14} sx={{ color: "text.tertiary" }} />
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        {kind === "fix"
          ? `Step 1 of the fix for ${issueId} — checking the issue shows up on today's agent. If it does, the fix tries changes when this run ends.${progress}`
          : `${issueId}'s scenarios only — no fix is tried.${progress}`}
        {fix && (
          <>
            {" · "}
            <Link
              component="button"
              type="button"
              underline="hover"
              onClick={() => navigate(improvementPath(envId, fix.id))}
              sx={linkSx}
            >
              View fix progress
            </Link>
          </>
        )}
        {kind === "issue" && <AddToEnvironment envId={envId} run={run} />}
      </Typography>
    </Stack>
  );
}
FixRunNote.propTypes = {
  envId: PropTypes.string,
  run: PropTypes.object,
  issue: PropTypes.object,
};
