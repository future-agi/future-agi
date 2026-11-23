import PropTypes from "prop-types";
import { useCallback, useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  Fade,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import usePrefersReducedMotion from "../../components/usePrefersReducedMotion";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { AgentRow, useWorldAgents } from "../../buildDrawer/starts/AgentPicker";
import { sourceLabel } from "src/sections/agent-playground/connected/connectedAgentLabels";
import ConnectSection from "../../buildDrawer/ConnectSection";
import { connectItemsFor } from "../../buildDrawer/buildDrawerCatalog";
import {
  ConnectModeContext,
  ConnectNewAgentContext,
  connectSubtitleFor,
  connectTitleFor,
} from "../../buildDrawer/connectNewAgent";
import { FORMS_BY_PANEL } from "../../buildDrawer/forms/formsByPanel";
import {
  BTN_SX,
  PANEL_SX,
  T12,
  T13,
  T14,
} from "../../buildDrawer/drawerStyles";

const TYPE_WORD = { voice: "voice", text: "chat" };

function Header({ title, subtitle, onBack, onClose }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        px: 2.5,
        py: 1.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    >
      {onBack && (
        <IconButton
          aria-label="Back"
          size="small"
          onClick={onBack}
          sx={{ color: "text.secondary" }}
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={18} />
        </IconButton>
      )}
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography noWrap sx={{ ...T14, fontWeight: 600 }}>
            {title}
          </Typography>
          <UiOnlyFlag feature="connectAgent" />
        </Stack>
        <Typography sx={{ ...T12, color: "text.secondary" }}>
          {subtitle}
        </Typography>
      </Box>
      <IconButton
        aria-label="Close"
        size="small"
        onClick={onClose}
        sx={{ color: "text.secondary" }}
      >
        <Iconify icon="solar:close-linear" width={18} />
      </IconButton>
    </Stack>
  );
}
Header.propTypes = {
  title: PropTypes.string.isRequired,
  subtitle: PropTypes.string,
  onBack: PropTypes.func,
  onClose: PropTypes.func.isRequired,
};

// Where an agent comes from, how many versions it has and where it's used.
const metaOf = (agent) =>
  [
    agent.source && sourceLabel(agent.source),
    agent.versionCount != null
      ? `${agent.versionCount} version${agent.versionCount === 1 ? "" : "s"}`
      : agent.latestLabel,
    agent.environments?.length &&
      `used in ${agent.environments.map((e) => e.name).join(", ")}`,
  ]
    .filter(Boolean)
    .join(" · ");

// The channel's agents to pick from, then the way to connect a new one.
function PickAgent({ modality, value, onChange, onConnectNew }) {
  const word = TYPE_WORD[modality];
  const { agents, isLoading } = useWorldAgents(modality);
  const { agents: all } = useWorldAgents();
  const other = all.length - agents.length;
  return (
    <Stack spacing={2.5}>
      <Box>
        <Typography sx={{ ...T13, fontWeight: 600, mb: 0.75 }}>
          {`Connected ${word} agents`}
        </Typography>
        {agents.length ? (
          <Box
            role="radiogroup"
            aria-label={`Connected ${word} agents`}
            sx={PANEL_SX}
          >
            {agents.map((a, i) => (
              <AgentRow
                key={a.id}
                agent={a}
                divider={i > 0}
                selected={a.id === value}
                onClick={() => onChange(a)}
                meta={metaOf(a)}
              />
            ))}
          </Box>
        ) : (
          <Typography sx={{ ...T12, color: "text.secondary" }}>
            {isLoading ? "Loading agents…" : `No ${word} agents in Agents yet.`}
          </Typography>
        )}
        {other > 0 && (
          <Typography sx={{ ...T12, color: "text.secondary", mt: 1 }}>
            {`${other} agent${other === 1 ? "" : "s"} on other channels ${other === 1 ? "isn't" : "aren't"} listed. This is a ${word} environment.`}
          </Typography>
        )}
      </Box>
      <Box sx={{ pt: 2, borderTop: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ ...T13, fontWeight: 600 }}>
          Not connected yet?
        </Typography>
        <Typography sx={{ ...T12, color: "text.secondary", mt: 0.25, mb: 1 }}>
          {`Connect it here. Only the ways that bring a ${word} agent are shown.`}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={onConnectNew}
          startIcon={<Iconify icon="solar:link-circle-linear" width={16} />}
          sx={BTN_SX}
        >
          {`Connect a ${word} agent`}
        </Button>
      </Box>
    </Stack>
  );
}
PickAgent.propTypes = {
  modality: PropTypes.oneOf(["voice", "text"]).isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  onConnectNew: PropTypes.func.isRequired,
};

/**
 * Connect agent on a design's environment, saved or not: pick one of the channel's connected
 * agents, or connect a new one in a second drawer offering only the ways that
 * bring an agent of that channel. Connecting there adds a sample agent, as in
 * Build environment; picking isn't sent anywhere yet.
 */
export default function ConnectAgentDrawer({ open, env, onClose, onPick }) {
  const reduce = usePrefersReducedMotion();
  const modality = env?.agentType === "voice" ? "voice" : "text";
  const word = TYPE_WORD[modality];
  const [picked, setPicked] = useState(null);
  const [samples, setSamples] = useState([]);
  // { onConnected, item? } while the second drawer is open.
  const [connecting, setConnecting] = useState(null);

  const requestConnect = useCallback(
    ({ onConnected }) => setConnecting({ onConnected }),
    [],
  );
  const connectApi = useMemo(
    () => ({ requestConnect, samples }),
    [requestConnect, samples],
  );
  const connectMode = useMemo(
    () =>
      connecting?.item && {
        item: connecting.item,
        modality,
        onConnected: (agent) => {
          setSamples((s) => [agent, ...s]);
          setPicked(agent);
          enqueueSnackbar(
            `UI only: ${agent.name} was added as a sample agent.`,
            { variant: "info" },
          );
          connecting.onConnected?.(agent);
          setConnecting(null);
        },
      },
    [connecting, modality],
  );
  const Form = connecting?.item && FORMS_BY_PANEL[connecting.item.setup.panel];

  return (
    <ConnectNewAgentContext.Provider value={connectApi}>
      {/* One drawer at a time: the picker gives way while a new agent is
          connected, and comes back with it picked. */}
      <SideDrawer
        open={open && !connecting}
        onClose={onClose}
        width={560}
        hideClose
      >
        <Stack sx={{ height: "100%", minHeight: 0 }}>
          <Header
            title={`Connect a ${word} agent`}
            subtitle={`Pick the ${word} agent and version this environment tests.`}
            onClose={onClose}
          />
          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2.5 }}>
            <PickAgent
              modality={modality}
              value={picked?.id}
              onChange={setPicked}
              onConnectNew={() => setConnecting({})}
            />
          </Box>
          <Stack
            direction="row"
            justifyContent="flex-end"
            spacing={1}
            sx={{
              px: 2.5,
              py: 1.5,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Button size="small" color="inherit" onClick={onClose} sx={BTN_SX}>
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!picked}
              onClick={() => onPick(picked)}
              sx={BTN_SX}
            >
              Pick an agent
            </Button>
          </Stack>
        </Stack>
      </SideDrawer>
      <SideDrawer
        open={!!connecting}
        onClose={() => setConnecting(null)}
        width={760}
        hideClose
      >
        <Stack sx={{ height: "100%", minHeight: 0 }}>
          <Header
            title={connecting?.item?.name || connectTitleFor(modality)}
            subtitle={connectSubtitleFor(modality)}
            onBack={
              connecting?.item
                ? () => setConnecting((c) => ({ ...c, item: null }))
                : () => setConnecting(null)
            }
            onClose={() => setConnecting(null)}
          />
          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 3, pt: 2.5 }}>
            <Fade
              key={connecting?.item?.id || "ways"}
              in
              appear
              timeout={reduce ? 0 : undefined}
            >
              <Box>
                {Form ? (
                  <ConnectModeContext.Provider value={connectMode}>
                    <Form item={connecting.item} onBusyChange={() => {}} />
                  </ConnectModeContext.Provider>
                ) : (
                  <Box sx={{ pb: 4 }}>
                    <ConnectSection
                      items={connectItemsFor(modality)}
                      head={false}
                      onPick={(item) => setConnecting((c) => ({ ...c, item }))}
                    />
                  </Box>
                )}
              </Box>
            </Fade>
          </Box>
        </Stack>
      </SideDrawer>
    </ConnectNewAgentContext.Provider>
  );
}
ConnectAgentDrawer.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.shape({ agentType: PropTypes.string }),
  onClose: PropTypes.func.isRequired,
  // (agent) the agent picked: { id, name, latestLabel, ... }.
  onPick: PropTypes.func.isRequired,
};
