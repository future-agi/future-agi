import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import { useGatewayContext } from "../context/useGatewayContext";
import GatewayEmptyState from "./GatewayEmptyState";
import GatewayPageFrame from "./frame/GatewayPageFrame";

const GatewayGuardSkeleton = () => (
  <Box p={3} data-testid="gateway-guard-loading">
    <Stack
      direction="row"
      justifyContent="space-between"
      alignItems="center"
      mb={3}
    >
      <Skeleton width={200} height={40} />
      <Skeleton width={120} height={36} variant="rounded" />
    </Stack>
    <Skeleton width="100%" height={48} variant="rounded" sx={{ mb: 2 }} />
    <Stack spacing={1}>
      {[...Array(4)].map((_, i) => (
        <Skeleton key={i} width="100%" height={56} variant="rounded" />
      ))}
    </Stack>
  </Box>
);

const GatewayGuard = ({ children }) => {
  const { gatewayId, isLoading, isRefreshing, refreshGateways, error } =
    useGatewayContext();
  const [isRetrying, setIsRetrying] = useState(false);
  const retryInFlight = useRef(false);
  const retryPending = Boolean(isRefreshing || isRetrying);

  const handleRetry = async () => {
    if (retryInFlight.current || isRefreshing) return;
    retryInFlight.current = true;
    setIsRetrying(true);
    try {
      await refreshGateways();
    } catch {
      // The context owns the read error; never expose a raw rejection here.
    } finally {
      retryInFlight.current = false;
      setIsRetrying(false);
    }
  };

  // A failed background read must not replace a usable cached page.
  if (gatewayId) return children;

  if (isLoading && !isRetrying) {
    return <GatewayGuardSkeleton />;
  }

  return (
    <GatewayPageFrame title="AI Gateway" testId="gateway-guard-empty">
      <Box aria-busy={retryPending}>
        <GatewayEmptyState
          variant="no-gateway"
          title={
            retryPending
              ? "Checking gateway availability"
              : error
                ? undefined
                : "No gateway available"
          }
          description={
            retryPending
              ? "Waiting for the gateway list. Your page will open when a gateway is available."
              : error
                ? undefined
                : "No gateway is available for this organization. Contact your administrator, or retry to check again."
          }
          onAction={handleRetry}
          actionLabel={retryPending ? "Retrying..." : "Retry"}
          actionDisabled={retryPending}
          errorMessage={error ? "The gateway request failed." : undefined}
        />
      </Box>
      {retryPending && (
        <Typography
          role="status"
          typography="s2"
          color="text.secondary"
          sx={{ textAlign: "center" }}
        >
          Checking for an available gateway...
        </Typography>
      )}
    </GatewayPageFrame>
  );
};

GatewayGuard.propTypes = {
  children: PropTypes.node.isRequired,
};

export default GatewayGuard;
