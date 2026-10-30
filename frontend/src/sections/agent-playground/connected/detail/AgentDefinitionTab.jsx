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
import { paths } from "src/routes/paths";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { sourceLabel } from "../connectedAgentLabels";
import { Card, LineSkeleton } from "./bits";
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
import { ConnectTracesDrawer, observeProjectPath } from "./AgentNextSteps";
import ObservabilityToggle, {
  ObservabilityToggleSkeleton,
} from "./ObservabilityToggle";
import useAgentRoute from "./useAgentRoute";

function DefinitionRow({ row }) {
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      spacing={2}
      sx={{
        px: 2,
        py: 1,
        borderTop: "1px solid",
        borderColor: "divider",
        "&:first-of-type": { borderTop: "none" },
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 150,
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
                  borderRadius: 0.75,
                  bgcolor: "action.selected",
                }}
              >
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 500,
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
          <Typography
            noWrap={!row.multiline}
            title={row.multiline ? undefined : String(row.value)}
            sx={{
              typography: "s2",
              lineHeight: "24px",
              fontFamily: row.mono ? MONO : undefined,
              ...(row.multiline && {
                whiteSpace: "pre-wrap",
                display: "-webkit-box",
                WebkitLineClamp: 6,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }),
            }}
          >
            {row.value}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}

DefinitionRow.propTypes = { row: PropTypes.object.isRequired };

// A loading row in the shape of DefinitionRow: label column, then the value.
const SKELETON_VALUES = [72, 96, 180, "70%"];
function DefinitionRowSkeleton({ value }) {
  return (
    <Stack
      data-testid="definition-row-skeleton"
      direction="row"
      spacing={2}
      sx={{
        px: 2,
        py: 1,
        borderTop: "1px solid",
        borderColor: "divider",
        "&:first-of-type": { borderTop: "none" },
      }}
    >
      <Typography
        sx={{ typography: "s2", lineHeight: "24px", width: 150, flexShrink: 0 }}
      >
        <LineSkeleton width={80} />
      </Typography>
      <Typography sx={{ typography: "s2", lineHeight: "24px", flex: 1 }}>
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
      px: 2,
      py: 1,
      // A line only between items; under the card title, which already
      // draws one, there's none.
      "* + &": { borderTop: "1px solid", borderColor: "divider" },
      typography: "s2",
      color: tone === "warning" ? "warning.main" : "text.secondary",
    }}
  >
    {children}
  </Typography>
);

Note.propTypes = { children: PropTypes.node, tone: PropTypes.string };

const Section = ({ title, rows, note, loading, children }) => (
  <Card title={title}>
    {loading
      ? SKELETON_VALUES.slice(0, 2).map((value) => (
          <DefinitionRowSkeleton key={value} value={value} />
        ))
      : rows?.map((row) => <DefinitionRow key={row.label} row={row} />)}
    {!loading && !rows?.length && !children && (
      <Note>{note ?? "Nothing recorded for this version"}</Note>
    )}
    {!loading && children}
  </Card>
);

Section.propTypes = {
  title: PropTypes.node.isRequired,
  rows: PropTypes.array,
  note: PropTypes.string,
  loading: PropTypes.bool,
  children: PropTypes.node,
};

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

  let configuration;
  if (!versionId) {
    configuration = <Note>No definition stored for this version</Note>;
  } else if (isError && !version) {
    configuration = <Note>Couldn&apos;t load this version</Note>;
  } else if (stored.isError) {
    configuration = (
      <Note>{stored.error?.message || "Couldn't load the stored Agent"}</Note>
    );
  } else if (details) {
    configuration = (
      <>
        {storedAgentRows(details).map((row) => (
          <DefinitionRow key={row.label} row={row} />
        ))}
        <Note>
          {`Stored by SDK ${details.sdkVersion} on ${format(new Date(details.storedAt), "dd-MM-yyyy")}`}
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
    const rows = hostedConfigurationRows(facts);
    configuration = rows.length ? (
      rows.map((row) => <DefinitionRow key={row.label} row={row} />)
    ) : (
      <Note>No definition stored for this version</Note>
    );
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
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={!canEdit}
            onClick={() => setConnecting(true)}
          >
            Link a project
          </Button>
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

  return (
    <Stack spacing={3}>
      <Stack direction="row" alignItems="center" spacing={1}>
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
      <Section
        title="Identity"
        loading={loading}
        note={factsNote}
        rows={factRows(identityRows(facts))}
      />
      <Section
        title="Source"
        loading={loading}
        note={factsNote}
        rows={factRows(sourceRows(facts, { isCode }))}
      />
      <Section
        title="Reach"
        loading={loading}
        note={factsNote}
        rows={factRows(reachRows(facts))}
      />
      <Card title="Configuration">
        {loading
          ? SKELETON_VALUES.map((value) => (
              <DefinitionRowSkeleton key={value} value={value} />
            ))
          : configuration}
      </Card>
      {agent ? (
        <ObservabilityToggle agent={agent} />
      ) : (
        <ObservabilityToggleSkeleton />
      )}
      <Section title="Links" loading={!agent} rows={links} />
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
