import React from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack } from "@mui/material";
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
  const { gatewayId, isLoading, refreshGateways, error } = useGatewayContext();

  if (isLoading) {
    return <GatewayGuardSkeleton />;
  }

  if (!gatewayId) {
    return (
      <GatewayPageFrame title="AI Gateway" testId="gateway-guard-empty">
        <GatewayEmptyState
          variant="no-gateway"
          onAction={refreshGateways}
          errorMessage={error ? "The gateway request failed." : undefined}
        />
      </GatewayPageFrame>
    );
  }

  return children;
};

GatewayGuard.propTypes = {
  children: PropTypes.node.isRequired,
};

export default GatewayGuard;
