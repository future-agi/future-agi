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
import AgentPicker, {
  useWorldAgents,
} from "../../buildDrawer/starts/AgentPicker";
import ConnectSection from "../../buildDrawer/ConnectSection";
import { connectItemsFor } from "../../buildDrawer/buildDrawerCatalog";
import {
  ConnectModeContext,
  ConnectNewAgentContext,
  connectSubtitleFor,
  connectTitleFor,
} from "../../buildDrawer/connectNewAgent";
import { FORMS_BY_PANEL } from "../../buildDrawer/forms/formsByPanel";
import { BTN_SX, T12, T14 } from "../../buildDrawer/drawerStyles";

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

// The agents of the environment's channel, with the one picked.
function PickAgent({ modality, value, onChange }) {
  const { agents, isLoading } = useWorldAgents(modality);
  return (
    <AgentPicker
      agents={agents}
      isLoading={isLoading}
      value={value}
      onChange={(id) =>
        onChange(
          id,
          agents.find((a) => a.id === id),
        )
      }
      title={`Connected ${TYPE_WORD[modality]} agents`}
      modality={modality}
    />
  );
}
PickAgent.propTypes = {
  modality: PropTypes.oneOf(["voice", "text"]).isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};

/**
 * Connect agent on a saved environment: pick one of the channel's connected
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
              // A just-connected agent isn't in the picker's list until it
              // re-renders, so it's kept as picked when connected.
              onChange={(id, agent) =>
                setPicked((prev) => agent || (prev?.id === id ? prev : null))
              }
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
