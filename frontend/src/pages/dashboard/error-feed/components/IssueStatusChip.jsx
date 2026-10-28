import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Stack, Tooltip, Typography, useTheme } from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { statusColors } from "src/utils/runStatus";
import { useAgentImprovementFlag } from "src/sections/simulate-v2/_mock/featureFlags";
import {
  issueFixNav,
  useIssueFix,
} from "src/sections/simulate-v2/errorFeed/issueFix";
import { WATCH_DAYS } from "src/sections/simulate-v2/errorFeed/issueFixModel";

/*
  The fix-status chip (RSI PRD: "a fix-status chip on the list"). It sits
  beside the issue's own triage status, never in place of it; with no fix
  it renders nothing.
  Each state takes a role from the one status map: work in flight and the
  watch window read as info, the fix waiting on a person as warning, the
  outcomes as success and danger; an honest ending (no safe improvement,
  couldn't reproduce) is neutral.
*/
const TONE = {
  fixing: { icon: "solar:toolbox-linear", role: "info" },
  ready: { icon: "solar:check-circle-linear", role: "warning" },
  review: { icon: "solar:git-pull-request-linear", role: "info" },
  watching: { icon: "solar:eye-linear", role: "info" },
  resolved: { icon: "solar:check-circle-bold", role: "success" },
  reopened: { icon: "solar:restart-linear", role: "danger" },
  "no-fix": { icon: "solar:close-circle-linear", role: "neutral" },
  "not-reproduced": { icon: "solar:close-circle-linear", role: "neutral" },
  "over-budget": { icon: "solar:pause-circle-linear", role: "neutral" },
  stopped: { icon: "solar:stop-circle-linear", role: "neutral" },
};

/* What each state of the fix means, and what happens next. */
const fixTip = (status) => {
  const { key, label } = status;
  if (key === "fixing")
    return "A fix is running: it reproduces the issue, then searches for a change that fixes it without breaking your must-pass scenarios.";
  if (key === "ready") {
    if (status.recheck)
      return "Another change went live after this fix measured the agent. Re-check its change on the version in production before you ship it.";
    if (/budget/i.test(label))
      return "The fix stopped at its budget. The best change it found so far is ready for you to review.";
    if (/suggestion/i.test(label))
      return "The fix suggests a change it couldn't test here. Review it before you ship it.";
    return "The fix found a change that fixes this issue without breaking your must-pass scenarios. Review it and ship it.";
  }
  if (key === "review")
    return status.awaitingRelease
      ? "The change is saved as a draft agent version, not live yet. Mark it released on the fix once it's live — the Error Feed then watches for the issue."
      : "The change was sent as a draft, not live yet. Mark it released on the fix once it's live — the Error Feed then watches for the issue.";
  if (key === "watching")
    return `Released. The Error Feed watches production for ${WATCH_DAYS} days — resolved if it doesn't come back, reopened if it does.`;
  if (key === "resolved")
    return `Fixed: released and not seen for ${WATCH_DAYS} days.`;
  if (key === "reopened")
    return "Seen again on the fixed version. Fix again to try another change.";
  if (/infrastructure/i.test(label))
    return "The fix couldn't finish for infrastructure reasons — not because of the agent. Try again.";
  if (key === "no-fix")
    return "The fix tried changes but none fixed the issue without breaking something else. Nothing was shipped.";
  if (key === "not-reproduced")
    return "The fix couldn't make the issue happen in the environment, so it didn't search. You can continue anyway.";
  if (key === "stopped")
    return "Someone stopped the fix before it finished. Nothing it tried was checked or shipped — Fix again starts a new one.";
  if (key === "over-budget")
    return "The fix stopped at its budget before searching: checking the finalists alone would have cost more. No change was tried — Fix again with a bigger budget.";
  return "Where this issue's fix stands.";
};

/*
  In the list the fix reads as quiet text, not a second chip beside the
  triage one: an icon and the state, its detail underneath — the shape of
  the other cells. Colour only where it means something: Ready for review
  wants a person, Reopened is a regression. No fix reads "—".
*/
const EMPHASIS = {
  ready: { color: "text.primary", role: "warning", weight: 600 },
  reopened: { role: "danger", weight: 600 },
};

export default function IssueStatusChip({ issue, variant = "chip" }) {
  const theme = useTheme();
  const navigate = useNavigate();
  const improving = useAgentImprovementFlag();
  const { status, agent, state } = useIssueFix(issue);
  if (!improving) return null;
  if (!status) {
    if (variant !== "text") return null;
    /* No agent linked to the issue's project: say why there's no fix here,
       not a bare dash — the issue's page has Connect agent to fix. Only once
       the store has loaded, so a connected agent isn't misread. */
    if (state?.hydrated && !agent)
      return (
        <Tooltip
          arrow
          placement="top-start"
          title="No agent is linked to this issue's project yet. Open the issue to connect one, then fix it."
        >
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Connect agent
          </Typography>
        </Tooltip>
      );
    return (
      <Typography sx={{ typography: "s3", color: "text.disabled" }}>
        —
      </Typography>
    );
  }
  const tone = TONE[status.key] || TONE.fixing;
  /* The detail rides along unless it's only a footnote (inconclusive count). */
  const label =
    status.detail &&
    !status.label.includes("·") &&
    status.key !== "not-reproduced"
      ? `${status.label} · ${status.detail}`
      : status.label;
  /* Opens the improvement — only when there is one to open. */
  const open = status.optId
    ? (e) => {
        e.stopPropagation();
        navigate(...issueFixNav(status, issue?.cluster_id));
      }
    : undefined;
  const tip = `${status.detail && label === status.label ? `${status.label} · ${status.detail}. ` : ""}${fixTip(status)}${open ? " Click to open the fix." : ""}`;

  if (variant === "text") {
    /* "Fix in review · Vapi copy" → the state, then its detail underneath. */
    const [head, ...rest] = status.label.split(" · ");
    const detail =
      rest.join(" · ") ||
      (status.key !== "not-reproduced" ? status.detail : null) ||
      null;
    const em = EMPHASIS[status.key];
    return (
      <Tooltip arrow title={tip} placement="top-start">
        <Stack
          direction="row"
          spacing={0.875}
          alignItems="flex-start"
          onClick={open}
          sx={{
            minWidth: 0,
            maxWidth: "100%",
            cursor: open ? "pointer" : "default",
            "&:hover .fix-head": open
              ? { textDecoration: "underline" }
              : undefined,
          }}
        >
          <Iconify
            icon={tone.icon}
            width={14}
            sx={{
              color: em ? statusColors(theme, em.role).fg : "text.subtitle",
              flexShrink: 0,
              mt: "2px",
            }}
          />
          <Box minWidth={0}>
            <Typography
              className="fix-head"
              noWrap
              sx={{
                typography: "s2",
                fontWeight: em?.weight || 500,
                color:
                  em?.color ||
                  (em ? statusColors(theme, em.role).fg : "text.secondary"),
              }}
            >
              {head}
            </Typography>
            {detail && (
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.subtitle" }}
              >
                {detail}
              </Typography>
            )}
          </Box>
        </Stack>
      </Tooltip>
    );
  }

  return (
    <Tooltip arrow title={tip}>
      <span style={{ display: "inline-flex", maxWidth: 220 }}>
        <StatusBadge
          status={status.key}
          role={tone.role}
          label={label}
          icon={<Iconify icon={tone.icon} width={12} />}
          onClick={open}
          sx={{
            cursor: open ? "pointer" : "default",
            ...(open && { "&:hover": { opacity: 0.85 } }),
          }}
        />
      </span>
    </Tooltip>
  );
}

IssueStatusChip.propTypes = {
  issue: PropTypes.object,
  variant: PropTypes.oneOf(["chip", "text"]),
};
