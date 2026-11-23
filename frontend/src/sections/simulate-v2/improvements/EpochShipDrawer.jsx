import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { improvementOutcome } from "../_mock/optimizationRuns";
import { agentSourceOf } from "../_mock/agentRegistry";
import { useIntegrations } from "../_mock/integrations";
import { useSimStore } from "../store";
import { shippedStateOf } from "../errorFeed/issueFixModel";
import OmegaHandoff from "../run/OmegaHandoff";
import { chosenVerified, shippedCandidate } from "../run/fixmyagent/tradeoffs";
import { agentModelOf, epochBriefOf, epochProofMarkdown } from "./epochBrief";
import { changeCountLine } from "./epochShip";

/**
 * Ship, on its own: what ships in one line (the diff is on the Changes tab
 * behind it), then the numbered steps — make the draft where the agent
 * lives, make it live there, released. One primary: the step it's on.
 */
export default function EpochShipDrawer({
  record,
  name,
  env,
  envState,
  patch,
  handoff,
  tasks,
  onRunWinner,
  onSeeChanges,
  onClose,
}) {
  const { state } = useSimStore();
  const integrations = useIntegrations();
  const result = record?.result;
  const winner = result?.winner || null;
  const ships = shippedCandidate(record) || winner;
  if (!result || !ships) return null;
  const agent = envState?.agent || null;
  const outcome = improvementOutcome(record, {
    model: agentModelOf(env, envState),
  });
  const verified = chosenVerified(record);
  const lifecycle = shippedStateOf(record, envState, integrations.prs || [], {
    envId: env?.id,
  });
  const proposals = ships.proposals || [];
  const strength =
    outcome?.strength?.key === "proven"
      ? "Verified on held-back and must-pass scenarios"
      : `${outcome?.strength?.label || "Limited validation"} — checked on its target and must-pass scenarios, not proven on enough unseen ones`;
  const brief = () =>
    epochBriefOf({ record, env, envState, state, name, tasks });

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{ typography: "s1", fontWeight: 600 }}
          >{`Ship Candidate ${ships.n}`}</Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {[
              agent?.name || env?.name,
              agent ? agentSourceOf(agent).label : null,
              record.trigger?.issueId,
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        </Box>
        <IconButton size="small" aria-label="Close" onClick={onClose}>
          <Iconify icon="solar:close-linear" width={16} />
        </IconButton>
      </Stack>
      <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0, px: 2.5, py: 2.5 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          What ships
        </Typography>
        {/* One line — the diff itself is on the Changes tab behind this. */}
        <Typography
          sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
        >
          {`${changeCountLine(env, proposals)} · ${strength}`}
          {onSeeChanges && (
            <>
              {" · "}
              <Link
                component="button"
                type="button"
                underline="hover"
                onClick={onSeeChanges}
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  color: "text.primary",
                  verticalAlign: "baseline",
                }}
              >
                See changes
              </Link>
            </>
          )}
        </Typography>
        <Typography sx={{ typography: "s2", fontWeight: 600, mt: 3 }}>
          What happens next
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 1 }}>
          {
            "Nothing goes live from here — it's released when someone makes it live where the agent runs."
          }
        </Typography>
        {/* The verdict controls the actions: a candidate that didn't verify
            gets no pull request or draft — the current version stays. */}
        {outcome.strength?.key === "none" && !lifecycle ? (
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Inconclusive — it didn&apos;t fix enough of the held-back scenarios
            to call it a fix, so there&apos;s nothing to ship. The current
            version stays.
          </Typography>
        ) : verified || lifecycle ? (
          <OmegaHandoff
            env={env}
            envState={envState}
            patch={patch}
            included={proposals}
            /* Nothing held back: the re-run is held to the target scenarios instead. */
            projected={
              result.split?.thin ? ships.score : ships.held ?? result.heldScore
            }
            projectedOn={
              result.split?.thin
                ? "the scenarios it was tuned on"
                : "held-back scenarios"
            }
            current={result.split?.thin ? result.base : result.heldBase}
            willFix={ships.fixed || 0}
            handoffKey={handoff?.key}
            landedVersion={handoff?.landedVersion}
            provenance={handoff?.provenance}
            onLand={handoff?.onLand}
            onRerun={handoff?.onRerun}
            brief={brief}
            briefTarget={
              env?.id
                ? { kind: "improvement", envId: env.id, optId: record.id }
                : undefined
            }
            validation={
              outcome.strength?.key === "none" ? undefined : outcome.strength
            }
            proof={epochProofMarkdown({ record, env, envState, state, name })}
          />
        ) : (
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => onRunWinner?.(null)}
            disabled={!onRunWinner}
            startIcon={<Iconify icon="solar:play-bold" width={14} />}
          >
            {`Verify Candidate ${ships.n}`}
          </Button>
        )}
      </Box>
    </Stack>
  );
}

EpochShipDrawer.propTypes = {
  record: PropTypes.object.isRequired,
  name: PropTypes.string,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  handoff: PropTypes.object,
  tasks: PropTypes.array,
  onRunWinner: PropTypes.func,
  /* Closes the drawer onto the Changes tab. */
  onSeeChanges: PropTypes.func,
  onClose: PropTypes.func,
};
