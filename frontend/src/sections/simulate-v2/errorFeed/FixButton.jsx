import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useAgentImprovementFlag } from "../_mock/featureFlags";
import { issueFixNav, issueProjectLinkOf, useIssueFix } from "./issueFix";
import ConnectAgentDialog from "./ConnectAgentDialog";
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
    label: "View fix",
    why: "A fix is running for this issue — open it to follow the search.",
  },
  ready: {
    label: "Review fix",
    why: "The fix finished — review the verdict and ship it as a draft.",
  },
  review: {
    label: "View fix",
    why: "The fix is shipped as a draft. Once it's deployed, mark it released on the fix — the Error Feed then watches for the issue.",
  },
  watching: {
    label: "View fix",
    why: "The fix is released and the Error Feed is watching for this issue. If it comes back, the issue reopens and Fix again starts a new fix.",
  },
  resolved: {
    label: "View fix",
    why: "The fix held through the watch window.",
  },
};
const AGAIN = new Set(["reopened", "no-fix", "not-reproduced", "over-budget"]);
/* What Fix again would do differently, where there's something to say. */
const AGAIN_HOW = { "over-budget": "Fix again with a bigger budget" };

/**
 * Fix on an Error Feed issue. It opens the Fix sheet — Future AGI's engine
 * finds and proves the change. (Your own coding agent comes in through Copy
 * for coding agent instead, PRD Flow 6.)
 *
 * The agent is never picked here. An agent in Agents records the Observe
 * project its traces land in (PRD, Agents → Links); Fix reads that. An issue
 * whose project no agent records reads Connect agent to fix (Flow 3): a
 * dialog over the issue connects it with this project already linked.
 *
 * Fix opens on any issue: its first step reproduces it, so a noisy one stops
 * at Could not reproduce before a search is spent (the sheet says when the
 * feed hasn't confirmed it yet).
 */
export default function FixButton({ issue, onOpenSheet }) {
  const navigate = useNavigate();
  const improving = useAgentImprovementFlag();
  const { agent, agents, state, gate, status } = useIssueFix(issue);
  /* Controlled so a link inside it closes it before it navigates. */
  const [tip, setTip] = useState(false);
  const [connecting, setConnecting] = useState(false);
  /* The issue's project, and the agent that records it. */
  const link = issueProjectLinkOf(issue, state, agents);
  if (!improving) return null;

  const openFix = () =>
    status?.optId && navigate(...issueFixNav(status, issue?.cluster_id));
  /* Where a gate is answered: Connections (consent, a disconnect) or Overview (Check what we found). */
  const openConnections = (tab = "connections") =>
    navigate(`${paths.dashboard.agents}/connected/${agent.id}?tab=${tab}`);

  /* Reopened with the change it came back on: the banner under the header
     carries View fix and Fix again, so the header doesn't repeat them. */
  if (agent && status?.key === "reopened" && status.change) return null;

  /* A fix already in flight: open it. */
  const inFlight = status && IN_FLIGHT[status.key];
  if (agent && inFlight) {
    return (
      <Tooltip
        arrow
        title={`${status.label}${status.detail ? ` · ${status.detail}` : ""}. ${inFlight.why}`}
      >
        <Button
          variant="outlined"
          onClick={openFix}
          startIcon={<Iconify icon="solar:toolbox-linear" width={14} />}
        >
          {inFlight.label}
        </Button>
      </Tooltip>
    );
  }

  /* Disconnected, archived or Coming soon — said on the button, with the
     way out when there is one. Live-test consent isn't one of them: the
     sheet asks for it, right where it's needed, before anything runs. */
  const hardGate = gate && gate.key !== "consent" ? gate : null;
  const blocked = !!agent && !!hardGate;
  const again = !!status && AGAIN.has(status.key);

  const why = hardGate ? (
    <Box sx={{ py: 0.25 }}>
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>
        {hardGate.title}
      </Typography>
      <Typography sx={{ typography: "s3", mt: 0.25 }}>
        {hardGate.body}
      </Typography>
      {(hardGate.action === "connections" ||
        hardGate.action === "overview") && (
        <Typography
          {...clickable(() => {
            setTip(false);
            openConnections(hardGate.action);
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
          {hardGate.action === "overview"
            ? "Open the agent's Overview"
            : "Open the agent's Connections tab"}
        </Typography>
      )}
    </Box>
  ) : !agent ? (
    `No agent in Agents is linked to ${link.name || "this issue's project"}. Connect it once — this project comes with it, and Fix works for every issue from it.`
  ) : again ? (
    `${status.label}${status.detail ? ` · ${status.detail}` : ""}. ${AGAIN_HOW[status.key] || "Fix again"} starts a new fix; the last one stays on its page.`
  ) : gate?.key === "consent" ? (
    `${agent.name} runs live — Fix asks for your consent before its first test.`
  ) : (
    ""
  );

  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      {again && (
        <Button size="small" onClick={openFix}>
          Last fix
        </Button>
      )}
      <Tooltip
        arrow
        title={why}
        open={tip && !!why}
        onOpen={() => setTip(true)}
        onClose={() => setTip(false)}
      >
        <span>
          {agent ? (
            <Button
              variant="contained"
              color="primary"
              disabled={blocked}
              startIcon={<Iconify icon="solar:toolbox-linear" width={14} />}
              onClick={onOpenSheet}
            >
              {again ? "Fix again" : "Fix"}
            </Button>
          ) : (
            <Button
              variant="contained"
              color="primary"
              startIcon={<Iconify icon="solar:toolbox-linear" width={14} />}
              onClick={() => {
                setTip(false);
                setConnecting(true);
              }}
            >
              Connect agent to fix
            </Button>
          )}
        </span>
      </Tooltip>
      {!agent && (
        <ConnectAgentDialog
          open={connecting}
          onClose={() => setConnecting(false)}
          issue={issue}
          project={link.project}
        />
      )}
    </Stack>
  );
}

FixButton.propTypes = { issue: PropTypes.object, onOpenSheet: PropTypes.func };
