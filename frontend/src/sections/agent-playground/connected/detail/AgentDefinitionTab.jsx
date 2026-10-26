import PropTypes from "prop-types";
import React from "react";
import { format } from "date-fns";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import { useAgentVersion, useStoredAgent } from "src/api/agents/agentDetail";
import { sourceLabel } from "../connectedAgentLabels";
import { Card, LineSkeleton } from "./bits";
import { MONO } from "./format";
import { hostedRows, storedAgentRows } from "./definitionRows";
import ComingSoonCard from "./ComingSoonCard";
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
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "background.neutral",
                }}
              >
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: 500,
                    fontFamily: row.mono ? MONO : undefined,
                    fontSize: row.mono ? 11.5 : undefined,
                  }}
                >
                  {item}
                </Typography>
              </Box>
            ))}
          </Stack>
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
      typography: "s3",
      color: tone === "warning" ? "#D97706" : "text.subtitle",
    }}
  >
    {children}
  </Typography>
);

Note.propTypes = { children: PropTypes.node, tone: PropTypes.string };

/** Definition: what we read for the selected version, and observability. */
export default function AgentDefinitionTab() {
  const { agentId, agent, versionId } = useAgentRoute();
  const {
    data: version,
    isLoading,
    isError,
  } = useAgentVersion(agentId, versionId);

  const kind = version?.agentKind ?? agent?.agentKind ?? null;
  // Only an agent with a kind ever stored an Agent; asking for the others
  // would always 404.
  const stored = useStoredAgent(agentId, versionId, { enabled: !!kind });

  const loading = !agent || isLoading || stored.isLoading;
  const isCode = kind === "source";
  const details = stored.data;
  const readFrom = agent
    ? sourceLabel(agent.source ?? version?.config?.provider)
    : "—";

  let body;
  if (loading) {
    body = SKELETON_VALUES.map((value) => (
      <DefinitionRowSkeleton key={value} value={value} />
    ));
  } else if (!versionId) {
    body = <Note>No definition stored for this version</Note>;
  } else if (isError && !version) {
    body = <Note>Couldn&apos;t load this version</Note>;
  } else if (stored.isError) {
    body = (
      <Note>{stored.error?.message || "Couldn't load the stored Agent"}</Note>
    );
  } else if (details) {
    body = (
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
    body = <Note>No definition stored for this version</Note>;
  } else {
    const rows = hostedRows(version ?? {});
    body = rows.length ? (
      rows.map((row) => <DefinitionRow key={row.label} row={row} />)
    ) : (
      <Note>No definition stored for this version</Note>
    );
  }

  return (
    <Stack spacing={2}>
      <Card
        title={
          <>
            <span>What we read</span>{" "}
            {loading && (
              <LineSkeleton
                width={28}
                sx={{ display: "inline-block", verticalAlign: "middle" }}
              />
            )}
            {!loading && version && (
              <Box
                component="span"
                sx={{
                  fontFamily: MONO,
                  fontWeight: 600,
                  color: "text.subtitle",
                }}
              >
                {`· ${version.label}`}
              </Box>
            )}
          </>
        }
        action={
          readFrom !== "—" ? (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.subtitle" }}
            >
              {`Read from ${readFrom}`}
            </Typography>
          ) : null
        }
      >
        {body}
      </Card>
      {agent ? (
        <ObservabilityToggle agent={agent} />
      ) : (
        <ObservabilityToggleSkeleton />
      )}
      <ComingSoonCard
        title="File structure"
        description="Browse the files we read from your agent, version by version."
      />
    </Stack>
  );
}
