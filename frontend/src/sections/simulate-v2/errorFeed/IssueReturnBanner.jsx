import PropTypes from "prop-types";
import { Alert, AlertTitle, Button, IconButton, Stack } from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useSimStore } from "../store";

/**
 * The way back to an Error Feed issue whose Fix sent the person here to build
 * the agent's environment. The environment remembers the issue
 * (`returnToIssue`), so this survives reloads and detours. Once it's built,
 * Continue fixing opens the issue with Fix ready — the fix measures today
 * itself, so nothing is run by hand first. It goes once a fix for the issue
 * has started, or when dismissed. Shown on the environment and its runs.
 */
export default function IssueReturnBanner({ envId }) {
  const navigate = useNavigate();
  const { state, commit } = useSimStore();
  const env = (state.myEnvironments || []).find((e) => e.id === envId);
  const issueId = env?.returnToIssue;
  if (!env || !issueId) return null;
  /* A fix for the issue started after this environment was linked to it
     (built for it, or opened from it): done here. */
  const since = Date.parse(env.returnSince || env.createdAt || 0) || 0;
  /* Built from the issue's Fix, or an environment that already existed. */
  const built = env.returnFrom === "built";
  const fixStarted = Object.values(state.byEnv || {}).some((s) =>
    (s?.optimizations || []).some(
      (o) =>
        o.trigger?.issueId === issueId &&
        (Date.parse(o.createdAt || 0) || 0) >= since,
    ),
  );
  if (fixStarted) return null;

  /* Built (or building) is all a fix needs — its first step measures today
     here, so nothing is run by hand first. */
  const ready = env.buildStatus !== "building";
  const back = () =>
    navigate(paths.dashboard.errorFeed.detail(issueId), {
      state: { openFix: true },
    });
  const dismiss = () =>
    commit({ type: "patchEnvironment", envId, patch: { returnToIssue: null } });

  const title = ready
    ? `Ready to fix ${issueId}`
    : built
      ? `Building for ${issueId}`
      : `Fixing ${issueId} — waiting for this environment`;
  const body = ready
    ? `${built ? "Built for it. " : ""}The fix runs this environment's scenarios on today's version first — what passes there must keep passing.`
    : "When it's built, continue the fix from here.";

  return (
    <Alert
      severity="info"
      icon={<Iconify icon="solar:bug-linear" width={16} />}
      sx={{ mx: 2.5, my: 1.25, flexShrink: 0 }}
      action={
        <Stack direction="row" alignItems="center" spacing={1}>
          <Button
            size="small"
            variant={ready ? "contained" : "outlined"}
            color={ready ? "primary" : "inherit"}
            onClick={back}
            sx={{ flexShrink: 0 }}
          >
            {ready ? `Continue fixing ${issueId}` : `Back to ${issueId}`}
          </Button>
          <IconButton size="small" onClick={dismiss} aria-label="Dismiss">
            <Iconify icon="solar:close-circle-linear" width={16} />
          </IconButton>
        </Stack>
      }
    >
      <AlertTitle>{title}</AlertTitle>
      {body}
    </Alert>
  );
}

IssueReturnBanner.propTypes = { envId: PropTypes.string };
