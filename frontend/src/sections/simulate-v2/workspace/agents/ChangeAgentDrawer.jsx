import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import SideDrawer from "../../components/SideDrawer";
import { useConnectedAgents } from "../../store";
import { agentSourceOf } from "../../_mock/agentRegistry";
import AgentStateTags from "../../agents/AgentStateTags";
import { channelLabel } from "../../_mock/nudgeEffects";
import { fitsEnvChannel } from "../../_mock/agentSwitch";
import { buildableAgentsOf } from "../../environments/worldSource";
import { getSurface } from "../../_mock/surfaces";
import { clickable, focusRing } from "../../components/clickable";

/* The agent record an environment stores — the list row minus what the
   list derives (usage, latest, page id). */
const recordOf = (a) => {
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
    ...record
  } = a;
  return record;
};

/**
 * Connect the agent an environment tests, when it has none of its own yet —
 * nothing connected, or a template's placeholder. Once an environment has a
 * real agent it keeps it: new versions come by Sync, and a move to another
 * platform or ID happens on the agent's page, as the same agent.
 *
 * Only agents on this environment's channel are offered: a voice environment
 * never tests a chat agent — another type builds its own environment.
 * `preview(picked, label)` lists the tools that don't fit the world (in
 * place of a placeholder), raised as nudges once it's connected.
 */
export default function ChangeAgentDrawer({
  open,
  onClose,
  env,
  currentKey,
  preview,
  onChoose,
  onConnectNew,
}) {
  const navigate = useNavigate();
  const { agents, state } = useConnectedAgents();
  /* Agents it can be built for: not a template's stand-in or another world's
     Sample agent, not archived or cut off, not Coming soon. */
  const candidates = useMemo(
    () => buildableAgentsOf(agents).filter((a) => a.key !== currentKey),
    [agents, currentKey],
  );
  const others = useMemo(
    () => candidates.filter((a) => fitsEnvChannel(a, env, state)),
    [candidates, env, state],
  );
  const otherType = candidates.length - others.length;
  const channel = channelLabel(env?.surface).toLowerCase();
  /* Every row is the same type, so every row wears the channel's icon — the
     platform is in the line under the name. */
  const channelIcon = getSurface(env?.surface)?.icon || "solar:cpu-bolt-linear";
  const [pickedId, setPickedId] = useState(null);
  const [version, setVersion] = useState(null);
  /* A deliberate pick — nothing is chosen for you. */
  useEffect(() => {
    if (open) {
      setPickedId(null);
      setVersion(null);
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const picked = others.find((a) => a.id === pickedId);
  /* The version in production by default — not a draft nobody released. */
  const chosenVersion =
    version && picked?.versions.some((v) => v.label === version)
      ? version
      : picked?.production || picked?.latest;
  const plan = useMemo(
    () => (preview && picked ? preview(picked, chosenVersion) : null),
    [preview, picked, chosenVersion],
  );

  return (
    <SideDrawer open={open} onClose={onClose} width={480}>
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
            <Typography
              sx={{ typography: "m2", fontWeight: 600 }}
            >{`Connect a ${channel} agent`}</Typography>
            <Typography
              sx={{ typography: "s2", color: "text.tertiary", mt: 0.25 }}
            >
              {`Pick the ${channel} agent this environment tests, and the version — the world, scenarios and evaluations apply as they are.`}
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

        <Stack spacing={2.5} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
          <Box>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                textTransform: "none",
                letterSpacing: 0.6,
                mb: 0.75,
              }}
            >
              {`Connected ${channel} agents`}
            </Typography>
            {others.length ? (
              <Stack spacing={0.75}>
                {others.map((a) => {
                  const src = agentSourceOf(a);
                  const on = a.id === pickedId;
                  return (
                    <Stack
                      key={a.id}
                      direction="row"
                      alignItems="center"
                      spacing={1.25}
                      {...clickable(
                        () => {
                          setPickedId(a.id);
                          setVersion(null);
                        },
                        { role: "radio", checked: on },
                      )}
                      sx={{
                        ...focusRing,
                        px: 1.5,
                        py: 1,
                        borderRadius: 1,
                        cursor: "pointer",
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
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={0.75}
                          sx={{ minWidth: 0 }}
                        >
                          <Typography
                            noWrap
                            sx={{
                              typography: "s2_1",
                              fontWeight: 500,
                              minWidth: 0,
                            }}
                          >
                            {a.name}
                          </Typography>
                          <AgentStateTags agent={a} />
                        </Stack>
                        <Typography
                          noWrap
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          {`${src.label} · ${a.versions.length} version${a.versions.length === 1 ? "" : "s"}${a.usedIn.length ? ` · used in ${a.usedIn.map((u) => u.envName).join(", ")}` : ""}`}
                        </Typography>
                      </Box>
                      {on && (
                        <Iconify
                          icon="solar:check-circle-bold"
                          width={16}
                          sx={{ color: "text.primary" }}
                        />
                      )}
                    </Stack>
                  );
                })}
              </Stack>
            ) : (
              <Typography
                sx={{ typography: "s2", color: "text.tertiary" }}
              >{`No ${channel} agents are connected yet.`}</Typography>
            )}
            {otherType > 0 && (
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mt: 1 }}
              >
                {`${otherType} agent${otherType === 1 ? "" : "s"} on other channels ${otherType === 1 ? "isn't" : "aren't"} listed — this is a ${channel} environment. `}
                <Box
                  component="span"
                  {...clickable(
                    () => {
                      onClose?.();
                      navigate(paths.dashboard.simulate.environmentBuild);
                    },
                    { role: "link" },
                  )}
                  sx={{
                    ...focusRing,
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
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  textTransform: "none",
                  letterSpacing: 0.6,
                  mb: 0.75,
                }}
              >
                Version to test
              </Typography>
              <Select
                size="small"
                value={chosenVersion}
                onChange={(e) => setVersion(e.target.value)}
                sx={{ minWidth: 180, typography: "s2" }}
              >
                {[...picked.versions].reverse().map((v) => (
                  <MenuItem
                    key={v.label}
                    value={v.label}
                    sx={{ typography: "s2" }}
                  >
                    {`${v.label}${v.pendingDraft ? " · draft" : v.label === picked.production ? " · in production" : ""}`}
                  </MenuItem>
                ))}
              </Select>
            </Box>
          )}

          {plan && <SwitchPreview plan={plan} name={picked.name} />}

          <Box sx={{ pt: 2, borderTop: "1px solid", borderColor: "divider" }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Not connected yet?
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25, mb: 1 }}
            >
              {`Connect it here — only the ways that bring a ${channel} agent are shown.`}
            </Typography>
            <Button
              size="small"
              variant="outlined"
              onClick={onConnectNew}
              startIcon={<Iconify icon="solar:link-circle-linear" width={16} />}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              {`Connect a ${channel} agent`}
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
          <Button
            size="small"
            onClick={onClose}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="primary"
            size="small"
            disabled={!picked}
            onClick={() =>
              onChoose({
                agent: recordOf(picked),
                versions: picked.versions,
                activeLabel: chosenVersion,
                name: picked.name,
                picked,
              })
            }
          >
            {!picked ? "Pick an agent" : `Test ${picked.name} here`}
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

ChangeAgentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  /* Its channel decides which agents are offered. */
  env: PropTypes.object,
  currentKey: PropTypes.string,
  /* (picked, label) => { version, nudges, core } — what a switch would list. */
  preview: PropTypes.func,
  onChoose: PropTypes.func.isRequired,
  onConnectNew: PropTypes.func.isRequired,
};

/* What the switch would list as nudges — said before it happens. */
function SwitchPreview({ plan, name }) {
  const rows = plan.nudges.map((n) => {
    if (n.kind === "add")
      return {
        id: n.id,
        sign: "+",
        text: `${n.capability}`,
        note: `${n.scenarioIds.length} scenario${n.scenarioIds.length === 1 ? "" : "s"} exercise it — propose a handler and 3 scenarios`,
      };
    if (n.kind === "remove")
      return {
        id: n.id,
        sign: "−",
        text: `${n.capability}`,
        note: `${n.scenarioIds.length} scenario${n.scenarioIds.length === 1 ? " needs" : "s need"} it — they go stale`,
      };
    return { id: n.id, sign: "?", text: n.title, note: "" };
  });
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          textTransform: "none",
          letterSpacing: 0.6,
          mb: 0.75,
        }}
      >
        {`What we'll list after connecting`}
      </Typography>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        {rows.length ? (
          rows.map((r, i) => (
            <Stack
              key={r.id}
              direction="row"
              spacing={1}
              alignItems="baseline"
              sx={{
                px: 1.5,
                py: 0.75,
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{
                  width: 14,
                  flexShrink: 0,
                  fontFamily: "ui-monospace, Menlo, monospace",
                  fontSize: 12,
                  color: "text.tertiary",
                }}
              >
                {r.sign}
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  fontSize: 13,
                  flexShrink: 0,
                }}
              >
                {r.text}
              </Typography>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
              >
                {r.note}
              </Typography>
            </Stack>
          ))
        ) : (
          <Typography
            sx={{ px: 1.5, py: 1, typography: "s3", color: "text.tertiary" }}
          >
            {`Nothing we can see that ${name} wouldn't fit — same tools and channel.`}
          </Typography>
        )}
      </Box>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
        {`Agent ${plan.version.label} · these wait in Needs input as one agent-change card — nothing moves until you rebuild or answer them.`}
      </Typography>
    </Box>
  );
}
SwitchPreview.propTypes = { plan: PropTypes.object, name: PropTypes.string };
