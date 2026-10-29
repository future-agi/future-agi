import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Collapse,
  IconButton,
  Tooltip,
  Button,
} from "@mui/material";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import { schemaFor, toolImplFor } from "../_mock/envInternals";
import { PROOF_CHECKS } from "../_mock/proofs";
import {
  downgradeTierPatch,
  needsSafeTarget,
  safeTargetPatch,
  tierOf,
  toolEnvPatch,
  toolEnvSummaryWords,
  toolSettingOf,
  allowLivePatch,
  liveAdvisoryOf,
  liveFieldsOf,
} from "../_mock/toolEnv";
import {
  TierDial,
  FaultsControl,
  SafeTargetPrompt,
  LiveAdvisory,
} from "./ToolDial";
import { toolArgsOf } from "../_mock/rlContract";
import ToolArgsGrid from "./ToolArgsGrid";
import AddMcpToolsDrawer from "./AddMcpToolsDrawer";
import { listMcpTools, mcpServersOfAgent } from "../_mock/mcpServers";
import {
  agentCallsOf,
  agentServerIdsOf,
  envMcpServersOf,
  mcpPlacementOf,
  removeWorldToolsPlan,
  toolReachOf,
} from "../_mock/worldTools";
import { gatewayServerTools, gatewayToolStatusOf } from "../_mock/gatewayMcp";
import { worldToolsFor } from "../_mock/toolFit";
import { isSecretRef } from "../_mock/secretRefs";
import SecretRefField from "../agents/connect/SecretRefField";
import { SecretRefText } from "./SecretRefControls";
import { useSimStore } from "../store";
import { clickable, focusRing } from "../components/clickable";
import ToolTryPanel from "./ToolTryPanel";
import CopyCommandButton from "../components/CopyCommandButton";

/**
 * World internals on the World tab.
 *
 * Three sections: DB schema, the world's tools (each with its fidelity dial
 * and fault profile; open one for its arguments and code), and the checks that prove
 * a scenario can be staged. Rows are compact by default — click one open to
 * inspect columns or read the code. Graders aren't here: sub-goals decide
 * pass or fail, and the evaluations that score on top live on the
 * Evaluations tab.
 */
export default function WorldInternalsSection({
  env,
  envState,
  patch,
  onGo,
  locked = false,
  mcp = null,
  onMcp,
}) {
  const schema = schemaFor(env, envState);
  /* The world being edited: the pinned version, plus tools added or removed since. */
  const tools = worldToolsFor(env, envState);
  const { dispatch } = useSimStore();
  /* Adding tools from an MCP server — any server, or one the agent already has
     attached. The World panel holds the drawer's state so its summary line can open it too. */
  const openMcp = (prefill = null) => onMcp?.({ prefill });
  const editable = !locked && !!patch;

  const totalRows = schema.reduce((a, t) => a + (t.rows || 0), 0);
  /* The agent's own MCP servers: tools it gets there that the world doesn't
     answer yet — placed the way the drawer places them. */
  const agentServerIds = agentServerIdsOf(envState);
  const agentServers = mcpServersOfAgent(envState?.agent)
    .map(({ server, tools: listed }) => ({
      server,
      tools: listed.filter(
        (t) =>
          mcpPlacementOf(t, server, tools, { agentServerIds }).status !==
          "inWorld",
      ),
    }))
    .filter((s) => s.tools.length);
  const sources = envMcpServersOf(env, envState);
  /* Removing is a world edit like adding: in place before a run pins the version, else unsaved. */
  const removeTools = (names, label) => {
    const plan = removeWorldToolsPlan(env, envState, { names, label });
    if (plan.envPatch)
      dispatch({
        type: "patchEnvironment",
        envId: env.id,
        patch: plan.envPatch,
      });
    patch(plan.statePatch);
  };

  return (
    <Stack spacing={2} sx={{ mb: 2 }}>
      {schema.length > 0 && (
        <SchemaCard schema={schema} totalRows={totalRows} />
      )}
      {agentServers.map(({ server, tools: missing }) => (
        <AgentMcpNotice
          key={server.id}
          agent={envState.agent}
          server={server}
          missing={missing}
          onAdd={
            editable
              ? () => openMcp({ server, tools: missing, reason: "agent" })
              : null
          }
        />
      ))}
      {sources.length > 0 && (
        <ToolSourcesCard
          env={env}
          envState={envState}
          sources={sources}
          tools={tools}
          patch={editable ? patch : null}
          onRefresh={editable ? (prefill) => openMcp(prefill) : null}
          onRemove={
            editable ? (src) => removeTools(src.tools, src.label) : null
          }
        />
      )}
      {tools.length > 0 ? (
        <ToolImplsCard
          env={env}
          tools={tools}
          envState={envState}
          patch={locked ? null : patch}
          onAddMcp={editable ? () => openMcp() : null}
          onRemove={
            editable
              ? (tool) => removeTools([tool.name], tool.via?.label)
              : null
          }
        />
      ) : (
        <NoToolsCard
          agent={envState?.agent}
          onAddMcp={editable ? () => openMcp() : null}
        />
      )}
      {editable && (
        <AddMcpToolsDrawer
          open={!!mcp}
          onClose={() => onMcp?.(null)}
          env={env}
          envState={envState}
          patch={patch}
          onGo={onGo}
          prefill={mcp?.prefill || null}
        />
      )}
      <ProofChecksCard onGo={onGo} />
    </Stack>
  );
}
WorldInternalsSection.propTypes = {
  mcp: PropTypes.object,
  onMcp: PropTypes.func,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func,
  onGo: PropTypes.func,
  locked: PropTypes.bool,
};

/* ── DB schema ────────────────────────────────────────────────────────── */

function SchemaCard({ schema, totalRows }) {
  const [openTable, setOpenTable] = useState(null);
  return (
    <SectionCard
      title="World state · database schema"
      subtitle="The tables and columns that make up this environment's world"
      action={
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {schema.length} tables · {totalRows.toLocaleString()} rows
        </Typography>
      }
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {schema.map((t) => {
          const open = openTable === t.name;
          return (
            <Box key={t.name}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                {...clickable(() => setOpenTable(open ? null : t.name), {
                  expanded: open,
                })}
                sx={{
                  ...focusRing,
                  px: 2.5,
                  py: 1.375,
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify
                  icon={
                    open
                      ? "solar:alt-arrow-down-linear"
                      : "solar:alt-arrow-right-linear"
                  }
                  width={14}
                  sx={{ color: "text.tertiary", flexShrink: 0 }}
                />
                <Iconify
                  icon="solar:database-linear"
                  width={16}
                  sx={{ color: "status.info.fg", flexShrink: 0 }}
                />
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    fontFamily: "ui-monospace, Menlo, monospace",
                    minWidth: 140,
                  }}
                >
                  {t.name}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flex: 1,
                    minWidth: 0,
                  }}
                  noWrap
                >
                  {t.cols.length} columns · {t.note}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    fontVariantNumeric: "tabular-nums",
                    flexShrink: 0,
                  }}
                >
                  {(t.rows || 0).toLocaleString()} rows
                </Typography>
              </Stack>
              <Collapse in={open}>
                <Box
                  sx={{
                    px: 2.5,
                    py: 1.5,
                    bgcolor: "background.neutral",
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Box
                    sx={{
                      display: "grid",
                      gridTemplateColumns: "20px 1fr 1fr",
                      rowGap: 0.5,
                      columnGap: 1.5,
                    }}
                  >
                    {t.cols.map((c) => (
                      <SchemaColumnRow key={c.name} col={c} />
                    ))}
                  </Box>
                </Box>
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
SchemaCard.propTypes = { schema: PropTypes.array, totalRows: PropTypes.number };

function SchemaColumnRow({ col }) {
  return (
    <>
      <Box sx={{ display: "grid", placeItems: "center" }}>
        {col.pk && (
          <Tooltip arrow title="Primary key">
            <Iconify
              icon="solar:key-minimalistic-linear"
              width={12}
              sx={{ color: "status.warning.fg" }}
            />
          </Tooltip>
        )}
      </Box>
      <Typography
        sx={{
          typography: "s2",
          fontFamily: "ui-monospace, Menlo, monospace",
          fontWeight: col.pk ? 600 : 500,
        }}
      >
        {col.name}
      </Typography>
      <Typography
        sx={{
          typography: "s3",
          fontFamily: "ui-monospace, Menlo, monospace",
          color: "text.tertiary",
        }}
      >
        {col.type}
      </Typography>
    </>
  );
}
SchemaColumnRow.propTypes = { col: PropTypes.object };

/* ── tool implementations ─────────────────────────────────────────────── */

/*
  The world's tool handlers, each with its fidelity dial (PRD v6 Story 5):
  how the tool answers in a run, and what goes wrong with it on purpose. A
  change to either is a world edit — the next run is on a new minor version.
*/
function ToolImplsCard({ env, tools, envState, patch, onAddMcp, onRemove }) {
  const [openTool, setOpenTool] = useState(null);
  /* Tools the agent calls — an added tool it doesn't call is offered to it, or it can't see it. */
  const agent = envState?.agent || null;
  const calls = agentCallsOf(env, envState);
  const summary = toolEnvSummaryWords({ tools }, envState);

  /* An MCP tool on Live goes to its server (or the Gateway) — saved with the tier, so it never asks for a target. */
  const setTier = (setting, tier, tool) => {
    if (!patch || setting.tier === tier) return;
    const fields =
      tier === "live" ? liveFieldsOf(tool) : { tier, safeTarget: null };
    patch(
      toolEnvPatch(
        envState,
        setting.name,
        fields,
        `${setting.name} set to ${tierOf(tier).word}`,
      ),
    );
  };
  /* Allow Live for one read-only tool and switch it there — one patch, so
     the permission and the tier land together. A world edit, like any tier. */
  const allowLive = (setting, tool) => {
    if (!patch) return;
    const allowed = allowLivePatch(envState, setting.name, true, tool).toolEnv;
    patch(
      toolEnvPatch(
        { ...envState, toolEnv: allowed },
        setting.name,
        liveFieldsOf(tool),
        `${setting.name} set to ${tierOf("live").word}`,
      ),
    );
  };
  const setFaults = (setting, faults, word) => {
    if (!patch) return;
    patch(
      toolEnvPatch(
        envState,
        setting.name,
        { faults },
        `${setting.name} faults set to ${word}`,
      ),
    );
  };

  return (
    <SectionCard
      title={`Tools · ${tools.length}`}
      subtitle={
        <>
          <Box
            component="span"
            sx={{
              color: "text.primary",
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {summary}
          </Box>
          {
            " — how each tool answers during a run. A change is a world edit, saved as the next environment version."
          }
        </>
      }
      action={
        <Stack direction="row" spacing={1} alignItems="center">
          <CopyCommandButton
            command={`agent-learn tool list --environment ${env?.id || "<env>"}`}
          />
          {patch && onAddMcp && (
            <Button
              size="small"
              variant="outlined"
              onClick={onAddMcp}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
                whiteSpace: "nowrap",
              }}
            >
              Add tools
            </Button>
          )}
        </Stack>
      }
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {tools.map((tool) => {
          const open = openTool === tool.name;
          const impl = toolImplFor(tool);
          const setting = toolSettingOf(tool, envState);
          const unsafe = needsSafeTarget(setting);
          return (
            <Box key={tool.name}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                {...clickable(() => setOpenTool(open ? null : tool.name), {
                  expanded: open,
                })}
                sx={{
                  ...focusRing,
                  px: 2.5,
                  py: 1.125,
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify
                  icon={
                    open
                      ? "solar:alt-arrow-down-linear"
                      : "solar:alt-arrow-right-linear"
                  }
                  width={14}
                  sx={{ color: "text.tertiary", flexShrink: 0 }}
                />
                <Iconify
                  icon="solar:code-linear"
                  width={16}
                  sx={{ color: "primary.main", flexShrink: 0 }}
                />
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    fontFamily: "ui-monospace, Menlo, monospace",
                    width: 220,
                    flexShrink: 0,
                  }}
                >
                  {tool.name}
                </Typography>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                  sx={{ flex: 1, minWidth: 0, overflow: "hidden" }}
                >
                  {tool.via?.kind === "mcp" && (
                    <Tooltip
                      arrow
                      title={
                        tool.via.gateway
                          ? `From ${tool.via.label} in your Gateway (${tool.via.host}). The world answers it during runs; on Live it's called through the Gateway, with its rate limits and checks.`
                          : `From the MCP server ${tool.via.host}. The world answers it during runs; the server is only called if the tool is Live.`
                      }
                    >
                      <Box component="span" sx={{ display: "inline-flex" }}>
                        <QuietChip>{`${tool.via.gateway ? "Gateway" : "MCP"} · ${tool.via.label}`}</QuietChip>
                      </Box>
                    </Tooltip>
                  )}
                  {(() => {
                    /* What the Gateway says now — it can change after the tool was added. */
                    const gw = gatewayToolStatusOf(tool);
                    return gw ? (
                      <Tooltip arrow title={gw.tip}>
                        <Box component="span" sx={{ display: "inline-flex" }}>
                          <QuietChip warn>{gw.label}</QuietChip>
                        </Box>
                      </Tooltip>
                    ) : null;
                  })()}
                  {/* Two labels at most: a Gateway warning takes the place of reach. */}
                  {tool.via?.kind === "mcp" &&
                    !gatewayToolStatusOf(tool) &&
                    (() => {
                      const reach = toolReachOf(tool, { agent, calls });
                      return reach ? (
                        <Tooltip arrow title={reach.tip}>
                          <Box component="span" sx={{ display: "inline-flex" }}>
                            <QuietChip warn={reach.kind === "blocked"}>
                              {reach.label}
                            </QuietChip>
                          </Box>
                        </Tooltip>
                      ) : null;
                    })()}
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      minWidth: 0,
                    }}
                    noWrap
                  >
                    {setting.safeTarget && setting.tier === "live"
                      ? `Live at ${setting.safeTarget}`
                      : setting.approval
                        ? `Waits for ${setting.approval.by} above $${setting.approval.above} · ${tool.desc || ""}`
                        : tool.desc}
                  </Typography>
                </Stack>
                <TierDial
                  setting={setting}
                  onChange={
                    patch ? (tier) => setTier(setting, tier, tool) : null
                  }
                  onAllowLive={patch ? () => allowLive(setting, tool) : null}
                />
                <FaultsControl
                  setting={setting}
                  onApply={
                    patch
                      ? (faults, word) => setFaults(setting, faults, word)
                      : null
                  }
                />
              </Stack>
              {liveAdvisoryOf(setting) && (
                <Box sx={{ px: 2.5, pb: 1.25, pl: 7.25 }}>
                  <LiveAdvisory
                    setting={setting}
                    onSwitch={
                      patch
                        ? (tier) =>
                            patch(
                              downgradeTierPatch(envState, setting.name, tier),
                            )
                        : null
                    }
                  />
                </Box>
              )}
              {unsafe && (
                <Box sx={{ px: 2.5, pb: 1.25, pl: 7.25 }}>
                  <SafeTargetPrompt
                    setting={setting}
                    onConfirm={
                      patch
                        ? (url) =>
                            patch(safeTargetPatch(envState, setting.name, url))
                        : null
                    }
                    onDowngrade={
                      patch
                        ? (tier) =>
                            patch(
                              downgradeTierPatch(envState, setting.name, tier),
                            )
                        : null
                    }
                  />
                </Box>
              )}
              <Collapse in={open} unmountOnExit>
                <ToolArgs tool={tool} envState={envState} />
                <ToolTryPanel tool={tool} env={env} envState={envState} />
                <CodeBlock file={impl.file} code={impl.code} />
                {tool.via?.kind === "mcp" && onRemove && (
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={1}
                    sx={{
                      px: 2.5,
                      pl: 7.25,
                      py: 1,
                      borderTop: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
                    >
                      {`From ${tool.via.label}${tool.mcpName ? `, where it's ${tool.mcpName}` : ""}. Scenarios that call it can't run as proved once it's gone.`}
                    </Typography>
                    <Button
                      size="small"
                      onClick={() => onRemove(tool)}
                      sx={{ color: "text.secondary" }}
                    >
                      Remove from the world
                    </Button>
                  </Stack>
                )}
              </Collapse>
            </Box>
          );
        })}
        {/* Where the eye lands when the list ends: the next tool comes from here. */}
        {patch && onAddMcp && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            {...clickable(onAddMcp)}
            sx={{
              ...focusRing,
              px: 2.5,
              py: 1.25,
              cursor: "pointer",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Box sx={{ width: 13, flexShrink: 0 }} />
            <Iconify
              icon="solar:add-circle-linear"
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                width: 220,
                flexShrink: 0,
              }}
            >
              Add tools from an MCP server
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
            >
              Shopify, Stripe, Zendesk or any server URL. Each tool starts
              Simulated.
            </Typography>
          </Stack>
        )}
      </Stack>
    </SectionCard>
  );
}
ToolImplsCard.propTypes = {
  env: PropTypes.object,
  tools: PropTypes.array,
  envState: PropTypes.object,
  patch: PropTypes.func,
  onAddMcp: PropTypes.func,
  onRemove: PropTypes.func,
};

/*
  Where the world's tools came from: each MCP server, read off the tools
  themselves — its address, how it's reached, its token by reference. Refresh
  asks the server (or the Gateway) what it lists now; Remove takes its tools
  out of the world. A token can be pointed elsewhere without touching the
  world: it's a credential, kept beside the agent's.
*/
const sourceBtn = {
  color: "text.primary",
  borderColor: "divider",
  minWidth: 0,
  whiteSpace: "nowrap",
};

function ToolSourcesCard({
  envState,
  sources,
  tools,
  patch,
  onRefresh,
  onRemove,
}) {
  const [note, setNote] = useState(null);
  const [confirming, setConfirming] = useState(null);
  const [retoken, setRetoken] = useState(null);
  const [tokenDraft, setTokenDraft] = useState("");
  const tokenOf = (src) =>
    envState?.mcpTokens?.[src.id] || src.tokenRef || null;
  const refresh = (src) => {
    setNote(null);
    const r = src.gateway
      ? gatewayServerTools(src.gateway.serverId)
      : listMcpTools({
          transport: src.transport,
          value: src.value,
          tokenRef: tokenOf(src),
        });
    if (!r.ok) {
      setNote({ id: src.id, text: r.error });
      return;
    }
    const fresh = r.tools.filter(
      (t) =>
        !t.deprecated &&
        mcpPlacementOf(t, r.server, tools).status !== "inWorld",
    );
    if (!fresh.length) {
      setNote({
        id: src.id,
        text: `Up to date — ${src.label} lists nothing this world doesn't have.`,
      });
      return;
    }
    onRefresh({ server: r.server, tools: fresh, reason: "refresh" });
  };
  const saveToken = (src) => {
    patch({
      mcpTokens: {
        ...(envState?.mcpTokens || {}),
        [src.id]: tokenDraft.trim(),
      },
    });
    setRetoken(null);
    setTokenDraft("");
  };
  return (
    <SectionCard
      title={`Tool sources · ${sources.length}`}
      subtitle="The MCP servers this world's tools came from. Refresh to see what a server lists now."
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {sources.map((src) => (
          <Box key={src.id}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ px: 2.5, py: 1 }}
            >
              <Iconify
                icon="solar:plug-circle-linear"
                width={16}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Box sx={{ width: 200, flexShrink: 0, minWidth: 0 }}>
                <Typography noWrap sx={{ typography: "s2_1", fontWeight: 500 }}>
                  {src.label}
                </Typography>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    fontFamily: "ui-monospace, Menlo, monospace",
                  }}
                >
                  {src.value || src.host}
                </Typography>
              </Box>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {`${src.tools.length} tool${src.tools.length === 1 ? "" : "s"} · ${src.tools.join(", ")}`}
              </Typography>
              <Box sx={{ width: 220, flexShrink: 0, minWidth: 0 }}>
                {src.gateway ? (
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    Credentials in your Gateway
                  </Typography>
                ) : src.transport === "stdio" ? (
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    Runs on your machine
                  </Typography>
                ) : tokenOf(src) ? (
                  <SecretRefText value={tokenOf(src)} />
                ) : (
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    No token
                  </Typography>
                )}
              </Box>
              {patch && (
                /* A fixed slot, right-aligned, so the columns line up whichever actions a source has. */
                <Stack
                  direction="row"
                  spacing={0.75}
                  justifyContent="flex-end"
                  sx={{ flexShrink: 0, width: 340 }}
                >
                  {!src.gateway && src.transport !== "stdio" && (
                    <Button
                      size="small"
                      variant="outlined"
                      sx={sourceBtn}
                      startIcon={
                        <Iconify
                          icon="solar:key-minimalistic-linear"
                          width={14}
                        />
                      }
                      onClick={() => {
                        setRetoken(retoken === src.id ? null : src.id);
                        setTokenDraft("");
                      }}
                    >
                      Change token
                    </Button>
                  )}
                  {onRefresh && (
                    <Button
                      size="small"
                      variant="outlined"
                      sx={sourceBtn}
                      startIcon={
                        <Iconify icon="solar:refresh-linear" width={14} />
                      }
                      onClick={() => refresh(src)}
                    >
                      Refresh
                    </Button>
                  )}
                  {onRemove && (
                    <Button
                      size="small"
                      variant="outlined"
                      sx={sourceBtn}
                      startIcon={
                        <Iconify
                          icon="solar:trash-bin-minimalistic-linear"
                          width={14}
                        />
                      }
                      onClick={() =>
                        setConfirming(confirming === src.id ? null : src.id)
                      }
                    >
                      Remove
                    </Button>
                  )}
                </Stack>
              )}
            </Stack>
            {note?.id === src.id && (
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  px: 2.5,
                  pl: 6.5,
                  pb: 1.25,
                }}
              >
                {note.text}
              </Typography>
            )}
            {retoken === src.id && (
              <Stack
                direction="row"
                alignItems="flex-start"
                spacing={1}
                sx={{ px: 2.5, pl: 6.5, pb: 1.5 }}
              >
                <Box flex={1} minWidth={0}>
                  <SecretRefField
                    label="New token reference"
                    placeholder="vault://acme/sim/mcp_token"
                    value={tokenDraft}
                    onChange={setTokenDraft}
                    fullWidth
                  />
                </Box>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!isSecretRef(tokenDraft)}
                  onClick={() => saveToken(src)}
                  sx={{ color: "text.primary", borderColor: "divider", mt: 3 }}
                >
                  Save
                </Button>
              </Stack>
            )}
            {confirming === src.id && (
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ px: 2.5, pl: 6.5, pb: 1.25 }}
              >
                <Typography
                  sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
                >
                  {`Take its ${src.tools.length} tool${src.tools.length === 1 ? "" : "s"} out of the world? Scenarios that call them can't run as proved.`}
                </Typography>
                <Button
                  size="small"
                  onClick={() => setConfirming(null)}
                  sx={{ color: "text.secondary" }}
                >
                  Keep
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => {
                    setConfirming(null);
                    onRemove(src);
                  }}
                  sx={{ color: "text.primary", borderColor: "divider" }}
                >
                  Remove {src.tools.length} tool
                  {src.tools.length === 1 ? "" : "s"}
                </Button>
              </Stack>
            )}
          </Box>
        ))}
      </Stack>
    </SectionCard>
  );
}
ToolSourcesCard.propTypes = {
  envState: PropTypes.object,
  sources: PropTypes.array.isRequired,
  tools: PropTypes.array.isRequired,
  patch: PropTypes.func,
  onRefresh: PropTypes.func,
  onRemove: PropTypes.func,
};

/* A quiet label on a tool row — where it came from, whether the agent has it. */
function QuietChip({ children, warn = false }) {
  return (
    <Typography
      component="span"
      noWrap
      sx={{
        typography: "s3",
        fontSize: 12,
        fontWeight: 600,
        px: 0.625,
        lineHeight: "18px",
        borderRadius: "4px",
        flexShrink: 0,
        border: "1px solid",
        borderColor: warn
          ? (t) => alpha(t.palette.warning.main, 0.5)
          : "divider",
        color: warn ? "warning.main" : "text.secondary",
      }}
    >
      {children}
    </Typography>
  );
}
QuietChip.propTypes = { children: PropTypes.node, warn: PropTypes.bool };

/* The world has no tools yet: they come from the agent, or from an MCP server. */
function NoToolsCard({ agent, onAddMcp }) {
  return (
    <SectionCard
      title="Tools · 0"
      subtitle={
        agent
          ? `${agent.name || "The agent"} declares no tools, so the world has none to answer. Add tools from an MCP server to give it some to act on.`
          : "None yet — the agent's tools are read when it's connected. Or add tools from an MCP server now."
      }
      action={
        onAddMcp && (
          <Button
            size="small"
            variant="outlined"
            onClick={onAddMcp}
            startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
              whiteSpace: "nowrap",
            }}
          >
            Add tools from an MCP server
          </Button>
        )
      }
    />
  );
}
NoToolsCard.propTypes = { agent: PropTypes.object, onAddMcp: PropTypes.func };

/* The agent has an MCP server attached whose tools the world doesn't answer — runs would get no answer for them. */
function AgentMcpNotice({ agent, server, missing, onAdd }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        px: 2,
        py: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
      }}
    >
      <Iconify
        icon="solar:plug-circle-linear"
        width={20}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {`${agent?.name || "The agent"} gets ${missing.length} tool${missing.length === 1 ? "" : "s"} from ${server.label} the world doesn't answer yet`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {`Its MCP server ${server.host} lists ${missing.map((t) => t.name).join(", ")}. Add them so runs answer them instead of failing.`}
        </Typography>
      </Box>
      {onAdd && (
        <Button
          size="small"
          variant="outlined"
          onClick={onAdd}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Add them to the world
        </Button>
      )}
    </Stack>
  );
}
AgentMcpNotice.propTypes = {
  agent: PropTypes.object,
  server: PropTypes.object.isRequired,
  missing: PropTypes.array.isRequired,
  onAdd: PropTypes.func,
};

/* A tool's arguments, read-only — the limits among them are edited on the Contract tab. */
function ToolArgs({ tool, envState }) {
  const args = toolArgsOf(tool, envState);
  return (
    <Box
      sx={{
        borderTop: "1px solid",
        borderColor: "divider",
        px: 2.5,
        pl: 7.25,
        py: 1.25,
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          mb: 0.5,
        }}
      >
        Arguments
      </Typography>
      {args.length === 0 ? (
        <Typography sx={{ typography: "s3", color: "text.disabled" }}>
          None
        </Typography>
      ) : (
        <ToolArgsGrid args={args} />
      )}
    </Box>
  );
}
ToolArgs.propTypes = {
  tool: PropTypes.object.isRequired,
  envState: PropTypes.object,
};

/* ── scenario checks ──────────────────────────────────────────────────── */

/* What proves a scenario before it runs. Graders are a link, not a copy —
   one home for them, one versioning rule. */
function ProofChecksCard({ onGo }) {
  const [openCheck, setOpenCheck] = useState(null);
  /* The same eight checks in every environment — folded to one line until asked for. */
  const [shown, setShown] = useState(false);
  if (!shown) {
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        {...clickable(() => setShown(true), { expanded: false })}
        sx={{
          ...focusRing,
          px: 2,
          py: 1.25,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon="solar:shield-check-linear"
          width={16}
          sx={{ color: "text.tertiary" }}
        />
        <Typography
          sx={{ typography: "s2", fontWeight: 600 }}
        >{`Scenario checks · ${PROOF_CHECKS.length}`}</Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          What proves each scenario before it runs
        </Typography>
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={14}
          sx={{ color: "text.tertiary" }}
        />
      </Stack>
    );
  }
  return (
    <SectionCard
      title="Scenario checks"
      subtitle="What proves each scenario before it runs. On a run, sub-goals decide pass or fail; evaluations score each conversation"
      action={
        onGo && (
          <Typography
            {...clickable(() => onGo("evals"), { role: "link" })}
            sx={{
              ...focusRing,
              typography: "s3",
              color: "primary.main",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Evaluations
          </Typography>
        )
      }
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {PROOF_CHECKS.map((check) => {
          const open = openCheck === check.id;
          return (
            <Box key={check.id}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                {...clickable(() => setOpenCheck(open ? null : check.id), {
                  expanded: open,
                })}
                sx={{
                  ...focusRing,
                  px: 2.5,
                  py: 1.375,
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify
                  icon={
                    open
                      ? "solar:alt-arrow-down-linear"
                      : "solar:alt-arrow-right-linear"
                  }
                  width={14}
                  sx={{ color: "text.tertiary", flexShrink: 0 }}
                />
                <Iconify
                  icon="solar:shield-check-linear"
                  width={16}
                  sx={{ color: "text.tertiary", flexShrink: 0 }}
                />
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    fontFamily: "ui-monospace, Menlo, monospace",
                    minWidth: 220,
                  }}
                >
                  {check.name}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flex: 1,
                    minWidth: 0,
                  }}
                  noWrap
                >
                  {check.desc}
                </Typography>
              </Stack>
              <Collapse in={open}>
                <CodeBlock file={check.file} code={check.code} />
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
ProofChecksCard.propTypes = { onGo: PropTypes.func };

/* ── shared code viewer ───────────────────────────────────────────────── */

function CodeBlock({ file, code }) {
  const { enqueueSnackbar } = useSnackbar();
  const copy = () => {
    navigator.clipboard?.writeText(code);
    enqueueSnackbar("Copied to clipboard", { variant: "info" });
  };
  return (
    <Box
      sx={{
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "action.hover",
        }}
      >
        <Iconify
          icon="solar:file-text-linear"
          width={12}
          sx={{ color: "text.tertiary" }}
        />
        <Typography
          sx={{
            typography: "s3",
            fontFamily: "ui-monospace, Menlo, monospace",
            color: "text.secondary",
            flex: 1,
          }}
        >
          {file}
        </Typography>
        <Tooltip arrow title="Copy code">
          <IconButton size="small" onClick={copy}>
            <Iconify
              icon="solar:copy-linear"
              width={12}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Tooltip>
      </Stack>
      <Box sx={{ px: 2.5, py: 1.5, maxHeight: 360, overflow: "auto" }}>
        <Typography
          component="pre"
          sx={{
            typography: "s3",
            fontFamily: "ui-monospace, Menlo, monospace",
            color: "text.primary",
            whiteSpace: "pre",
            m: 0,
            lineHeight: 1.6,
          }}
        >
          {code}
        </Typography>
      </Box>
    </Box>
  );
}
CodeBlock.propTypes = { file: PropTypes.string, code: PropTypes.string };
