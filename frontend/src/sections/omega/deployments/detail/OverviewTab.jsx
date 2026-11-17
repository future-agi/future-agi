import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import { useDeploymentHealth } from "../api/queries";
import AccessTokenNotice from "../components/AccessTokenNotice";
import CopyField from "../components/CopyField";
import DeployIcon from "../components/DeployIcon";
import ObserveCard from "../components/ObserveCard";
import OpenInStudio from "../components/OpenInStudio";
import { HealthChip, StatusChip } from "../components/StatusChip";
import {
  absoluteTime,
  actionLabel,
  isBusy,
  liveRevisionOf,
  relativeTime,
  shortDigest,
} from "../lib/format";
import { HOST_ONLY_STATUS, hostOnlyNote, isHostOnly } from "../lib/reach";

export function Card({ title, action, children, testId }) {
  return (
    <Box
      data-testid={testId}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 1,
          minHeight: 30,
        }}
      >
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          {title}
        </Typography>
        {action}
      </Box>
      {children}
    </Box>
  );
}

Card.propTypes = {
  title: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
  testId: PropTypes.string,
};

export function Fact({ label, children }) {
  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        gap: 2,
        minWidth: 0,
      }}
    >
      <Typography typography="s2" color="text.secondary" sx={{ flexShrink: 0 }}>
        {label}
      </Typography>
      <Typography
        component="div"
        typography="s2"
        color="text.primary"
        sx={{ textAlign: "right", minWidth: 0, overflowWrap: "anywhere" }}
      >
        {children}
      </Typography>
    </Box>
  );
}

Fact.propTypes = { label: PropTypes.node, children: PropTypes.node };

function HealthCard({ deployment }) {
  const health = useDeploymentHealth(
    deployment.id,
    Boolean(deployment.liveRevision),
  );
  const probe = health.data?.health ?? deployment.health;
  return (
    <Card
      title="Health"
      testId="health-card"
      action={
        deployment.liveRevision ? (
          <Button
            size="small"
            color="inherit"
            onClick={() => health.refetch()}
            disabled={health.isFetching}
            startIcon={<DeployIcon name="refresh" size={14} />}
          >
            Check
          </Button>
        ) : null
      }
    >
      {probe ? (
        <>
          <Box>
            <HealthChip status={probe.status} />
          </Box>
          <Fact label="Endpoint">
            GET /health{probe.httpStatus ? ` → ${probe.httpStatus}` : ""}
          </Fact>
          {probe.revision && (
            <Fact label="Answered as">Revision {probe.revision}</Fact>
          )}
          <Fact label="Checked">{relativeTime(probe.checkedAt)}</Fact>
        </>
      ) : (
        <Typography typography="s2" color="text.secondary">
          Nothing is serving yet, so there is nothing to check.
        </Typography>
      )}
    </Card>
  );
}

HealthCard.propTypes = { deployment: PropTypes.object.isRequired };

export default function OverviewTab({ deployment }) {
  const live = liveRevisionOf(deployment);
  const operation = deployment.operation;
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <AccessTokenNotice id={deployment.id} />
      <Card title="Endpoint" testId="endpoint-card">
        {deployment.url ? (
          <>
            <CopyField
              value={deployment.url}
              label="deployment URL"
              testId="overview-url"
            />
            <Typography typography="s2" color="text.secondary">
              Serves the project&apos;s Studio API at /studio/v1 and its health
              at /health. Calls need the access token.
            </Typography>
            {isHostOnly(deployment) && (
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="endpoint-reach"
                data-reach="deployment-host"
              >
                {HOST_ONLY_STATUS}: {hostOnlyNote(deployment)}
              </Typography>
            )}
            <OpenInStudio deployment={deployment} />
          </>
        ) : (
          <Typography typography="s2" color="text.secondary">
            The URL appears once a revision is live.
          </Typography>
        )}
      </Card>
      <ObserveCard deployment={deployment} />
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: "repeat(3, minmax(0, 1fr))" },
          gap: 2,
        }}
      >
        <Card title="Live revision" testId="live-revision-card">
          {live ? (
            <>
              <Fact label="Revision">{live.revision}</Fact>
              <Fact label="Live since">
                {absoluteTime(live.liveAt || live.createdAt)}
              </Fact>
              <Fact label="Bundle">
                {live.bundle.files} files ·{" "}
                <Box component="span" sx={{ fontFamily: "monospace" }}>
                  {shortDigest(live.bundle.sha256)}
                </Box>
              </Fact>
              <Fact label="Studio page">{live.studio ? "Served" : "Off"}</Fact>
            </>
          ) : (
            <Typography typography="s2" color="text.secondary">
              No revision is live.
            </Typography>
          )}
        </Card>
        <HealthCard deployment={deployment} />
        <Card title="Latest operation" testId="operation-card">
          {operation ? (
            <>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <StatusChip
                  status={operation.status}
                  testId="operation-status"
                />
                <Typography typography="s2" color="text.secondary">
                  {actionLabel(operation.action)} · revision{" "}
                  {operation.revision}
                </Typography>
              </Box>
              {operation.message && (
                <Typography
                  typography="s2"
                  color={
                    operation.status === "failed"
                      ? "error.main"
                      : "text.secondary"
                  }
                >
                  {operation.message}
                </Typography>
              )}
              <Fact label={isBusy(operation.status) ? "Started" : "Finished"}>
                {relativeTime(operation.endedAt || operation.startedAt)}
              </Fact>
            </>
          ) : (
            <Typography typography="s2" color="text.secondary">
              No operation yet.
            </Typography>
          )}
        </Card>
      </Box>
    </Box>
  );
}

OverviewTab.propTypes = { deployment: PropTypes.object.isRequired };
