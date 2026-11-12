import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Helmet } from "react-helmet-async";
import { Navigate } from "react-router-dom";

import { apiPath } from "src/api/contracts/api-surface";
import { LoadingScreen } from "src/components/loading-screen";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import axios from "src/utils/axios";

import DeploymentPreviewNotice from "../deployments/components/DeploymentPreviewNotice";
import { OmegaIcon } from "./icons";
import {
  EARLY_ACCESS_KEY,
  hasRequestable,
  useEarlyAccess,
} from "./useEarlyAccess";

// Settings → Early access (READINESS 3.13, RC-05): the Omega products this
// organization may ask for, and where each request stands. Owners and admins
// ask; Future AGI staff invite in batches. A product still internal is never
// listed here. The server decides every row; this page only shows it.
//
// RC-01: an org with nothing to ask for (nothing listed, or every listed
// product already admitted) gets Future AGI's standard 404, exactly like an
// unknown URL, so the page is never a waitlist with nothing on it (CP:D12,
// AUDIT R1). While loading it shows the host's loading screen, never the copy.

const ICONS = {
  console: "console",
  sandboxes: "sandboxes",
  deployments: "deployments",
  studio_tab: "studio",
  hub: "hub",
};

const DESCRIPTIONS = {
  console: "Hosted agents for your team, with approvals and runs.",
  sandboxes: "Isolated environments your agents can run code in.",
  deployments: "Ship an agent as an endpoint, with logs and rollbacks.",
  studio_tab: "Your local Studio, opened inside Future AGI.",
  // What the Hub's own page says: no install claim while nothing can install
  // an entry (hub/lib/installs.js; no-install-copy.test.js).
  hub: "Agents and skills for Omega, each version approved by a Future AGI maintainer.",
};

function statusOf(row) {
  if (row.admitted && row.state === "paused") {
    return { label: "Paused", color: "warning" };
  }
  if (row.admitted) return { label: "Enabled", color: "success" };
  if (row.request_status === "requested") {
    return { label: "Requested", color: "info" };
  }
  if (row.request_status === "declined") {
    return { label: "Declined", color: "default" };
  }
  return null;
}

function ProductRow({ row, canRequest, onRequest, pending }) {
  const status = statusOf(row);
  const askable = !row.admitted && !row.request_status;
  return (
    <Box
      data-testid={`early-access-row-${row.product}`}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1.5,
        px: 2,
        py: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
      }}
    >
      <OmegaIcon name={ICONS[row.product]} size={20} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography typography="s1" fontWeight={500} color="text.primary">
          {row.label}
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {DESCRIPTIONS[row.product]}
        </Typography>
        {row.product === "deployments" && <DeploymentPreviewNotice />}
      </Box>
      {status && (
        <Chip
          size="small"
          label={status.label}
          color={status.color}
          variant="outlined"
        />
      )}
      {askable && canRequest && (
        <LoadingButton
          size="small"
          variant="outlined"
          loading={pending}
          onClick={() => onRequest(row.product)}
          data-testid={`early-access-request-${row.product}`}
        >
          Request access
        </LoadingButton>
      )}
    </Box>
  );
}

ProductRow.propTypes = {
  row: PropTypes.shape({
    product: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
    admitted: PropTypes.bool.isRequired,
    state: PropTypes.string,
    request_status: PropTypes.string,
  }).isRequired,
  canRequest: PropTypes.bool.isRequired,
  onRequest: PropTypes.func.isRequired,
  pending: PropTypes.bool.isRequired,
};

export default function EarlyAccessPage() {
  const queryClient = useQueryClient();
  const query = useEarlyAccess();
  const request = useMutation({
    mutationFn: (product) =>
      axios.post(apiPath("/omega-rollout/early-access/"), { product }),
    onSuccess: (res) => {
      queryClient.setQueryData(EARLY_ACCESS_KEY, res);
      queryClient.invalidateQueries({ queryKey: CAPABILITIES_QUERY_KEY });
    },
  });
  const data = query.data;

  if (query.isLoading) return <LoadingScreen sx={undefined} />;
  if (query.isError) {
    return (
      <Box sx={{ p: 2, maxWidth: 760 }} data-testid="early-access-page">
        <Alert
          severity="error"
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => query.refetch()}
            >
              Try again
            </Button>
          }
        >
          Early access couldn&apos;t be loaded. Try again in a few seconds.
        </Alert>
      </Box>
    );
  }
  if (!hasRequestable(data)) return <Navigate to="/404" replace />;

  return (
    <Box sx={{ p: 2, maxWidth: 760 }} data-testid="early-access-page">
      <Helmet>
        <title>Early access</title>
      </Helmet>
      <Box sx={{ mb: 2.5 }}>
        <Typography
          typography="m3"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          Early access
        </Typography>
        <Typography typography="s1" color="text.secondary">
          New Omega products open to organizations in stages. Ask for access and
          check here for its status.
        </Typography>
      </Box>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {!data.can_request && (
          <Typography typography="s2" color="text.secondary">
            Only organization owners and admins can ask for access.
          </Typography>
        )}
        {request.isError && (
          <Alert severity="error">
            The request could not be confirmed. Try again in a few seconds.
          </Alert>
        )}
        {data.products.map((row) => (
          <ProductRow
            key={row.product}
            row={row}
            canRequest={data.can_request}
            onRequest={(product) => request.mutate(product)}
            pending={request.isPending && request.variables === row.product}
          />
        ))}
      </Box>
    </Box>
  );
}
