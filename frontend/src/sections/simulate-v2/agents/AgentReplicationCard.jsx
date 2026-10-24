import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  MODE_ICONS,
  cloneNameFor,
  replicationOf,
  webhookUrlFor,
} from "../_mock/replication";
import {
  EndpointConsent,
  FidelityBadge,
  RunLocationChoice,
} from "./connect/replicationBits";
import { MONO } from "./agentViewUtils";
import { InfoTip } from "./AgentBits";

/**
 * How the platform copies this agent (AC-1.9) — on the Connections tab, next
 * to how we reach it. The copy, what stays untouched, the run fidelity its
 * results carry; where a code agent runs (editable, Story 6); and, for an
 * opaque endpoint, the acknowledgment and the optional webhook (AC-1.12).
 *   clones — [{ envId, envName, cloneName }] for a provider clone
 */
export default function AgentReplicationCard({
  agent,
  env,
  clones = [],
  onSaveValues,
}) {
  const r = replicationOf(agent, env);
  const rows = [
    {
      label: "Copy",
      body: (
        <>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Iconify
              icon={MODE_ICONS[r.mode]}
              width={14}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {r.label}
              <InfoTip text={r.how} />
            </Typography>
          </Stack>
        </>
      ),
    },
    {
      label: "Left untouched",
      body: <Typography sx={{ typography: "s2" }}>{r.untouched}</Typography>,
    },
    /* Proof and run fidelity in one row — they read the same unless the
       runs are played back rather than placed live. */
    {
      label: "Proof",
      body: (
        <Typography sx={{ typography: "s2" }}>
          <Box component="span" sx={{ fontWeight: 600 }}>
            {`${r.proofLabel} · ${r.tierLabel}${r.fidelityLabel && r.fidelityLabel !== r.proofLabel ? ` · ${r.fidelityLabel}` : ""}`}
          </Box>
          <InfoTip
            text={[
              r.proofNote,
              r.fidelityLabel !== r.proofLabel ? r.fidelityNote : null,
            ]
              .filter(Boolean)
              .join(" ")}
          />
        </Typography>
      ),
    },
    ...(r.mode === "provider_clone" && clones.length
      ? [
          {
            label: clones.length === 1 ? "Test copy" : "Test copies",
            body: (
              <Stack spacing={0.25}>
                {clones.map((c) => (
                  <Typography key={c.envId} noWrap sx={{ typography: "s2" }}>
                    <Box component="span" sx={{ fontFamily: MONO }}>
                      {c.cloneName}
                    </Box>
                    <Box
                      component="span"
                      sx={{ color: "text.subtitle" }}
                    >{` · ${c.envName}`}</Box>
                  </Typography>
                ))}
              </Stack>
            ),
          },
        ]
      : []),
  ];

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
          How we test it
          {r.mode === "provider_clone" && (
            <InfoTip
              text={`Each environment gets its own test copy in your account, named fagi-sim-<environment>-<build>${env ? ` — e.g. ${cloneNameFor(env)}` : ""}. Archiving the agent removes them.`}
            />
          )}
        </Typography>
        <FidelityBadge
          fidelity={r.proofKind === "live" ? "live" : r.fidelity}
          label={r.proofLabel}
        />
      </Stack>
      {rows.map((row, i) => (
        <Stack
          key={row.label}
          direction="row"
          spacing={2}
          sx={{
            px: 2,
            py: 1,
            borderTop: i ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s2",
              color: "text.subtitle",
              width: 150,
              flexShrink: 0,
            }}
          >
            {row.label}
          </Typography>
          <Box flex={1} minWidth={0}>
            {row.body}
          </Box>
        </Stack>
      ))}

      {r.runLocation && (
        <Box
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
            Where it runs
          </Typography>
          {/* Only the hosted sandbox can be chosen today — nothing to save. */}
          <RunLocationChoice value={r.runLocation} dense />
        </Box>
      )}

      {r.mode === "wrapped_endpoint" && (
        <Box
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
            Live traffic
          </Typography>
          <EndpointConsent
            value={r.consent}
            webhookUrl={webhookUrlFor(agent)}
            lead="Runs are blocked until this is acknowledged"
            onChange={(next) =>
              onSaveValues(
                { endpointConsent: next },
                !next
                  ? "Acknowledgment withdrawn — runs of this agent are blocked until it's given again."
                  : next.webhookAt && !r.consent?.webhookAt
                    ? "Webhook recorded — runs now show the tool calls behind each turn."
                    : !next.webhookAt && r.consent?.webhookAt
                      ? "Webhook removed — fidelity is back to Observed only."
                      : "Acknowledged — simulations can drive this agent.",
              )
            }
          />
        </Box>
      )}
    </Box>
  );
}

AgentReplicationCard.propTypes = {
  agent: PropTypes.object.isRequired,
  env: PropTypes.object,
  clones: PropTypes.array,
  onSaveValues: PropTypes.func.isRequired,
};
