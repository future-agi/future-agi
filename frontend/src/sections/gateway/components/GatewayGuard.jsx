import React from "react";
import PropTypes from "prop-types";
import { LoadingScreen } from "src/components/loading-screen";
import { useGatewayContext } from "../context/useGatewayContext";
import GatewayEmptyState from "./GatewayEmptyState";

const GatewayGuard = ({ children }) => {
  const { gatewayId, isLoading, refreshGateways } = useGatewayContext();

  if (isLoading) {
    return <LoadingScreen />;
  }

  if (!gatewayId) {
    return (
      <GatewayEmptyState variant="no-gateway" onAction={refreshGateways} />
    );
  }

  return children;
};

GatewayGuard.propTypes = {
  children: PropTypes.node.isRequired,
};

export default GatewayGuard;
