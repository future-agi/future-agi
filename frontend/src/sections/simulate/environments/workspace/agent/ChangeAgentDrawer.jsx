import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  ButtonBase,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface } from "src/api/simulate-environments/_fixtures/surfaces";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import { toVersion } from "src/api/agents/agentDetail";
import { sourceLabel } from "src/sections/agent-playground/connected/connectedAgentLabels";
import SideDrawer from "../../components/SideDrawer";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import BuildEnvironmentDrawer from "../../buildDrawer/BuildEnvironmentDrawer";
import { useWorldAgents } from "../../buildDrawer/starts/AgentPicker";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";

// An environment's channel ("voice" or "chat") as the agents list's modality
// ("voice" or "text"), and as the word the copy uses.
const MODALITY = { voice: "voice", chat: "text", text: "text" };
const WORD = { voice: "voice", text: "chat" };

const sectionLabel = {
  typography: "s3",
  fontWeight: 600,
  letterSpacing: 0.6,
  mb: 0.75,
};

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// The list sends at most five environment names; the count is the total.
const usedIn = ({ environments, environmentCount }) => {
  const names = (environments || []).map((e) => e.name).filter(Boolean);
  if (!names.length) return null;
  const more = (environmentCount || 0) - names.length;
  return `used in ${names.join(", ")}${more > 0 ? ` and ${more} more` : ""}`;
};

// "Vapi · 3 versions · used in Billing env", from the agents list's real fields.
const agentMeta = (agent) =>
  [
    agent.source && sourceLabel(agent.source),
    agent.versionCount != null && plural(agent.versionCount, "version"),
    usedIn(agent),
  ]
    .filter(Boolean)
    .join(" · ") || "-";

/**
 * Vel's Change agent drawer: pick the agent this environment tests, and its
 * version. Only connected agents of the environment's channel are offered; a
 * voice environment never tests a chat agent. The list and each agent's
 * versions are real. Attaching an agent to an environment has no API yet, so
 * the final action is UI only and changes nothing. "Connect a voice agent"
 * opens the build drawer's ways to connect, filtered to the channel.
 */
export default function ChangeAgentDrawer({ open, onClose, env }) {
  const modality = MODALITY[env?.surface] || null;
  // null, "connect" (the ways to connect an agent of this channel) or "build".
  const [build, setBuild] = useState(null);
  const openBuild = (kind) => {
    onClose?.();
    setBuild(kind);
  };
  return (
    <>
      <SideDrawer open={open} onClose={onClose} width={480}>
        <ChangeAgentBody
          env={env}
          modality={modality}
          onClose={onClose}
          onConnectNew={() => openBuild("connect")}
          onBuildNew={() => openBuild("build")}
        />
      </SideDrawer>
      {build && (
        <BuildEnvironmentDrawer
          open
          onClose={() => setBuild(null)}
          connect={
            build === "connect"
              ? { modality, onConnected: () => setBuild(null) }
              : undefined
          }
        />
      )}
    </>
  );
}

ChangeAgentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  // Its channel decides which agents are offered; its testSubject is left out.
  env: PropTypes.shape({
    surface: PropTypes.string,
    testSubject: PropTypes.shape({ id: PropTypes.string }),
  }),
};

// Mounted only while the drawer is open, so the queries run then and a pick
// starts fresh each time.
function ChangeAgentBody({ env, modality, onClose, onConnectNew, onBuildNew }) {
  const word = WORD[modality];
  const agentWord = word ? `${word} agent` : "agent";
  const currentId = env?.testSubject?.id;
  const { agents: all, isLoading } = useWorldAgents();
  const candidates = useMemo(
    () => all.filter((a) => !a.sample && a.id !== currentId),
    [all, currentId],
  );
  const offered = useMemo(
    () =>
      modality ? candidates.filter((a) => a.modality === modality) : candidates,
    [candidates, modality],
  );
  const otherType = candidates.length - offered.length;
  const channelIcon = getSurface(env?.surface)?.icon || "solar:cpu-bolt-linear";

  const [pickedId, setPickedId] = useState(null);
  const [version, setVersion] = useState(null);
  const picked = offered.find((a) => a.id === pickedId) || null;
  const versionsQuery = useAgentDefinitionVersions({
    selectedAgentId: picked?.id,
  });
  const versions = useMemo(
    () =>
      (versionsQuery.data?.pages?.[0]?.results ?? []).map((v) => toVersion(v)),
    [versionsQuery.data],
  );
  // The active version by default, as the agent's page marks it.
  const chosen =
    versions.find((v) => v.id === version) ||
    versions.find((v) => v.isActive) ||
    versions[0] ||
    null;

  const pick = (id) => {
    setPickedId(id);
    setVersion(null);
  };
  const confirm = () => {
    onClose?.();
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
  };

  return (
    <Stack sx={{ height: "100%" }}>
      <Box
        sx={{
          pl: 2.5,
          pr: 6,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography sx={{ typography: "m3", fontWeight: 600 }}>
            {`Connect ${word ? `a ${agentWord}` : "an agent"}`}
          </Typography>
          <UiOnlyFlag feature="connectAgent" />
        </Stack>
        <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25 }}>
          {`Pick the ${agentWord} this environment tests, and the version. The world, scenarios and evaluations apply as they are.`}
        </Typography>
      </Box>

      <Stack spacing={2.5} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
        <Box>
          <Typography sx={sectionLabel}>{`Connected ${agentWord}s`}</Typography>
          {offered.length ? (
            <Stack spacing={0.75} role="radiogroup">
              {offered.map((a) => {
                const on = a.id === pickedId;
                return (
                  <ButtonBase
                    key={a.id}
                    role="radio"
                    aria-checked={on}
                    onClick={() => pick(a.id)}
                    sx={{
                      px: 1.5,
                      py: 1,
                      gap: 1.25,
                      display: "flex",
                      alignItems: "center",
                      textAlign: "left",
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: on ? "text.primary" : "divider",
                      bgcolor: on ? "action.hover" : "transparent",
                    }}
                  >
                    <Iconify
                      icon={channelIcon}
                      width={16}
                      sx={{ color: "text.secondary", flexShrink: 0 }}
                    />
                    <Box flex={1} minWidth={0}>
                      <Typography
                        noWrap
                        sx={{ typography: "s2", fontWeight: 500 }}
                      >
                        {a.name}
                      </Typography>
                      <Typography
                        noWrap
                        sx={{ typography: "s3", color: "text.subtitle" }}
                      >
                        {agentMeta(a)}
                      </Typography>
                    </Box>
                    {on && (
                      <Iconify
                        icon="solar:check-circle-bold"
                        width={16}
                        sx={{ color: "text.primary", flexShrink: 0 }}
                      />
                    )}
                  </ButtonBase>
                );
              })}
            </Stack>
          ) : (
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {isLoading
                ? "Loading agents…"
                : `No ${agentWord}s are connected yet.`}
            </Typography>
          )}
          {modality && otherType > 0 && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}
            >
              {`${plural(otherType, "agent")} on other channels ${otherType === 1 ? "isn't" : "aren't"} listed. This is a ${word} environment. `}
              <Box
                component="button"
                type="button"
                onClick={onBuildNew}
                sx={{
                  p: 0,
                  border: 0,
                  bgcolor: "transparent",
                  font: "inherit",
                  color: "text.primary",
                  fontWeight: 600,
                  cursor: "pointer",
                  "&:hover": { textDecoration: "underline" },
                }}
              >
                Build an environment for one
              </Box>
            </Typography>
          )}
        </Box>

        {picked && (
          <Box>
            <Typography sx={sectionLabel}>Version to test</Typography>
            {versions.length ? (
              <Select
                size="small"
                value={chosen?.id ?? ""}
                onChange={(e) => setVersion(e.target.value)}
                SelectDisplayProps={{ "aria-label": "Version to test" }}
                sx={{ minWidth: 180, typography: "s2" }}
              >
                {versions.map((v) => (
                  <MenuItem key={v.id} value={v.id} sx={{ typography: "s2" }}>
                    {`${v.label}${v.isActive ? " · active" : ""}`}
                  </MenuItem>
                ))}
              </Select>
            ) : (
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                {versionsQuery.isLoading ? "Loading versions…" : "-"}
              </Typography>
            )}
          </Box>
        )}

        <Box sx={{ pt: 2, borderTop: "1px solid", borderColor: "divider" }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Not connected yet?
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.25, mb: 1 }}
          >
            {word
              ? `Connect it here. Only the ways that bring a ${agentWord} are shown.`
              : "Connect it here."}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={onConnectNew}
            startIcon={<Iconify icon="solar:link-circle-linear" width={16} />}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            {`Connect ${word ? `a ${agentWord}` : "an agent"}`}
          </Button>
        </Box>
      </Stack>

      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{
          px: 2.5,
          py: 1.75,
          borderTop: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={!picked}
          onClick={confirm}
        >
          {picked ? `Test ${picked.name} here` : "Pick an agent"}
        </Button>
      </Stack>
    </Stack>
  );
}

ChangeAgentBody.propTypes = {
  env: PropTypes.object,
  modality: PropTypes.string,
  onClose: PropTypes.func,
  onConnectNew: PropTypes.func.isRequired,
  onBuildNew: PropTypes.func.isRequired,
};
