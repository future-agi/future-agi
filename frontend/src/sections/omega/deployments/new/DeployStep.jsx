import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";

import { useDeployment } from "../api/queries";
import AccessTokenNotice from "../components/AccessTokenNotice";
import CopyField from "../components/CopyField";
import DeployIcon from "../components/DeployIcon";
import { errorCopy } from "../components/DeploymentErrorState";
import LogViewer from "../components/LogViewer";
import OpenInStudio from "../components/OpenInStudio";
import { isHostOnly } from "../lib/reach";
import { formatBytes, PROJECT_MANIFEST } from "../lib/bundle";
import { isBusy, shortDigest } from "../lib/format";
import { deploymentPath } from "../lib/links";
import { useLogStream } from "../lib/useLogStream";
import DeployProgress from "./DeployProgress";

function Row({ label, children }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "160px 1fr" },
        gap: { xs: 0.25, sm: 2 },
        py: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        "&:last-of-type": { borderBottom: 0 },
      }}
    >
      <Typography typography="s2" color="text.secondary">
        {label}
      </Typography>
      <Box sx={{ minWidth: 0, typography: "s1", overflowWrap: "anywhere" }}>
        {children}
      </Box>
    </Box>
  );
}

Row.propTypes = { label: PropTypes.node, children: PropTypes.node };

const mono = { fontFamily: "monospace", fontSize: 13 };

/** What will be sent, before it is sent. */
export function DeployReview({ bundle, settings, env }) {
  const names = Object.keys(env);
  return (
    <Box
      data-testid="deploy-review"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 2,
      }}
    >
      <Row label="Name">
        <Box component="span" sx={mono}>
          {settings.name}
        </Box>
      </Row>
      <Row label="Project">
        {bundle.manifest.name || bundle.manifest.file || PROJECT_MANIFEST} ·{" "}
        {bundle.files.length} files · {formatBytes(bundle.size)} ·{" "}
        <Box component="span" sx={mono}>
          {shortDigest(bundle.sha256)}
        </Box>
      </Row>
      <Row label="Variables">
        {names.length ? (
          <Box component="span" sx={mono}>
            {names.join(", ")}
          </Box>
        ) : (
          "None"
        )}
      </Row>
      <Row label="Secrets">
        {settings.secretRefs.length ? (
          <Box component="span" sx={mono}>
            {settings.secretRefs.join(", ")}
          </Box>
        ) : (
          "None"
        )}
      </Row>
      <Row label="Studio page">{settings.studio ? "Served" : "Off"}</Row>
    </Box>
  );
}

DeployReview.propTypes = {
  bundle: PropTypes.object.isRequired,
  settings: PropTypes.object.isRequired,
  env: PropTypes.object.isRequired,
};

/** The revision this wizard started, as EE reports it now. */
function trackedStatus(detail, revision) {
  if (!detail) return { status: "queued", message: null };
  if (
    detail.operation?.revision === revision &&
    detail.operation.action === "deploy"
  ) {
    return {
      status: detail.operation.status,
      message: detail.operation.message,
    };
  }
  const record = (detail.revisions || []).find((r) => r.revision === revision);
  if (!record) return { status: detail.status, message: detail.message };
  return {
    status: record.status === "superseded" ? "live" : record.status,
    message: record.message,
  };
}

/** Live progress of one accepted deploy: status line, build log, then the result. */
export function DeployTracker({ id, revision, onEdit }) {
  const navigate = useNavigate();
  const query = useDeployment(id);
  const detail = query.data;
  const { status, message } = trackedStatus(detail, revision);
  const log = useLogStream(id, { source: "build", revision });
  const live = status === "live";
  const failed = status === "failed";

  return (
    <Box
      data-testid="deploy-tracker"
      data-status={status}
      sx={{ display: "flex", flexDirection: "column", gap: 2 }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "baseline",
          gap: 1,
          flexWrap: "wrap",
        }}
      >
        <Typography typography="m3" fontWeight="fontWeightMedium" role="status">
          {live
            ? isHostOnly(detail)
              ? `${detail?.name} is running on this host`
              : `${detail?.name} is live`
            : failed
              ? `Revision ${revision} did not go live`
              : `Deploying ${detail?.name ?? ""} · revision ${revision}`}
        </Typography>
      </Box>
      <DeployProgress
        status={status}
        message={isBusy(status) || failed ? message : null}
        hostOnly={isHostOnly(detail)}
      />
      {live && detail?.url && (
        <Box
          data-testid="deploy-result"
          sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
        >
          <CopyField
            value={detail.url}
            label="deployment URL"
            testId="deploy-url"
          />
          <AccessTokenNotice id={id} />
          <Box
            sx={{
              display: "flex",
              gap: 1.5,
              flexWrap: "wrap",
              alignItems: "flex-start",
            }}
          >
            <Button
              variant="contained"
              size="small"
              onClick={() => navigate(deploymentPath(id))}
              data-testid="view-deployment"
            >
              View deployment
            </Button>
            {/* The access token is shown just above (AccessTokenNotice). */}
            <OpenInStudio deployment={detail} withHint={false} tokenOnScreen />
          </Box>
        </Box>
      )}
      {failed && (
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onEdit}
          >
            Back to settings
          </Button>
          <Button
            size="small"
            color="inherit"
            onClick={() => navigate(deploymentPath(id, "logs"))}
          >
            Open logs
          </Button>
        </Box>
      )}
      <Box>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          sx={{ mb: 1 }}
        >
          Build log
        </Typography>
        <Typography typography="s2" color="text.secondary" sx={{ mb: 1 }}>
          Build setup details are hidden so the useful steps stay easy to scan.
        </Typography>
        <LogViewer
          stream={log}
          height={{ xs: 260, sm: 320 }}
          source="build"
          testId="build-log"
        />
      </Box>
    </Box>
  );
}

DeployTracker.propTypes = {
  id: PropTypes.string.isRequired,
  revision: PropTypes.number.isRequired,
  onEdit: PropTypes.func.isRequired,
};

export function CreateError({ error }) {
  if (!error) return null;
  const copy = errorCopy(error);
  const ownTitle = copy.title !== "Something went wrong";
  return (
    <Alert severity="error" variant="outlined" data-testid="deploy-error">
      {ownTitle && <strong>{copy.title}. </strong>}
      {copy.description || error.message}
    </Alert>
  );
}

CreateError.propTypes = { error: PropTypes.object };

export function DeployButton({ onClick, pending, disabled }) {
  return (
    <Button
      variant="contained"
      color="primary"
      onClick={onClick}
      disabled={disabled || pending}
      startIcon={<DeployIcon name="rocket" size={16} />}
      data-testid="deploy-submit"
    >
      {pending ? "Sending…" : "Deploy"}
    </Button>
  );
}

DeployButton.propTypes = {
  onClick: PropTypes.func.isRequired,
  pending: PropTypes.bool,
  disabled: PropTypes.bool,
};
