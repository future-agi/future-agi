import PropTypes from "prop-types";
import React, { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Collapse,
  IconButton,
  Tooltip,
  Button,
  Menu,
  MenuItem,
} from "@mui/material";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import { SectionCard } from "../components/primitives";
import { schemaFor, toolImplFor } from "../_mock/envInternals";
import { PROOF_CHECKS } from "../_mock/proofs";
import {
  downgradeTierPatch,
  needsSafeTarget,
  safeTargetPatch,
  tierOf,
  TIERS,
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
import { toolOutputOf, toolTablesOf } from "../_mock/toolArgSpec";
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
import useVersionChange from "./useVersionChange";

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
  /* A new version seeding tables from a file: those rows count up. */
  const tableChange = useVersionChange(envState, "tables");
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
      {!(WORLD_MAP && tools.length > 0 && schema.length > 0) &&
        schema.length > 0 && (
          <SchemaCard
            schema={schema}
            totalRows={totalRows}
            change={tableChange}
          />
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
          schema={WORLD_MAP && schema.length > 0 ? schema : null}
          totalRows={totalRows}
          tableChange={tableChange}
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

function SchemaCard({ schema, totalRows, change = null }) {
  const [openTable, setOpenTable] = useState(null);
  const live = change?.live;
  const fresh = change?.fresh;
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
      {/* One compact row per table: name, rows, columns, then the note —
          the numbers sit beside the name, the free text runs last. */}
      {schema.map((t, i) => {
        const open = openTable === t.name;
        return (
          <Box
            key={t.name}
            sx={{ borderTop: i ? "1px solid" : "none", borderColor: "divider" }}
          >
            <Box
              {...clickable(() => setOpenTable(open ? null : t.name), {
                expanded: open,
              })}
              sx={{
                ...focusRing,
                display: "grid",
                gridTemplateColumns: "12px 220px 120px 90px minmax(0, 1fr)",
                columnGap: 1.5,
                alignItems: "center",
                px: 2,
                minHeight: 36,
                cursor: "pointer",
                bgcolor: open ? "action.selected" : "transparent",
                "&:hover": {
                  bgcolor: open ? "action.selected" : "action.hover",
                },
              }}
            >
              <Iconify
                icon={
                  open
                    ? "solar:alt-arrow-down-linear"
                    : "solar:alt-arrow-right-linear"
                }
                width={12}
                sx={{ color: "text.tertiary" }}
              />
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.75}
                sx={{ minWidth: 0 }}
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    fontWeight: 500,
                    fontFamily: "ui-monospace, Menlo, monospace",
                    minWidth: 0,
                  }}
                >
                  {t.name}
                </Typography>
                {fresh?.ids.includes(t.name) && (
                  <QuietChip>{`New in ${fresh.label}`}</QuietChip>
                )}
              </Stack>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: live?.ids.includes(t.name)
                    ? "text.primary"
                    : "text.secondary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {live?.ids.includes(t.name)
                  ? live.state === "next"
                    ? `Seeds in ${live.label}`
                    : `Seeding · ${live.rowsOf(t.name).toLocaleString()} of ${(t.rows || 0).toLocaleString()}`
                  : `${(t.rows || 0).toLocaleString()} rows`}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`${t.cols.length} columns`}
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {t.note}
              </Typography>
            </Box>
            {open && (
              <Box
                sx={{
                  px: 2,
                  pl: 5,
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
            )}
          </Box>
        );
      })}
    </SectionCard>
  );
}
SchemaCard.propTypes = {
  schema: PropTypes.array,
  totalRows: PropTypes.number,
  change: PropTypes.object,
};

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
/* Tables and tools as one map: each table with the tools that use it
   (user, 20 Nov). Off restores the two separate cards. */
const WORLD_MAP = true;

function ToolImplsCard({
  env,
  tools,
  schema = null,
  totalRows = 0,
  tableChange = null,
  envState,
  patch,
  onAddMcp,
  onRemove,
}) {
  const [openTable, setOpenTable] = useState(null);
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
  /* Tools grouped by tier, in the tier list's order. */
  const groups = TIERS.map((t) => ({
    tier: t.id,
    tools: tools.filter((tool) => toolSettingOf(tool, envState).tier === t.id),
  })).filter((g) => g.tools.length);
  /* Every tool in a group to one tier — one pending change, not one each. */
  const setGroupTier = (group, tier) => {
    if (!patch || !group.length) return;
    const all = { ...(envState?.toolEnv || {}) };
    group.slice(0, -1).forEach((t) => {
      all[t.name] = {
        ...(all[t.name] || {}),
        tier,
        safeTarget: null,
        confirmed: true,
      };
    });
    const last = group[group.length - 1];
    patch(
      toolEnvPatch(
        { ...envState, toolEnv: all },
        last.name,
        { tier, safeTarget: null },
        `${group.length} ${group.length === 1 ? "tool" : "tools"} set to ${tierOf(tier).word}`,
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

  /* The tool a chip opened, in the side drawer. */
  const drawerTool = tools.find((t) => t.name === openTool) || null;
  const toolDetail = (tool) => {
    const impl = toolImplFor(tool);
    const setting = toolSettingOf(tool, envState);
    const unsafe = needsSafeTarget(setting);
    return (
      <Box>
        {liveAdvisoryOf(setting) && (
          <Box sx={{ px: 2.5, pt: 1.5 }}>
            <LiveAdvisory
              setting={setting}
              onSwitch={
                patch
                  ? (tier) =>
                      patch(downgradeTierPatch(envState, setting.name, tier))
                  : null
              }
            />
          </Box>
        )}
        {unsafe && (
          <Box sx={{ px: 2.5, pt: 1.5 }}>
            <SafeTargetPrompt
              setting={setting}
              onConfirm={
                patch
                  ? (url) => patch(safeTargetPatch(envState, setting.name, url))
                  : null
              }
              onDowngrade={
                patch
                  ? (tier) =>
                      patch(downgradeTierPatch(envState, setting.name, tier))
                  : null
              }
            />
          </Box>
        )}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ px: 2.5, py: 1.25 }}
        >
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Answered by
          </Typography>
          <TierDial
            setting={setting}
            onChange={patch ? (tier) => setTier(setting, tier, tool) : null}
            onAllowLive={patch ? () => allowLive(setting, tool) : null}
          />
          <Typography sx={{ typography: "s3", color: "text.tertiary", pl: 1 }}>
            Faults
          </Typography>
          <FaultsControl
            setting={setting}
            onApply={
              patch ? (faults, word) => setFaults(setting, faults, word) : null
            }
          />
        </Stack>
        <ToolArgs tool={tool} env={env} envState={envState} setting={setting} />
        <ToolTryPanel tool={tool} env={env} envState={envState} />
        <CodeBlock file={impl.file} code={impl.code} />
        {tool.via?.kind === "mcp" && onRemove && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 2.5,
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
              variant="outlined"
              size="small"
              onClick={() => {
                setOpenTool(null);
                onRemove(tool);
              }}
            >
              Remove from the world
            </Button>
          </Stack>
        )}
      </Box>
    );
  };

  if (schema) {
    /* Which table each tool uses — the same reading the tool's details show. */
    const names = schema.map((t) => t.name);
    const byTable = new Map(names.map((n) => [n, []]));
    const none = [];
    tools.forEach((tool) => {
      const setting = toolSettingOf(tool, envState);
      const { reads, changes } = toolTablesOf(tool.name, setting.effect, names);
      const table = changes[0] || reads[0];
      const entry = { tool, writes: !!changes[0], setting };
      if (table && byTable.has(table)) byTable.get(table).push(entry);
      else none.push(entry);
    });
    const live = tableChange?.live;
    const fresh = tableChange?.fresh;
    const GRID = "12px minmax(160px, 220px) 90px 80px minmax(0, 1fr)";
    const chip = ({ tool, writes, setting }) => (
      <Tooltip
        key={tool.name}
        arrow
        title={`${tool.desc || tool.name}${writes ? " · changes this table" : " · reads this table"}`}
      >
        <Box
          component="button"
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setOpenTool(tool.name);
          }}
          sx={{
            ...focusRing,
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            px: 0.875,
            py: 0.25,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            bgcolor: openTool === tool.name ? "action.selected" : "transparent",
            color: "text.primary",
            cursor: "pointer",
            font: "inherit",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Typography
            component="span"
            sx={{
              typography: "s3",
              fontFamily: "ui-monospace, Menlo, monospace",
            }}
          >
            {tool.name}
          </Typography>
          {/* "Changes this table" is on hover; a pencil read as Edit. */}
          {(setting.faults || tool.via?.kind === "mcp") && (
            <Box
              component="span"
              sx={{
                width: 5,
                height: 5,
                borderRadius: "50%",
                bgcolor: setting.faults ? "status.warning.fg" : "text.tertiary",
              }}
            />
          )}
        </Box>
      </Tooltip>
    );
    const headCell = { typography: "s3", color: "text.tertiary" };
    return (
      <SectionCard
        title={`World · ${schema.length} tables · ${tools.length} tools`}
        subtitle={`${totalRows.toLocaleString()} rows. Each table with the tools that use it; a tool opens its details.`}
        action={
          patch && onAddMcp ? (
            <Button
              size="small"
              variant="outlined"
              onClick={onAddMcp}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                whiteSpace: "nowrap",
              }}
            >
              Add tools
            </Button>
          ) : null
        }
      >
        {/* How the tools answer — one line per tier, one Change each. */}
        {groups.map((g) => (
          <Stack
            key={g.tier}
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 2,
              py: 0.75,
              bgcolor: "background.neutral",
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={{ typography: "s3", fontWeight: 600 }}>
              {`${groups.length === 1 ? "All tools" : `${g.tools.length} tools`}: ${tierOf(g.tier).word}`}
            </Typography>
            <Typography
              noWrap
              sx={{
                typography: "s3",
                color: "text.tertiary",
                flex: 1,
                minWidth: 0,
              }}
            >
              {tierOf(g.tier).note}
            </Typography>
            {patch && (
              <GroupTierMenu
                current={g.tier}
                onPick={(tier) => setGroupTier(g.tools, tier)}
              />
            )}
          </Stack>
        ))}
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: GRID,
            columnGap: 1.5,
            px: 2,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box />
          <Typography sx={headCell}>Table</Typography>
          <Typography sx={headCell}>Rows</Typography>
          <Typography sx={headCell}>Columns</Typography>
          <Typography sx={headCell}>Tools that use it</Typography>
        </Box>
        {schema.map((t, i) => {
          const open = openTable === t.name;
          const used = byTable.get(t.name) || [];
          return (
            <Box
              key={t.name}
              sx={{
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <Box
                {...clickable(() => setOpenTable(open ? null : t.name), {
                  expanded: open,
                })}
                sx={{
                  ...focusRing,
                  display: "grid",
                  gridTemplateColumns: GRID,
                  columnGap: 1.5,
                  alignItems: "start",
                  px: 2,
                  py: 1,
                  cursor: "pointer",
                  bgcolor: open ? "action.selected" : "transparent",
                  "&:hover": {
                    bgcolor: open ? "action.selected" : "action.hover",
                  },
                }}
              >
                <Iconify
                  icon={
                    open
                      ? "solar:alt-arrow-down-linear"
                      : "solar:alt-arrow-right-linear"
                  }
                  width={12}
                  sx={{ color: "text.tertiary", mt: 0.5 }}
                />
                <Box sx={{ minWidth: 0 }}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <Typography
                      noWrap
                      sx={{
                        typography: "s2",
                        fontWeight: 500,
                        fontFamily: "ui-monospace, Menlo, monospace",
                        minWidth: 0,
                      }}
                    >
                      {t.name}
                    </Typography>
                    {fresh?.ids.includes(t.name) && (
                      <QuietChip>{`New in ${fresh.label}`}</QuietChip>
                    )}
                  </Stack>
                  <Typography
                    noWrap
                    title={t.note}
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {t.note}
                  </Typography>
                </Box>
                <Typography
                  sx={{
                    typography: "s3",
                    color: live?.ids.includes(t.name)
                      ? "text.primary"
                      : "text.secondary",
                    fontVariantNumeric: "tabular-nums",
                    pt: 0.25,
                  }}
                >
                  {live?.ids.includes(t.name)
                    ? live.state === "next"
                      ? `Seeds in ${live.label}`
                      : `Seeding · ${live.rowsOf(t.name).toLocaleString()}`
                    : (t.rows || 0).toLocaleString()}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.secondary",
                    fontVariantNumeric: "tabular-nums",
                    pt: 0.25,
                  }}
                >
                  {t.cols.length}
                </Typography>
                <Stack direction="row" flexWrap="wrap" sx={{ gap: 0.75 }}>
                  {used.length ? (
                    used.map(chip)
                  ) : (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        pt: 0.25,
                      }}
                    >
                      No tool uses it
                    </Typography>
                  )}
                </Stack>
              </Box>
              {open && (
                <Box
                  sx={{
                    px: 2,
                    pl: 5,
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
              )}
            </Box>
          );
        })}
        {none.length > 0 && (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: GRID,
              columnGap: 1.5,
              alignItems: "start",
              px: 2,
              py: 1,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              No table
            </Typography>
            <Box />
            <Box />
            <Stack direction="row" flexWrap="wrap" sx={{ gap: 0.75 }}>
              {none.map(chip)}
            </Stack>
          </Box>
        )}
        <SideDrawer
          open={!!drawerTool}
          onClose={() => setOpenTool(null)}
          width={{ xs: "100%", sm: 640 }}
        >
          {drawerTool && (
            <Stack sx={{ height: "100%", minHeight: 0 }}>
              <Stack
                direction="row"
                alignItems="flex-start"
                spacing={1.5}
                sx={{
                  px: 2.5,
                  py: 2,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  flexShrink: 0,
                }}
              >
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    sx={{
                      typography: "s1",
                      fontWeight: 600,
                      fontFamily: "ui-monospace, Menlo, monospace",
                    }}
                  >
                    {drawerTool.name}
                  </Typography>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {drawerTool.desc}
                  </Typography>
                </Box>
                <IconButton
                  size="small"
                  aria-label="Close"
                  onClick={() => setOpenTool(null)}
                  sx={{ mt: -0.25 }}
                >
                  <Iconify icon="solar:close-linear" width={18} />
                </IconButton>
              </Stack>
              <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
                {toolDetail(drawerTool)}
              </Box>
            </Stack>
          )}
        </SideDrawer>
      </SectionCard>
    );
  }

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
          <Tooltip
            arrow
            title="How each tool answers during a run. A change is a world edit, saved as the next environment version."
          >
            <Box
              component="span"
              sx={{ display: "inline-flex", verticalAlign: "middle", ml: 0.5 }}
            >
              <Iconify
                icon="solar:info-circle-linear"
                width={14}
                sx={{ color: "text.tertiary" }}
              />
            </Box>
          </Tooltip>
        </>
      }
      action={
        <Stack direction="row" spacing={1} alignItems="center">
          {patch && onAddMcp && (
            <Tooltip
              arrow
              title="From an MCP server — Shopify, Stripe, Zendesk or any URL. Each tool starts Simulated."
            >
              <Button
                size="small"
                variant="outlined"
                onClick={onAddMcp}
                startIcon={
                  <Iconify icon="solar:add-circle-linear" width={16} />
                }
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  flexShrink: 0,
                  whiteSpace: "nowrap",
                }}
              >
                Add tools
              </Button>
            </Tooltip>
          )}
        </Stack>
      }
    >
      {/* Grouped by how each tool is answered: one header and one Change
          per group, so thirteen identical "Simulated" menus became one. A
          tool's own tier and faults are set when it's opened. */}
      {groups.map((g, gi) => (
        <Box key={g.tier}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 2,
              py: 0.75,
              bgcolor: "background.neutral",
              borderTop: gi ? "1px solid" : "none",
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={{ typography: "s3", fontWeight: 600 }}>
              {tierOf(g.tier).word}
            </Typography>
            <Typography
              noWrap
              sx={{
                typography: "s3",
                color: "text.tertiary",
                flex: 1,
                minWidth: 0,
              }}
            >
              {`${tierOf(g.tier).note} · ${g.tools.length} ${g.tools.length === 1 ? "tool" : "tools"}`}
            </Typography>
            {patch && (
              <GroupTierMenu
                current={g.tier}
                onPick={(tier) => setGroupTier(g.tools, tier)}
              />
            )}
          </Stack>
          {g.tools.map((tool, ti) => {
            const open = openTool === tool.name;
            const impl = toolImplFor(tool);
            const setting = toolSettingOf(tool, envState);
            const unsafe = needsSafeTarget(setting);
            const gw = gatewayToolStatusOf(tool);
            const reach =
              tool.via?.kind === "mcp" && !gw
                ? toolReachOf(tool, { agent, calls })
                : null;
            return (
              <Box
                key={tool.name}
                sx={{
                  borderTop: ti ? "1px solid" : "none",
                  borderColor: "divider",
                }}
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1.5}
                  {...clickable(() => setOpenTool(open ? null : tool.name), {
                    expanded: open,
                  })}
                  sx={{
                    ...focusRing,
                    px: 2,
                    minHeight: 36,
                    cursor: "pointer",
                    bgcolor: open ? "action.selected" : "transparent",
                    "&:hover": {
                      bgcolor: open ? "action.selected" : "action.hover",
                    },
                  }}
                >
                  <Iconify
                    icon={
                      open
                        ? "solar:alt-arrow-down-linear"
                        : "solar:alt-arrow-right-linear"
                    }
                    width={12}
                    sx={{ color: "text.tertiary", flexShrink: 0 }}
                  />
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      fontWeight: 500,
                      fontFamily: "ui-monospace, Menlo, monospace",
                      width: 220,
                      flexShrink: 0,
                    }}
                  >
                    {tool.name}
                  </Typography>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.secondary",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {setting.safeTarget && setting.tier === "live"
                      ? `Live at ${setting.safeTarget}`
                      : setting.approval
                        ? `Waits for ${setting.approval.by} above $${setting.approval.above} · ${tool.desc || ""}`
                        : tool.desc}
                  </Typography>
                  {tool.via?.kind === "mcp" && (
                    <Tooltip
                      arrow
                      title={
                        tool.via.gateway
                          ? `From ${tool.via.label} in your Gateway (${tool.via.host}). The world answers it in runs; Live calls go through the Gateway.`
                          : `From the MCP server ${tool.via.host}. The world answers it during runs; the server is only called if the tool is Live.`
                      }
                    >
                      <Box component="span" sx={{ display: "inline-flex" }}>
                        <QuietChip>{`${tool.via.gateway ? "Gateway" : "MCP"} · ${tool.via.label}`}</QuietChip>
                      </Box>
                    </Tooltip>
                  )}
                  {gw && (
                    <Tooltip arrow title={gw.tip}>
                      <Box component="span" sx={{ display: "inline-flex" }}>
                        <QuietChip warn>{gw.label}</QuietChip>
                      </Box>
                    </Tooltip>
                  )}
                  {reach && (
                    <Tooltip arrow title={reach.tip}>
                      <Box component="span" sx={{ display: "inline-flex" }}>
                        <QuietChip warn={reach.kind === "blocked"}>
                          {reach.label}
                        </QuietChip>
                      </Box>
                    </Tooltip>
                  )}
                  {setting.faults && <QuietChip warn>Faults on</QuietChip>}
                </Stack>
                {liveAdvisoryOf(setting) && (
                  <Box sx={{ px: 2, pb: 1.25, pl: 5 }}>
                    <LiveAdvisory
                      setting={setting}
                      onSwitch={
                        patch
                          ? (tier) =>
                              patch(
                                downgradeTierPatch(
                                  envState,
                                  setting.name,
                                  tier,
                                ),
                              )
                          : null
                      }
                    />
                  </Box>
                )}
                {unsafe && (
                  <Box sx={{ px: 2, pb: 1.25, pl: 5 }}>
                    <SafeTargetPrompt
                      setting={setting}
                      onConfirm={
                        patch
                          ? (url) =>
                              patch(
                                safeTargetPatch(envState, setting.name, url),
                              )
                          : null
                      }
                      onDowngrade={
                        patch
                          ? (tier) =>
                              patch(
                                downgradeTierPatch(
                                  envState,
                                  setting.name,
                                  tier,
                                ),
                              )
                          : null
                      }
                    />
                  </Box>
                )}
                {open && (
                  <Box
                    sx={{
                      borderTop: "1px solid",
                      borderColor: "divider",
                      bgcolor: "background.neutral",
                    }}
                  >
                    {/* This tool's own tier and faults. */}
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={1.5}
                      sx={{ px: 2, pl: 5, py: 1 }}
                    >
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        Answered by
                      </Typography>
                      <TierDial
                        setting={setting}
                        onChange={
                          patch ? (tier) => setTier(setting, tier, tool) : null
                        }
                        onAllowLive={
                          patch ? () => allowLive(setting, tool) : null
                        }
                      />
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary", pl: 1 }}
                      >
                        Faults
                      </Typography>
                      <FaultsControl
                        setting={setting}
                        onApply={
                          patch
                            ? (faults, word) => setFaults(setting, faults, word)
                            : null
                        }
                      />
                    </Stack>
                    <ToolArgs
                      tool={tool}
                      env={env}
                      envState={envState}
                      setting={setting}
                    />
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
                          sx={{
                            typography: "s3",
                            color: "text.tertiary",
                            flex: 1,
                          }}
                        >
                          {`From ${tool.via.label}${tool.mcpName ? `, where it's ${tool.mcpName}` : ""}. Scenarios that call it can't run as proved once it's gone.`}
                        </Typography>
                        <Button
                          variant="outlined"
                          size="small"
                          onClick={() => onRemove(tool)}
                        >
                          Remove from the world
                        </Button>
                      </Stack>
                    )}
                  </Box>
                )}
              </Box>
            );
          })}
        </Box>
      ))}
    </SectionCard>
  );
}
ToolImplsCard.propTypes = {
  env: PropTypes.object,
  tools: PropTypes.array,
  /* With the schema: tables and tools as one map. */
  schema: PropTypes.array,
  totalRows: PropTypes.number,
  tableChange: PropTypes.object,
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
                  {`Take its ${src.tools.length} tool${src.tools.length === 1 ? "" : "s"} out of the world? Scenarios that call them can't run.`}
                </Typography>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() => setConfirming(null)}
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
/* A group's Change: every tool in it to another tier. Live is per tool
   (it's allowed one read-only tool at a time), so it isn't offered here. */
function GroupTierMenu({ current, onPick }) {
  const [anchor, setAnchor] = useState(null);
  const options = TIERS.filter((t) => t.id !== "live");
  return (
    <>
      <Button
        size="small"
        color="inherit"
        onClick={(e) => setAnchor(e.currentTarget)}
        endIcon={<Iconify icon="eva:chevron-down-fill" width={14} />}
        sx={{
          typography: "s3",
          color: "text.secondary",
          minWidth: 0,
          px: 0.75,
        }}
      >
        Change
      </Button>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {options.map((t) => (
          <MenuItem
            key={t.id}
            disabled={!!t.unavailable || t.id === current}
            onClick={() => {
              setAnchor(null);
              onPick(t.id);
            }}
            sx={{ typography: "s2" }}
          >
            <Box>
              <Typography
                sx={{ typography: "s2" }}
              >{`All to ${t.word}`}</Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {t.unavailable || t.note}
              </Typography>
            </Box>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
GroupTierMenu.propTypes = { current: PropTypes.string, onPick: PropTypes.func };

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
          {`Its MCP server ${server.host} lists ${missing.map((t) => t.name).join(", ")}. Add them so runs can answer.`}
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

/* A tool's contract, read-only: arguments, what it returns, the tables it
   reads or changes, and how it resets. The limits among the arguments are
   edited on the Contract tab. */
function ToolArgs({ tool, env, envState, setting }) {
  const args = toolArgsOf(tool, envState);
  const tables = (env?.seed?.tables || []).map((t) => t.name);
  const { reads, changes } = toolTablesOf(tool.name, setting.effect, tables);
  const live = setting.tier === "live";
  const rows = [
    [
      "Returns",
      toolOutputOf(tool, setting.effect)
        .map((f) => `${f.name} (${f.type})`)
        .join(", "),
      true,
    ],
    changes.length
      ? ["Changes", changes.join(", "), true]
      : reads.length
        ? ["Reads", reads.join(", "), true]
        : ["Reads", "None of the world's tables", false],
    [
      "Reset",
      live
        ? "Nothing to restore — a Live call reaches the real tool"
        : setting.effect === "real"
          ? "Its sandbox is recreated before every trial"
          : "Restored to the seed before every trial",
      false,
    ],
    /* world.md §3: a remote (Live) connection declares its safety, reset
       and evidence limits — production is never a fallback. */
    ...(live
      ? [
          [
            "Safety",
            "Calls reach the real system; only tools marked safe run, and nothing falls back to production",
            false,
          ],
          [
            "Evidence",
            "Only what the tool returns — no before/after state, so checks on the world's records are Unmeasured",
            false,
          ],
        ]
      : []),
  ];
  /* One spec table: every label in the same column, Arguments first. */
  const label = { typography: "s3", color: "text.tertiary" };
  return (
    <Box
      sx={{
        borderTop: "1px solid",
        borderColor: "divider",
        px: 2.5,
        py: 1.5,
        display: "grid",
        gridTemplateColumns: "84px minmax(0, 1fr)",
        columnGap: 2,
        rowGap: 0.75,
        alignItems: "baseline",
      }}
    >
      <Typography sx={label}>Arguments</Typography>
      {args.length === 0 ? (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          None
        </Typography>
      ) : (
        <ToolArgsGrid args={args} compact rowGap={0.5} />
      )}
      {rows.map(([name, value, mono]) => (
        <React.Fragment key={name}>
          <Typography sx={label}>{name}</Typography>
          <Typography
            sx={{
              typography: "s3",
              color: "text.secondary",
              ...(mono && { fontFamily: "ui-monospace, Menlo, monospace" }),
            }}
          >
            {value}
          </Typography>
        </React.Fragment>
      ))}
    </Box>
  );
}
ToolArgs.propTypes = {
  tool: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  setting: PropTypes.object.isRequired,
};

/* ── scenario checks ──────────────────────────────────────────────────── */

/* What proves a scenario before it runs. Graders are a link, not a copy —
   one home for them, one versioning rule. */
function ProofChecksCard({ onGo }) {
  const [openCheck, setOpenCheck] = useState(null);
  /* The same eight checks in every environment — folded to one line until
     asked for. Open or folded, the header is the same line, so it closes
     where it opened. */
  const [shown, setShown] = useState(false);
  const header = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      {...clickable(() => setShown((v) => !v), { expanded: shown })}
      sx={{
        ...focusRing,
        px: 2,
        py: 1.25,
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
        icon={
          shown ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
        }
        width={14}
        sx={{ color: "text.tertiary" }}
      />
    </Stack>
  );
  if (!shown) {
    return (
      <Box
        sx={{ borderRadius: 1, border: "1px solid", borderColor: "divider" }}
      >
        {header}
      </Box>
    );
  }
  return (
    <Box
      sx={{
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        overflow: "hidden",
      }}
    >
      {header}
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          px: 2,
          pb: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        On a run, sub-goals decide pass or fail and evaluations score each
        conversation
        {onGo && (
          <>
            {" — "}
            <Box
              component="span"
              {...clickable(() => onGo("evals"), { role: "link" })}
              sx={{
                ...focusRing,
                color: "text.primary",
                textDecoration: "underline",
                textUnderlineOffset: "2px",
                cursor: "pointer",
              }}
            >
              see Evaluations
            </Box>
          </>
        )}
        .
      </Typography>
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
    </Box>
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
