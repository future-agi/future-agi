import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useSnackbar } from "notistack";
import { Box, Button, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useAgentImprovementFlag } from "../_mock/featureFlags";
import { issueFixNav } from "./issueFix";
import { epochNameOf, useIssueFixHeader } from "./issueFixHeader";
import ConnectAgentDialog from "./ConnectAgentDialog";
import FixStartDialog from "./FixStartDialog";
import useFixPlan from "./useFixPlan";
import { clickable, focusRing } from "../components/clickable";

/*
  What the button does at each point of the fix (RSI PRD, Flow 3 and Part 3
  "Improvement states"): an issue has at most one fix in flight. Once a fix
  is running, waiting for review, shipped or being watched, the button opens
  that fix — it never starts a second one against the version the first fix
  produced. Only an ending that left the issue unfixed (no safe improvement,
  could not reproduce, failed, stopped at budget before searching) or a
  Reopened issue offers Fix again.
*/
const IN_FLIGHT = {
  fixing: {
    label: "View",
    why: "An epoch is running on this issue — open it to follow the search.",
  },
  ready: {
    label: "Review",
    why: "The epoch finished — review the verdict, then open a pull request (a draft if it isn't Verified).",
  },
  review: {
    label: "View",
    why: "The epoch's change is shipped. Once deployed, mark it released.",
  },
  watching: {
    label: "View",
    why: "The change is released and Error Feed is watching. If the issue comes back, it reopens.",
  },
  resolved: {
    label: "View",
    why: "The change held through the watch window.",
  },
};
const AGAIN = new Set([
  "reopened",
  "no-fix",
  "not-reproduced",
  "over-budget",
  "stopped",
]);
/* What Fix again would do differently, where there's something to say. */
const AGAIN_HOW = { "over-budget": "Run Epoch again with a bigger budget" };

/**
 * Fix on an Error Feed issue. It opens the Fix sheet — Future AGI's engine
 * finds and proves the change. (Your own coding agent comes in through Copy
 * for coding agent instead, PRD Flow 6.)
 *
 * The agent is never picked here. An agent in Agents records the Observe
 * project its traces land in (PRD, Agents → Links); Fix reads that. An issue
 * whose project no agent records reads Link agent (agents exist) or Connect
 * agent (none yet): a dialog over the issue links or connects one with this
 * project already linked. A stopped epoch reads Retry Epoch N.
 *
 * Fix opens on any issue: its first step reproduces it, so a noisy one stops
 * at Could not reproduce before a search is spent (the sheet says when the
 * feed hasn't confirmed it yet).
 */
export default function FixButton({ issue, onOpenSheet, rootCause = null }) {
  const navigate = useNavigate();
  const improving = useAgentImprovementFlag();
  const { enqueueSnackbar } = useSnackbar();
  const [, setParams] = useSearchParams();
  /* The fix with its defaults — its price on the button, and what the
     confirm starts. Adjust (onOpenSheet) is the full plan to edit. */
  /* The environment picked in the popup — this fix only. */
  const [envId, setEnvId] = useState(null);
  const plan = useFixPlan(issue, rootCause, envId);
  const { agent, state, gate, status, retry } = plan;
  /* What the header says: what's missing first, then how the last epoch ended. */
  const { link, prereq, ended } = useIssueFixHeader(issue);
  const [confirming, setConfirming] = useState(false);
  /* Controlled so a link inside it closes it before it navigates. */
  const [tip, setTip] = useState(false);
  const [connecting, setConnecting] = useState(false);
  if (!improving) return null;

  const openFix = () =>
    status?.optId && navigate(...issueFixNav(status, issue?.cluster_id));
  /* Where a gate is answered: Connections (consent, a disconnect) or Overview (Check what we found). */
  const openConnections = (tab = "connections") =>
    navigate(`${paths.dashboard.agents}/connected/${agent.id}?tab=${tab}`);

  /* Until the store has loaded, say nothing — not "Connect agent" for an
     agent that is connected but not read yet. */
  if (!state?.hydrated) return null;

  /* Nothing to run on yet: connect an agent, or link one to this project.
     The dialog opens on your agents when there are some. */
  if (prereq) {
    const noAgent = prereq === "no-agent";
    return (
      <>
        <Tooltip
          arrow
          title={
            noAgent
              ? `Epochs run on your agent. Connect the one whose traces land in ${link.name || "this project"}.`
              : `Link the agent whose traces land in ${link.name || "this project"}. Every issue from it can then run an epoch.`
          }
        >
          <Button
            variant="contained"
            color="primary"
            startIcon={
              <Iconify
                icon={noAgent ? "solar:link-circle-linear" : "solar:link-linear"}
                width={14}
              />
            }
            onClick={() => setConnecting(true)}
          >
            {noAgent ? "Connect agent" : "Link agent"}
          </Button>
        </Tooltip>
        <ConnectAgentDialog
          open={connecting}
          onClose={() => setConnecting(false)}
          issue={issue}
          project={link.project}
          noAgents={noAgent}
        />
      </>
    );
  }

  /* The last epoch couldn't run: Retry runs that same epoch again. */
  if (ended?.kind === "stopped" && ended.canRetry) {
    const n = ended.name;
    const onRetry = () => {
      if (ended.demo) {
        setParams(
          (p) => {
            const next = new URLSearchParams(p);
            next.delete("epoch");
            return next;
          },
          { replace: true },
        );
      } else retry();
      enqueueSnackbar(`${n} is running again`, { variant: "success" });
    };
    return (
      <Tooltip
        arrow
        title={`Runs ${n} again with the same scenarios and limits. Keeps the check runs that finished.`}
      >
        <Button
          variant="contained"
          color="primary"
          onClick={onRetry}
          startIcon={<Iconify icon="solar:restart-linear" width={14} />}
        >
          {`Retry ${n}`}
        </Button>
      </Tooltip>
    );
  }

  /* A fix already in flight: open it. */
  const inFlight = status && IN_FLIGHT[status.key];
  if (agent && inFlight && !ended) {
    return (
      <Tooltip
        arrow
        title={`${status.label}${status.detail ? ` · ${status.detail}` : ""}. ${inFlight.why}`}
      >
        <Button
          /* The header's one main action, the same look in every state
             (#80) — Review fix, View fix or Fix. */
          variant="contained"
          color="primary"
          onClick={openFix}
          startIcon={<Iconify icon="fagi:fix" width={14} />}
        >
          {`${inFlight.label} ${epochNameOf(state, status) || "Epoch"}`}
        </Button>
      </Tooltip>
    );
  }

  /* Disconnected or archived — said on the button and in the row under
     the header, with the way out. Live-test consent isn't one of them: the
     sheet asks for it, right where it's needed, before anything runs. */
  /* "Check what we found" on a new code agent is advice, not a stop: the
     tooltip still says it, with the link, but Fix stays on — it reproduces
     the issue before it searches. */
  const hardGate =
    gate && gate.key !== "consent" && gate.key !== "found" ? gate : null;
  const shownGate = hardGate || (gate?.key === "found" ? gate : null);
  const blocked = !!hardGate;
  /* Ended without a fix, or reopened: Fix again starts a new epoch; the
     last one stays on its page (View Epoch N under the header). */
  const again =
    !!ended || (!!status && (AGAIN.has(status.key) || status.key === "reopened"));

  const why = shownGate ? (
    <Box sx={{ py: 0.25 }}>
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>
        {shownGate.title}
      </Typography>
      <Typography sx={{ typography: "s3", mt: 0.25 }}>
        {shownGate.body}
      </Typography>
      {(shownGate.action === "connections" ||
        shownGate.action === "overview") && (
        <Typography
          {...clickable(() => {
            setTip(false);
            openConnections(shownGate.action);
          })}
          sx={{
            typography: "s3",
            fontWeight: 600,
            mt: 0.5,
            cursor: "pointer",
            textDecoration: "underline",
            ...focusRing,
          }}
        >
          {shownGate.action === "overview"
            ? "Open the agent's Overview"
            : "Open the agent's Connections tab"}
        </Typography>
      )}
    </Box>
  ) : again ? (
    `${["no-fix", "not-reproduced"].includes(status?.key) ? "The last epoch says what to try first — the same epoch again would likely end the same way. " : ""}${AGAIN_HOW[status?.key] || "Run Epoch again"} starts a new epoch; the last one stays on its page.`
  ) : gate?.key === "consent" ? (
    `${agent.name} runs live — Run Epoch asks for your consent before its first test.`
  ) : (
    "Runs an epoch on this issue: it reproduces the failure first, then searches for a change that fixes it."
  );

  return (
    <>
      <Tooltip
        arrow
        title={why}
        open={tip && !!why}
        onOpen={() => setTip(true)}
        onClose={() => setTip(false)}
      >
        <span>
          <Button
            variant="contained"
            color="primary"
            disabled={blocked}
            startIcon={<Iconify icon="fagi:fix" width={14} />}
            onClick={() => {
              setTip(false);
              setConfirming(true);
            }}
          >
            {again ? "Run Epoch again" : "Run Epoch"}
          </Button>
        </span>
      </Tooltip>
      {!blocked && (
        <FixStartDialog
          open={confirming}
          onClose={() => setConfirming(false)}
          issue={issue}
          plan={plan}
          onEnv={setEnvId}
          onAdjust={() => onOpenSheet?.(plan.main?.id || null)}
        />
      )}
    </>
  );
}

FixButton.propTypes = {
  issue: PropTypes.object,
  onOpenSheet: PropTypes.func,
  rootCause: PropTypes.object,
};
