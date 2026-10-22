import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useConnectedAgents } from "../store";
import { improverSessionFor, useIntegrations } from "../_mock/integrations";
import { coderStatusOf } from "../_mock/improverAttempts";
import { candidateRouteOf } from "../_mock/candidateRoute";
import { improvementRowsOf } from "./improvementRows";
import StatusPill from "./ImprovementStatusPill";
import ImproverAttempts from "../run/fixmyagent/ImproverAttempts";
import OmegaHandoff from "../run/OmegaHandoff";

/**
 * An improvement the customer's coding agent fixes and Future AGI judges
 * (RSI PRD, Bring your own improver) — opened by handing it an Error Feed
 * issue or a failed run. There's no search of ours to show: the page is the
 * agent's attempts, each with the verdict we sent back, and — once one is
 * accepted — the same next steps as any change that ships.
 */
export default function CodingAgentImprovementView({ env, envState, record, patch, onBack, backLabel, handoff }) {
  const { agents, state: simState } = useConnectedAgents();
  const { prs, improverSessions } = useIntegrations();
  const session = useMemo(() => improverSessionFor(env.id, record.id, { improverSessions }), [env.id, record.id, improverSessions]);
  const tier = candidateRouteOf(envState?.agent).tier;

  /* Attempts arrive over time — re-read while the agent is still working. */
  const [now, setNow] = useState(() => Date.now());
  const coder = coderStatusOf(record, env.id, { now, surface: env.surface, tier });
  useEffect(() => {
    if (coder.key !== "running") return undefined;
    const t = setInterval(() => setNow(Date.now()), 2000);
    return () => clearInterval(t);
  }, [coder.key]);

  const row = useMemo(
    () => improvementRowsOf({ state: simState, agents, prs, now }).find((r) => r.envId === env.id && r.recordId === record.id) || null,
    [simState, agents, prs, now, env.id, record.id],
  );
  const accepted = coder.attempt || null;
  const issueId = record.trigger?.issueId || null;

  return (
    <Box sx={{ height: "100%", overflow: "auto", p: 2, display: "flex", flexDirection: "column" }}>
      <Box sx={{ mb: 2, flexShrink: 0 }}>
        <Button
          size="small" onClick={onBack}
          startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
          sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", ml: -0.75, mb: 0.75, minWidth: 0 }}
        >
          {`Back to ${backLabel}`}
        </Button>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography noWrap sx={{ typography: "m3", fontWeight: 700 }}>{row?.name || record.name}</Typography>
          <StatusPill ending={row?.status} headline={row?.headline} />
        </Stack>
        <Tooltip arrow placement="bottom-start" title={record.id}>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle", mt: 0.25, width: "fit-content", maxWidth: "100%" }}>
            {[row?.identity, env.name, issueId || row?.trigger?.label].filter(Boolean).join(" · ")}
          </Typography>
        </Tooltip>
      </Box>

      {/* ── who fixes it, and who judges ── */}
      <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, px: 2, py: 1.5, mb: 2 }}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>
          {`${coder.improver} writes the fix · Future AGI checks every attempt`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>
          {`It makes each change and submits it for checking. We run it and today's agent on the same scenarios and send back a verdict — it never sees the held-back scenarios and can't edit the ones that judge it.${record.reproduce?.total ? ` Today: reproduced on ${record.reproduce.reproduced || 0} of ${record.reproduce.total} of ${issueId || "the issue"}'s scenarios.` : ""}`}
        </Typography>
      </Box>

      {/* ── its attempts, each with the verdict it got ── */}
      <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
        {session ? (
          <ImproverAttempts
            record={record} env={env} envState={envState} session={session}
            name={row?.name || record.name} searchSpent={0}
          />
        ) : (
          <Typography sx={{ px: 2, py: 2, typography: "s2", color: "text.subtitle" }}>
            {`Waiting for ${coder.improver} to pick up the brief.`}
          </Typography>
        )}
      </Box>

      {/* ── accepted: the same next steps as any change that ships ── */}
      {accepted && (
        <Box sx={{ mt: 3 }}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>What happens next</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
            {"Nothing goes live from here — it's released when someone makes it live where the agent runs."}
          </Typography>
          <OmegaHandoff
            env={env}
            envState={envState}
            patch={patch}
            included={[{ id: accepted.id, kind: "System prompt", title: accepted.title, diff: [] }]}
            projected={accepted.score}
            projectedOn="the scenarios it was shown"
            current={accepted.before}
            willFix={accepted.visible.filter((x) => x.outcome === "fixed").length}
            handoffKey={handoff?.key}
            landedVersion={handoff?.landedVersion}
            provenance={handoff?.provenance}
            onLand={handoff?.onLand}
            onRerun={handoff?.onRerun}
          />
        </Box>
      )}
    </Box>
  );
}

CodingAgentImprovementView.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  record: PropTypes.object.isRequired,
  patch: PropTypes.func,
  onBack: PropTypes.func,
  backLabel: PropTypes.string,
  handoff: PropTypes.object,
};
