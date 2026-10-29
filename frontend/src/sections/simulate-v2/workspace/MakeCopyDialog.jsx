import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import CopyCommandButton from "../components/CopyCommandButton";
import { useConnectedAgents } from "../store";
import { envAgentKey } from "../_mock/agentRegistry";
import { fitsEnvChannel } from "../_mock/agentSwitch";
import { mainEnvOf } from "../_mock/agentLifecycle";
import { pendingEnvChanges } from "../_mock/versions";
import { buildableAgentsOf } from "../environments/worldSource";
import { adoptBlockOf, catalogueById, catalogueOf } from "../_mock/catalogue";
import { copyPlanOf, copyScopesOf, envCopyCli } from "../_mock/envCopy";
import { forkBindingOf } from "../_mock/forkBinding";

const label = { typography: "s3", fontWeight: 600, mb: 0.5 };

/* One line of what the copy means: a label and what it says. */
function Line({ k, children }) {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      sx={{
        py: 0.75,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.tertiary",
          width: 110,
          flexShrink: 0,
        }}
      >
        {k}
      </Typography>
      <Typography
        sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}
      >
        {children}
      </Typography>
    </Stack>
  );
}
Line.propTypes = { k: PropTypes.string, children: PropTypes.node };

/**
 * Make a copy (roadmap catalogue.md): name, source version, agent and
 * channel, scenario scope — and before it's made, what carries over, what
 * stays with the source, whether the two compare, and which environment is
 * the agent's main. Same agent makes a duplicate; another agent a fork. A
 * withdrawn release can't be copied; a retired one can, with a notice.
 */
export default function MakeCopyDialog({
  open,
  env,
  envState,
  version = null,
  selectedIds = [],
  onClose,
  onCreate,
}) {
  const { agents, state } = useConnectedAgents();
  const versions = envState?.envVersions || [];
  const active =
    envState?.activeEnvVersion || versions[versions.length - 1]?.label || "v1";
  const [name, setName] = useState("");
  const [from, setFrom] = useState(active);
  const [agentKey, setAgentKey] = useState("");
  const [scopeId, setScopeId] = useState("all");
  const sourceKey = envState?.agent ? envAgentKey(envState.agent, env.id) : "";
  useEffect(() => {
    if (!open) return;
    setName(`${env.name} · copy`);
    setFrom(version || active);
    setAgentKey(sourceKey);
    setScopeId(selectedIds.length ? "selected" : "all");
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const choices = useMemo(
    () =>
      buildableAgentsOf(agents).filter(
        (a) => a.key === sourceKey || fitsEnvChannel(a, env, state),
      ),
    [agents, sourceKey, env, state],
  );
  const scopes = useMemo(
    () => copyScopesOf(env, envState, selectedIds),
    [env, envState, selectedIds],
  );
  const scope = scopes.find((s) => s.id === scopeId) ||
    scopes[0] || { id: "all", ids: [] };
  const sameAgent = !agentKey || agentKey === sourceKey;
  const target = agents.find((a) => a.key === agentKey) || null;
  const total = (scopes.find((s) => s.id === "all") || scope).ids.length;
  const plan = copyPlanOf({
    sameAgent,
    scope,
    total,
    runs: (envState?.runs || []).length,
  });
  const pending = pendingEnvChanges(envState).length;
  /* A fork: what the new agent changes, reviewed before the copy exists. */
  const binding = useMemo(
    () =>
      !sameAgent && target
        ? forkBindingOf({
            env,
            envState,
            target,
            state,
            scenarioIds: scope.ids,
          })
        : null,
    [sameAgent, target, env, envState, state, scope.ids],
  );
  /* The world's release, when it came from the catalogue. */
  const entry = env.catalogueId
    ? catalogueById(env.catalogueId)
    : catalogueOf(env.templateId);
  const blocked =
    entry?.release?.status === "withdrawn" ? adoptBlockOf(entry) : null;
  const replacement = entry?.release?.replacement
    ? catalogueById(entry.release.replacement)
    : null;
  const agentName = sameAgent ? envState?.agent?.name : target?.name;
  const main = (() => {
    const who = sameAgent ? agents.find((a) => a.key === sourceKey) : target;
    if (!who) return null;
    const m = mainEnvOf(who, state);
    return m
      ? `${who.name}'s main environment stays ${m.envName || m.env?.name}. Make the copy main from its Overview if it should be.`
      : `Becomes ${who.name}'s main environment — it has none yet.`;
  })();
  if (!open) return null;
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: 600 } }}
      title={`Make a copy of ${env.name}`}
      content={
        <Stack spacing={1.75}>
          {blocked && (
            <Typography sx={{ typography: "s2", color: "status.warning.fg" }}>
              {`${blocked}${replacement ? ` Use ${replacement.id} · ${replacement.task} instead.` : ""}`}
            </Typography>
          )}
          {entry?.release?.status === "retired" && (
            <Typography
              sx={{ typography: "s2", color: "text.secondary" }}
            >{`${entry.release.note} The copy keeps release ${entry.release.version}.`}</Typography>
          )}
          <Box>
            <Typography sx={label}>Name</Typography>
            <TextField
              size="small"
              fullWidth
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Box>
          <Stack direction="row" spacing={1.5}>
            <Box flex={1} minWidth={0}>
              <Typography sx={label}>Source version</Typography>
              <Select
                size="small"
                fullWidth
                value={from}
                onChange={(e) => setFrom(e.target.value)}
                sx={{ typography: "s2" }}
              >
                {versions.map((v) => (
                  <MenuItem
                    key={v.label}
                    value={v.label}
                    sx={{ typography: "s2" }}
                  >
                    {`Environment ${v.label}${v.label === active ? " · current" : ""}`}
                  </MenuItem>
                ))}
              </Select>
            </Box>
            <Box flex={1} minWidth={0}>
              <Typography sx={label}>Agent</Typography>
              <Select
                size="small"
                fullWidth
                value={agentKey}
                onChange={(e) => setAgentKey(e.target.value)}
                sx={{ typography: "s2" }}
                displayEmpty
              >
                {!sourceKey && (
                  <MenuItem value="" sx={{ typography: "s2" }}>
                    No agent yet
                  </MenuItem>
                )}
                {choices.map((a) => (
                  <MenuItem key={a.key} value={a.key} sx={{ typography: "s2" }}>
                    {`${a.name}${a.key === sourceKey ? " · same agent" : ""}`}
                  </MenuItem>
                ))}
              </Select>
            </Box>
          </Stack>
          {pending > 0 && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: -1 }}
            >
              {`${pending} unsaved edit${pending === 1 ? " isn't" : "s aren't"} copied — save first to include ${pending === 1 ? "it" : "them"}, or copy the saved version.`}
            </Typography>
          )}
          <Box>
            <Typography sx={label}>Scenarios</Typography>
            <Select
              size="small"
              fullWidth
              value={scope.id}
              onChange={(e) => setScopeId(e.target.value)}
              sx={{ typography: "s2" }}
            >
              {scopes.map((s) => (
                <MenuItem key={s.id} value={s.id} sx={{ typography: "s2" }}>
                  {`${s.label} · ${s.ids.length}`}
                </MenuItem>
              ))}
            </Select>
          </Box>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              px: 1.5,
              py: 0.5,
            }}
          >
            <Line k="Makes">
              {plan.kind === "duplicate"
                ? "A duplicate — same agent and channel."
                : `A fork — tested with ${agentName || "another agent"}; its tools are checked against the world before it runs.`}
            </Line>
            <Line k="Carries over">{`The world and data at environment ${from}, its rules, evaluations, actors and non-secret settings. Credential references carry only where you're allowed to use them.`}</Line>
            <Line k="History">{plan.history}</Line>
            <Line k="Compares">{plan.comparability}</Line>
            {main && <Line k="Main">{main}</Line>}
          </Box>
          {binding && (
            <Box>
              <Typography
                sx={label}
              >{`What changes with ${target.name}`}</Typography>
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  px: 1.5,
                  py: 0.5,
                }}
              >
                <Line k="Tools">
                  {!binding.known
                    ? `${target.name}'s tools aren't known yet. Sync it or run its connection check; cases are checked against the world before it runs.`
                    : `Calls ${binding.covers.length} of the world's ${binding.covers.length + binding.unused.length} tools${binding.unused.length ? `; ${binding.unused.length} it doesn't call stay in the world` : ""}.`}
                  {binding.unanswered.length > 0 && (
                    <Box component="span" sx={{ color: "status.warning.fg" }}>
                      {` Also calls ${binding.unanswered.join(", ")}, which the world can't answer yet. Add ${binding.unanswered.length === 1 ? "it" : "them"} on World & tools after copying.`}
                    </Box>
                  )}
                </Line>
                <Line k="Cases">
                  {binding.unmeasurable.length ? (
                    <Box component="span" sx={{ color: "status.warning.fg" }}>
                      {`${binding.unmeasurable.length} of the ${binding.cases} active scenarios can't be measured with ${target.name}: ${[
                        ...new Set(
                          binding.unmeasurable.map(
                            (c) => `“${c.title}” needs ${c.missing.join(", ")}`,
                          ),
                        ),
                      ]
                        .slice(0, 2)
                        .join(
                          "; ",
                        )}${new Set(binding.unmeasurable.map((c) => c.title)).size > 2 ? ", and others" : ""}. They stay in the copy; runs leave them out until the agent has those tools.`}
                    </Box>
                  ) : (
                    `All ${binding.cases} active scenarios can be measured with ${target.name}.`
                  )}
                </Line>
                <Line k="Stays">
                  {`The world, its ${binding.rules} rule${binding.rules === 1 ? "" : "s"}, evaluations and actors. They grade ${target.name} the same way; only the agent changes.`}
                </Line>
                <Line k="Compares">
                  {`With ${envState?.agent?.name || "the source agent"} on the ${binding.comparable} case${binding.comparable === 1 ? "" : "s"} both can run, on environment ${from}.`}
                </Line>
              </Box>
            </Box>
          )}
          <Box>
            <CopyCommandButton
              command={envCopyCli({
                env,
                name,
                version: from,
                agentName: sameAgent ? null : agentName,
                scopeId: scope.id,
              })}
            />
          </Box>
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!!blocked || !name.trim() || !scope.ids.length}
          onClick={() =>
            onCreate({
              name: name.trim(),
              version: from,
              kind: plan.kind,
              agent: sameAgent ? null : target,
              scope,
            })
          }
        >
          {plan.kind === "duplicate" ? "Make duplicate" : "Make fork"}
        </Button>
      }
    />
  );
}
MakeCopyDialog.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  /* A History entry preselects its version. */
  version: PropTypes.string,
  selectedIds: PropTypes.array,
  onClose: PropTypes.func.isRequired,
  onCreate: PropTypes.func.isRequired,
};
