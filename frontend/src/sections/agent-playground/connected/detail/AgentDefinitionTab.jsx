import PropTypes from "prop-types";
import React, { useState } from "react";
import { format } from "date-fns";
import { Link as RouterLink } from "react-router-dom";
import { Box, Button, Link, Stack, Typography } from "@mui/material";
import {
  useAgentEnvironments,
  useAgentVersion,
  useStoredAgent,
} from "src/api/agents/agentDetail";
import { useAuthContext } from "src/auth/hooks";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { sourceLabel } from "../connectedAgentLabels";
import { LineSkeleton } from "./bits";
import { MONO } from "./format";
import {
  factsOf,
  hostedConfigurationRows,
  identityRows,
  reachRows,
  sourceRows,
  storedAgentRows,
} from "./definitionRows";
import CodeCard from "./code/CodeCard";
import { ConnectTracesDrawer } from "./AgentNextSteps";
import { observeProjectPath } from "./observeProject";
import ObservabilityToggle, {
  ObservabilityToggleSkeleton,
} from "./ObservabilityToggle";
import useAgentRoute from "./useAgentRoute";

function DefinitionRow({ row }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      spacing={2}
      sx={{ px: 1.5, py: 0.25 }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 128,
          flexShrink: 0,
          lineHeight: "24px",
        }}
      >
        {row.label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {row.items ? (
          <Stack direction="row" flexWrap="wrap" useFlexGap gap={0.75}>
            {row.items.map((item) => (
              <Box
                key={item}
                sx={{
                  px: 1,
                  height: 24,
                  display: "inline-flex",
                  alignItems: "center",
                  borderRadius: "4px",
                  bgcolor: "action.selected",
                }}
              >
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 500,
                    color: "text.secondary",
                    fontFamily: row.mono ? MONO : undefined,
                  }}
                >
                  {item}
                </Typography>
              </Box>
            ))}
          </Stack>
        ) : row.node ? (
          row.node
        ) : (
          <>
            <Typography
              noWrap={!row.multiline}
              title={row.multiline ? undefined : String(row.value)}
              sx={{
                typography: "s1",
                lineHeight: "24px",
                fontFamily: row.mono ? MONO : undefined,
                ...(row.multiline && {
                  whiteSpace: "pre-wrap",
                  // Four lines until opened, by height so it holds for any text.
                  ...(!expanded && { maxHeight: 96, overflow: "hidden" }),
                }),
              }}
            >
              {row.value}
            </Typography>
            {row.multiline &&
              (String(row.value).split("\n").length > 4 ||
                String(row.value).length > 320) && (
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => setExpanded((v) => !v)}
                  sx={{ typography: "s2", minWidth: 0, px: 0.5, mt: 0.25 }}
                >
                  {expanded ? "Show less" : "Show the whole prompt"}
                </Button>
              )}
          </>
        )}
      </Box>
    </Stack>
  );
}

DefinitionRow.propTypes = { row: PropTypes.object.isRequired };

// The prompt, read like a document: its own panel, six lines until opened.
function PromptPanel({ row }) {
  const [open, setOpen] = useState(false);
  const text = String(row.value || "");
  const long = text.split("\n").length > 6 || text.length > 480;
  if (!text.trim()) return null;
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        px: 2,
        py: 1.5,
      }}
    >
      <Typography sx={{ typography: "s2", color: "text.subtitle", mb: 0.5 }}>
        {row.label}
      </Typography>
      <Typography
        sx={{
          typography: "s1",
          lineHeight: "24px",
          whiteSpace: "pre-wrap",
          ...(!open && long && { maxHeight: 144, overflow: "hidden" }),
        }}
      >
        {text}
      </Typography>
      {long && (
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={() => setOpen((v) => !v)}
          endIcon={
            <Iconify
              icon={open ? "eva:chevron-up-fill" : "eva:chevron-down-fill"}
              width={16}
            />
          }
          sx={{ mt: 1 }}
        >
          {open ? "Show less" : "Show the whole prompt"}
        </Button>
      )}
    </Box>
  );
}

PromptPanel.propTypes = { row: PropTypes.object.isRequired };

// A loading row in the shape of DefinitionRow: label column, then the value.
const SKELETON_VALUES = [72, 96, 180, "70%"];
function DefinitionRowSkeleton({ value }) {
  return (
    <Stack
      data-testid="definition-row-skeleton"
      direction="row"
      spacing={2}
      sx={{
        px: 1.5,
        py: 0.75,
        borderTop: "1px solid",
        borderColor: "divider",
        "&:first-of-type": { borderTop: "none" },
      }}
    >
      <Typography
        sx={{ typography: "s1", lineHeight: "24px", width: 150, flexShrink: 0 }}
      >
        <LineSkeleton width={80} />
      </Typography>
      <Typography sx={{ typography: "s1", lineHeight: "24px", flex: 1 }}>
        <LineSkeleton width={value} />
      </Typography>
    </Stack>
  );
}

DefinitionRowSkeleton.propTypes = {
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};

const Note = ({ children, tone }) => (
  <Typography
    sx={{
      px: 1.5,
      py: 1,
      // A line only between items; under the card title, which already
      // draws one, there's none.
      "* + &": { borderTop: "1px solid", borderColor: "divider" },
      typography: "s3",
      color: tone === "warning" ? "status.warning.fg" : "text.subtitle",
    }}
  >
    {children}
  </Typography>
);

Note.propTypes = { children: PropTypes.node, tone: PropTypes.string };

// A row with nothing in it: listed once as "Not set: …", not as its own row.
const EMPTY_VALUES = new Set(["", "—", "-", "None", "None yet"]);
const isEmptyRow = (row) =>
  !row.items?.length &&
  !row.node &&
  (row.value == null || EMPTY_VALUES.has(String(row.value).trim()));

/**
 * Definition: what we hold for the selected version, read-only. Identity,
 * Source and Reach come from the version's own snapshot and stored Agent;
 * Configuration is the stored Agent for code, the model and prompt for a
 * hosted agent; Links points at the environments testing it.
 */
export default function AgentDefinitionTab() {
  const { agentId, agent, versionId } = useAgentRoute();
  const { role } = useAuthContext();
  const canEdit = !!RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role];
  const [connecting, setConnecting] = useState(false);
  const {
    data: version,
    isLoading,
    isError,
  } = useAgentVersion(agentId, versionId);
  const environments = useAgentEnvironments(agentId);

  const kind = version?.agentKind ?? agent?.agentKind ?? null;
  // Only an agent with a kind ever stored an Agent; asking for the others
  // would always 404.
  const stored = useStoredAgent(agentId, versionId, { enabled: !!kind });

  const loading = !agent || isLoading || stored.isLoading;
  const isCode = kind === "source";
  const details = stored.data;
  const facts = factsOf(agent ?? {}, version ?? {}, details?.agent);
  const source = agent?.source ?? version?.config?.provider;
  const readFrom = source ? sourceLabel(source) : null;
  // Facts wait for the selected version, so another version's never show
  // under its label.
  const factsNote =
    isError && !version ? "Couldn't load this version" : undefined;
  const factRows = (rows) => (version ? rows : []);

  // What the configuration holds: its rows, a provenance note, or (when
  // there's nothing to show) one message in their place.
  let configRows = [];
  let configNote = null;
  let configuration = null;
  if (!versionId) {
    configuration = <Note>No definition stored for this version</Note>;
  } else if (isError && !version) {
    configuration = <Note>Couldn&apos;t load this version</Note>;
  } else if (stored.isError) {
    configuration = (
      <Note>{stored.error?.message || "Couldn't load the stored Agent"}</Note>
    );
  } else if (details) {
    configRows = storedAgentRows(details);
    configNote = (
      <>
        <Note>
          {details.origin === "reading"
            ? `Read from the code on ${format(new Date(details.storedAt), "dd-MM-yyyy")} · SDK ${details.sdkVersion}`
            : `Stored by SDK ${details.sdkVersion} on ${format(new Date(details.storedAt), "dd-MM-yyyy")}`}
        </Note>
        {details.setupStale && (
          <Note tone="warning">
            This setup may be out of date. The agent changed after it was
            stored.
          </Note>
        )}
      </>
    );
  } else if (isCode) {
    configuration = <Note>No definition stored for this version</Note>;
  } else {
    configRows = hostedConfigurationRows(facts);
    if (!configRows.length)
      configuration = <Note>No definition stored for this version</Note>;
  }

  const envs = environments.data ?? [];
  const observe = agent?.observeProject;
  const observeRow = {
    label: "Observe project",
    node: observe ? (
      <Typography
        data-testid="observe-project-link"
        sx={{ typography: "s2_1", lineHeight: "24px" }}
      >
        {observe.name}
        <Box component="span" sx={{ color: "text.secondary" }}>
          {" · linked · "}
        </Box>
        <Link
          href={observeProjectPath(observe.id)}
          target="_blank"
          rel="noopener"
          underline="hover"
          sx={{ fontWeight: 500 }}
        >
          Open in Observe
        </Link>
      </Typography>
    ) : (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        data-testid="observe-project-link"
      >
        <Typography
          sx={{
            typography: "s2_1",
            lineHeight: "24px",
            color: "text.secondary",
          }}
        >
          {isCode
            ? "Not linked"
            : "Not linked · turn observability on to pull its calls in"}
        </Typography>
        {isCode && (
          <CustomTooltip
            show={!canEdit}
            title="You don't have permission to link a project."
            size="small"
            arrow
          >
            <span>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={!canEdit}
                onClick={() => setConnecting(true)}
              >
                Link a project
              </Button>
            </span>
          </CustomTooltip>
        )}
      </Stack>
    ),
  };
  const links = [
    observeRow,
    {
      label: "Environments",
      node: environments.isLoading ? (
        <Typography sx={{ typography: "s2", lineHeight: "24px" }}>
          <LineSkeleton width={120} />
        </Typography>
      ) : envs.length ? (
        <Stack
          direction="row"
          flexWrap="wrap"
          useFlexGap
          columnGap={1.5}
          rowGap={0.5}
          sx={{ lineHeight: "24px" }}
        >
          {envs.map((env) => (
            <Link
              key={env.id}
              component={RouterLink}
              to={paths.dashboard.simulate.environments.detail(env.id)}
              underline="hover"
              sx={{ typography: "s2_1", fontWeight: 500 }}
            >
              {env.name}
            </Link>
          ))}
        </Stack>
      ) : (
        <Typography
          sx={{
            typography: "s2_1",
            lineHeight: "24px",
            color: "text.secondary",
          }}
        >
          None yet
        </Typography>
      ),
    },
  ];

  // Prompt first, tools under it, everything else in one two-column table.
  const isPrompt = (row) => !!row.multiline;
  const isTools = (row) => String(row.label).startsWith("Tools");
  const promptRow = configRows.find(isPrompt);
  const toolsRow = configRows.find(isTools);
  const seen = new Set();
  const detailRows = [
    ...configRows.filter((row) => !isPrompt(row) && !isTools(row)),
    ...factRows(sourceRows(facts, { isCode })),
    // The name is the page title, so it isn't repeated here.
    ...factRows(identityRows(facts)).filter((row) => row.label !== "Name"),
    ...factRows(reachRows(facts)),
    ...(agent ? links : []),
  ].filter((row) => {
    // Type is in both the configuration and the identity: show it once.
    const key = `${row.label}|${row.node ? "" : String(row.value ?? row.items)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const shownDetails = detailRows.filter((row) => !isEmptyRow(row));
  const unsetDetails = detailRows.filter(isEmptyRow).map((row) => row.label);

  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          Definition of
        </Typography>
        {loading ? (
          <LineSkeleton width={28} />
        ) : (
          version && (
            <Typography
              sx={{ typography: "s2_1", fontFamily: MONO, fontWeight: 500 }}
            >
              {version.label}
            </Typography>
          )
        )}
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {readFrom ? `Read-only · read from ${readFrom}` : "Read-only"}
        </Typography>
      </Stack>
      {loading ? (
        SKELETON_VALUES.map((value) => (
          <DefinitionRowSkeleton key={value} value={value} />
        ))
      ) : (
        <>
          {configuration}
          {factsNote && <Note>{factsNote}</Note>}
          {promptRow && <PromptPanel row={promptRow} />}
          {toolsRow && !isEmptyRow(toolsRow) && (
            <Box sx={{ mx: -1.5 }}>
              <DefinitionRow row={toolsRow} />
            </Box>
          )}
          {/* Every other field, as one dense two-column table. */}
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                md: "repeat(2, minmax(0, 1fr))",
              },
              columnGap: 4,
              borderTop: "1px solid",
              borderColor: "divider",
              pt: 1,
              mx: -1.5,
            }}
          >
            {shownDetails.map((row) => (
              <DefinitionRow key={row.label} row={row} />
            ))}
          </Box>
          {(unsetDetails.length > 0 || configNote) && (
            <Box sx={{ mx: -1.5, mt: -1 }}>
              {unsetDetails.length > 0 && (
                <Note>{`Not set: ${unsetDetails.join(", ")}`}</Note>
              )}
              {configNote}
            </Box>
          )}
        </>
      )}
      {agent ? (
        <ObservabilityToggle agent={agent} />
      ) : (
        <ObservabilityToggleSkeleton />
      )}
      {isCode && versionId && (
        <CodeCard
          agentId={agentId}
          versionId={versionId}
          versionLabel={version?.label || "this version"}
        />
      )}
      {agent && isCode && (
        <ConnectTracesDrawer
          agent={agent}
          open={connecting}
          onClose={() => setConnecting(false)}
          canEdit={canEdit}
        />
      )}
    </Stack>
  );
}
