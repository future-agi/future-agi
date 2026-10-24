import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { useConnectedAgents } from "../../store";
import { agentRecordFromSource } from "../../_mock/agentRegistry";
import ConnectAgentPicker from "../../agents/connect/ConnectAgentPicker";
import { channelLabel } from "../../_mock/nudgeEffects";

/**
 * "Connect a new agent" from an environment — the same flow as Agents →
 * New agent (hosted platform, repository, built in Future AGI, framework
 * adapter, endpoint, upload),
 * so an agent connected here is the same record, with the same identity,
 * as one connected anywhere else. Connecting an agent that's already
 * connected picks up its history instead of starting a second one — and
 * fills in whatever the form added (an endpoint, a name, a linked repo).
 *
 * `channel` is the environment's: only ways to bring that type of agent are
 * offered, and every flow opens with the type set.
 */
export default function ConnectNewAgentDrawer({
  open,
  onClose,
  onConnected,
  channel = null,
}) {
  const type = channel ? channelLabel(channel).toLowerCase() : null;
  const { agents, connectAgent } = useConnectedAgents();

  const onConnect = (source) => {
    const now = new Date().toISOString();
    const record = agentRecordFromSource(source, now);
    const existing = agents.find((a) => a.key === record.key);
    if (existing) {
      /* The store merges the new fields into the existing agent; the
         environment's copy gets the same merge. */
      connectAgent(record);
      const {
        usedIn,
        formerlyIn,
        latest,
        id,
        updatedAt,
        lastSyncedAt,
        origin,
        firstEnvName,
        versions,
        ...rest
      } = existing;
      const filled = Object.fromEntries(
        Object.entries(record.values || {}).filter(
          ([, val]) => val !== undefined && val !== "",
        ),
      );
      const agent = {
        ...rest,
        name: rest.name || record.name,
        values: { ...(rest.values || {}), ...filled },
        definition:
          rest.definition?.repoUrl || !record.definition?.repoUrl
            ? rest.definition
            : { ...(rest.definition || {}), ...record.definition },
      };
      onConnected({
        agent,
        versions,
        activeLabel: latest,
        name: agent.name,
        existing: true,
      });
      return;
    }
    const versions = [
      {
        id: "agent-v1",
        label: "v1",
        note: source.versionNote || "First version connected.",
        createdAt: now,
        connectedAt: now,
        origin: "connect",
      },
    ];
    connectAgent({
      ...record,
      values: { ...(record.values || {}), setupStartedAt: now },
      versions,
    });
    onConnected({
      agent: record,
      versions,
      activeLabel: "v1",
      name: record.name,
    });
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={2}
          sx={{
            px: 2.5,
            py: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              {type ? `Connect a ${type} agent` : "Connect a new agent"}
            </Typography>
            <Typography
              sx={{ typography: "s2", color: "text.tertiary", mt: 0.25 }}
            >
              {type
                ? `This is a ${type} environment, so only ways to connect a ${type} agent are shown. It joins your connected agents, and this environment tests it.`
                : "It joins your connected agents, and this environment tests it. An agent it tested before keeps its runs."}
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose}>
            <Iconify
              icon="solar:close-circle-linear"
              width={20}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Stack>
        <Box sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
          {open && (
            <ConnectAgentPicker
              onConnect={onConnect}
              flowsIn="inline"
              channel={channel}
            />
          )}
        </Box>
      </Stack>
    </SideDrawer>
  );
}

ConnectNewAgentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onConnected: PropTypes.func.isRequired,
  channel: PropTypes.string,
};
