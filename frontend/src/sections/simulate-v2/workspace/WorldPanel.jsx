import PropTypes from "prop-types";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Box, ButtonBase, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import { agentVersions, currentAgentVersion } from "../_mock/versions";
import {
  toolCallers,
  toolFit,
  toolSince,
  worldToolsFor,
} from "../_mock/toolFit";
import CapabilityGraph from "./CapabilityGraph";
import { focusRing } from "../components/clickable";
import { TwinSandboxSection } from "./OverviewPanel";
import WorldInternalsSection from "./WorldInternalsSection";
import { agentMcpTools, mcpServersOfAgent } from "../_mock/mcpServers";
import {
  agentCallsOf,
  agentServerIdsOf,
  mcpPlacementOf,
} from "../_mock/worldTools";
import ToolFitBanner from "./ToolFitBanner";
import ConnectOnlyWorld, { EnvironmentKindNote } from "./ConnectOnlyWorld";
import { isConnectOnly } from "../_mock/envModel";
import { envLabelOf } from "../_mock/envVersionKinds";
import { firstSnapshotOfWorld } from "../_mock/envSnapshot";
import BuildStreamNote from "../environments/BuildStreamNote";
import SourcesSection from "./data/SourcesSection";
import IdentityMapCard from "./IdentityMapCard";
import SandboxPreviewDrawer from "./SandboxPreviewDrawer";

/**
 * The world — the data and tool answers a run executes against.
 *
 * Separate from the contract on purpose. The contract is the rules (how a run
 * ends, who else acts in it); the world is what the agent talks to. The world
 * is also the part that grows: it was first built to answer the tools of the
 * agent version the environment was made from, and it learns a new tool each
 * time a later version brings one.
 *
 * The world is data and tools (V7a): the tool handlers belong to it, so a
 * change to how a tool behaves is a new environment version. A world read
 * from a repository says which folders it came from, so a commit there reads
 * as the world change it is. A connect-only environment has no world at all —
 * it says so instead of showing empty tables.
 */

/* Where a repo-built world lives in its repository. */
const repoOriginOf = (env, envState) => {
  const src = env?.builtFrom;
  if (src?.kind !== "repo" || !src.value) return null;
  const repo = String(src.value)
    .replace(/^https?:\/\/(www\.)?github\.com\//, "")
    .replace(/\.git$/, "")
    .replace(/\/$/, "");
  const ref = src.ref || envState?.agent?.definition?.ref || "main";
  return { repo, ref };
};

function VersionTag({ label, dim }) {
  return (
    <Box
      component="span"
      sx={{
        px: 0.625,
        height: 18,
        display: "inline-flex",
        alignItems: "center",
        borderRadius: "4px",
        border: "1px solid",
        borderColor: "divider",
        typography: "s3",
        fontWeight: 600,
        color: dim ? "text.disabled" : "text.secondary",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {label}
    </Box>
  );
}
VersionTag.propTypes = { label: PropTypes.string, dim: PropTypes.bool };

export default function WorldPanel({
  env,
  envState,
  patch,
  onGo,
  locked = false,
  buildProgress = null,
  buildDone = null,
}) {
  /* The sandbox preview opens from here, or from the header's ⋯ (?sandbox=1). */
  const [params, setParams] = useSearchParams();
  const sandboxOpen = params.get("sandbox") === "1";
  const setSandbox = (on) =>
    setParams(
      (cur) => {
        const next = new URLSearchParams(cur);
        if (on) next.set("sandbox", "1");
        else next.delete("sandbox");
        return next;
      },
      { replace: true },
    );
  /* On the build screen: the world fills in as it's built — each tool's row
     once its handler is written, the rest once the world is done. */
  const inBuild = Array.isArray(buildDone) && !buildDone.includes("build");
  const [graphOpen, setGraphOpen] = useState(false);
  /* The add-tools drawer: opened from the summary line up here, or from the tools further down. */
  const [mcp, setMcp] = useState(null);
  const canAddTools = !inBuild && !locked && !!patch;
  const read = !!buildDone?.includes("understand");
  const fit = toolFit(env, envState);
  const since = toolSince(env, envState);
  const callers = toolCallers(env, envState);
  const activeAgent = currentAgentVersion(envState)?.label;
  const versionsCount = agentVersions(envState).length;

  const origin = repoOriginOf(env, envState);

  if (isConnectOnly(env)) {
    return (
      <Box sx={{ p: 2 }}>
        {/* No heading — the tab names the page; its description is the tab's hover hint. */}
        <EnvironmentKindNote env={env} envState={envState} sx={{ mb: 2 }} />
        <ConnectOnlyWorld env={env} sx={{ mb: 2 }} />
        {(env.tools?.length || 0) > 0 && (
          <Box sx={{ mb: 2 }}>
            <CapabilityGraph env={env} envState={envState} onGo={onGo} />
          </Box>
        )}
      </Box>
    );
  }

  /* Tools the agent gets from its MCP servers that the world doesn't answer
     yet — counted the way the World tab's notice and the drawer place them. */
  const edited = worldToolsFor(env, envState);
  const agentServerIds = agentServerIdsOf(envState);
  const agentMcp = agentMcpTools(envState?.agent);
  const mcpMissing = mcpServersOfAgent(envState?.agent).flatMap(
    ({ server, tools: listed }) =>
      listed.filter(
        (t) =>
          mcpPlacementOf(t, server, edited, { agentServerIds }).status !==
          "inWorld",
      ),
  ).length;
  /* Everything the agent calls: its own tools and the ones its MCP servers list. */
  /* Everything the agent calls at the version tested: its own tools and the ones its MCP servers list. */
  const callsCount =
    agentCallsOf(env, envState).size ||
    new Set([...fit.calls.map((t) => t.name), ...agentMcp.map((t) => t.name)])
      .size;
  /* The strip's summary — while the world builds, and when the banner already says what doesn't fit, it says nothing. */
  const summaryText =
    inBuild || (activeAgent && !fit.fits)
      ? null
      : !activeAgent
        ? fit.world.length
          ? `No agent connected yet — the world answers the ${fit.world.length} tools it was built for.`
          : "No agent connected yet, and no tools in the world yet."
        : mcpMissing
          ? `Agent ${activeAgent} calls ${callsCount} tools — the world answers ${callsCount - mcpMissing}; ${mcpMissing} from its MCP server it doesn't answer yet.`
          : `Agent ${activeAgent} calls ${callsCount} tools — the world answers every one.`;
  const allRows = [
    ...fit.world.map((t) => ({ tool: t, answered: true })),
    ...fit.missing.map((t) => ({ tool: t, answered: false })),
  ];
  const handlers = buildProgress?.handlers || 0;
  const rows = inBuild ? allRows.slice(0, handlers) : allRows;
  /* Since / Called by only earn columns once they differ row to row — with
     one environment version and one agent version every row said the same. */
  const sinceOf = (t) => since[t.name] || fit.envVersion;
  const callerKey = (t) => (callers[t.name] || []).join(",");
  const showSince =
    new Set(allRows.filter((r) => r.answered).map((r) => sinceOf(r.tool)))
      .size > 1 || allRows.some((r) => !r.answered);
  const showCallers =
    versionsCount > 1 &&
    new Set(allRows.map((r) => callerKey(r.tool))).size > 1;
  const cols = [
    "minmax(160px, 1.1fr)",
    "minmax(0, 2fr)",
    showSince && "124px",
    showCallers && "minmax(120px, 1fr)",
  ]
    .filter(Boolean)
    .join(" ");

  if (inBuild && !read) {
    return (
      <Box sx={{ p: 2 }}>
        <BuildStreamNote
          waiting
          label="The world is built once the agent has been read — its tools first"
        />
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      <SandboxPreviewDrawer
        open={sandboxOpen}
        onClose={() => setSandbox(false)}
        env={env}
        envState={envState}
        patch={patch}
        locked={locked}
      />
      {/* One line above the world: what the agent calls against what the world
          answers, and how to add more — then, quieter on the right, the kind of
          proof a run is and where the world lives in the repo (each explained
          on hover). No heading: the tab names the page. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ mb: 2, px: 0.5, minHeight: 24, flexWrap: "wrap", rowGap: 0.75 }}
      >
        {summaryText && (
          <Typography
            sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
          >
            {summaryText}
          </Typography>
        )}
        {canAddTools && (
          <ButtonBase
            onClick={() => setMcp({ prefill: null })}
            sx={{
              ...focusRing,
              typography: "s2",
              fontWeight: 600,
              color: "text.primary",
              textDecoration: "underline",
              textUnderlineOffset: 3,
              textDecorationColor: (t) => t.palette.text.disabled,
              borderRadius: "4px",
              flexShrink: 0,
              "&:hover": { textDecorationColor: "currentColor" },
            }}
          >
            Add tools from an MCP server
          </ButtonBase>
        )}
        <ButtonBase
          onClick={() => setSandbox(true)}
          sx={{
            ...focusRing,
            typography: "s2",
            fontWeight: 600,
            color: "text.primary",
            textDecoration: "underline",
            textUnderlineOffset: 3,
            textDecorationColor: (t) => t.palette.text.disabled,
            borderRadius: "4px",
            flexShrink: 0,
            "&:hover": { textDecorationColor: "currentColor" },
          }}
        >
          Try in sandbox
        </ButtonBase>
        <Box flex={1} />
        <EnvironmentKindNote env={env} envState={envState} compact />
        {origin && (
          <Tooltip
            arrow
            title={`tools-api/ and db/ in ${origin.repo} @ ${origin.ref} — a commit touching these is a world change.`}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.5}
              sx={{ minWidth: 0, cursor: "default" }}
            >
              <Iconify
                icon="solar:folder-with-files-linear"
                width={14}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  fontFamily: "ui-monospace, Menlo, monospace",
                }}
              >
                {`${String(origin.repo).split("/").pop()} @ ${origin.ref}`}
              </Typography>
            </Stack>
          </Tooltip>
        )}
      </Stack>

      {inBuild &&
        (handlers < allRows.length ? (
          <BuildStreamNote
            label="Writing a handler for each tool"
            count={handlers}
            total={allRows.length}
          />
        ) : (
          <BuildStreamNote
            label="Seeding the world"
            count={buildProgress?.stores || 0}
            total={env.seed?.tables?.length || 0}
          />
        ))}

      {!inBuild && (
        <ToolFitBanner
          env={env}
          envState={envState}
          patch={patch}
          sx={{ mb: 2 }}
          locked={locked}
        />
      )}

      {/* While it builds, tools appear here as their handlers are written;
          after, the handlers list below carries them — with tier and faults —
          so the same twelve rows aren't listed twice. */}
      {inBuild ? (
        <SectionCard
          title={`Tools · ${allRows.length}`}
          subtitle={
            !activeAgent
              ? "No agent connected yet — the tools the world was built to answer."
              : fit.fits
                ? `Agent ${activeAgent} calls ${fit.calls.length} tools — the world answers every one.`
                : `Agent ${activeAgent} calls ${fit.missing.length === 1 ? "one tool" : `${fit.missing.length} tools`} the world can't answer yet.`
          }
          sx={{ mb: 2 }}
        >
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: cols,
              columnGap: 2,
              px: 2.5,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            {[
              "Tool",
              "What it does",
              showSince && "Since",
              showCallers && "Called by",
            ]
              .filter(Boolean)
              .map((h) => (
                <Typography
                  key={h}
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.tertiary",
                  }}
                >
                  {h}
                </Typography>
              ))}
          </Box>
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {rows.map(({ tool, answered }) => (
              <Box
                key={tool.name}
                sx={{
                  display: "grid",
                  gridTemplateColumns: cols,
                  columnGap: 2,
                  alignItems: "center",
                  px: 2.5,
                  py: 1.125,
                  ...(!answered && { bgcolor: "action.hover" }),
                }}
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    fontFamily: "ui-monospace, Menlo, monospace",
                  }}
                >
                  {tool.name}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s2", color: "text.secondary" }}
                >
                  {tool.desc}
                </Typography>
                {showSince && (
                  <Box>
                    {answered ? (
                      <VersionTag
                        label={`environment ${firstSnapshotOfWorld(env, envState, since[tool.name] || fit.envVersion)?.label || envLabelOf(env, envState, since[tool.name] || fit.envVersion)}`}
                      />
                    ) : (
                      <Typography sx={{ typography: "s3", fontWeight: 600 }}>
                        Not yet
                      </Typography>
                    )}
                  </Box>
                )}
                {showCallers && (
                  <Stack
                    direction="row"
                    spacing={0.5}
                    flexWrap="wrap"
                    rowGap={0.5}
                  >
                    {(callers[tool.name] || []).map((v) => (
                      <VersionTag key={v} label={v} />
                    ))}
                    {!(callers[tool.name] || []).length && (
                      <Typography
                        sx={{ typography: "s3", color: "text.disabled" }}
                      >
                        No version calls it
                      </Typography>
                    )}
                  </Stack>
                )}
              </Box>
            ))}
          </Stack>
        </SectionCard>
      ) : null}

      {/* The graph and the world's internals land once the world is built. */}
      {!inBuild && envState?.twinBacking && (
        <Box sx={{ mb: 2 }}>
          <TwinSandboxSection env={env} envState={envState} />
        </Box>
      )}
      {!inBuild && envState?.twinBacking && (
        <IdentityMapCard envState={envState} />
      )}
      {/* The graph is one more view of the same tools — folded until asked for. */}
      {!inBuild &&
        !envState?.twinBacking &&
        (envState?.agent || (env.tools?.length || 0) > 0) && (
          <Box sx={{ mb: 2 }}>
            {graphOpen ? (
              <>
                <CapabilityGraph
                  env={env}
                  envState={envState}
                  onGo={onGo}
                  onClose={() => setGraphOpen(false)}
                />
              </>
            ) : (
              <ButtonBase
                onClick={() => setGraphOpen(true)}
                aria-expanded={false}
                sx={{
                  ...focusRing,
                  width: "100%",
                  justifyContent: "flex-start",
                  gap: 1,
                  px: 2,
                  py: 1.25,
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: "divider",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify
                  icon="solar:graph-new-linear"
                  width={16}
                  sx={{ color: "text.tertiary" }}
                />
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  Capability graph
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flex: 1,
                    textAlign: "left",
                  }}
                >
                  Tools, flows, personas and guardrails as one picture
                </Typography>
                <Iconify
                  icon="solar:alt-arrow-down-linear"
                  width={14}
                  sx={{ color: "text.tertiary" }}
                />
              </ButtonBase>
            )}
          </Box>
        )}

      {!inBuild && (
        <SourcesSection
          env={env}
          envState={envState}
          patch={patch}
          readOnly={locked}
        />
      )}

      {!inBuild && (
        <WorldInternalsSection
          env={env}
          envState={envState}
          patch={patch}
          onGo={onGo}
          locked={locked}
          mcp={mcp}
          onMcp={setMcp}
        />
      )}
    </Box>
  );
}

WorldPanel.propTypes = {
  buildProgress: PropTypes.object,
  buildDone: PropTypes.array,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  onGo: PropTypes.func,
  locked: PropTypes.bool,
};
