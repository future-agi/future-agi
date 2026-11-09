import React from "react";
import PropTypes from "prop-types";
import { useOrganization } from "src/contexts/OrganizationContext";
import GatewayEmptyState from "../../components/GatewayEmptyState";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { isMcpTabHidden } from "../../shell/page-status";
import RevisionReview from "./RevisionReview";

// This is the EXISTING release switch, shared by the tab bar and deep links.
// Keep HIDDEN_MCP_TABS.trust until the owner's release review enables G13b.
export default function TrustTab({ gatewayId }) {
  const { currentOrganizationId } = useOrganization();
  const { canEdit } = useGatewayAdmin();
  if (isMcpTabHidden("trust"))
    return (
      <GatewayEmptyState
        title="Not available on this gateway"
        description="This gateway does not review changes to a server's tools."
      />
    );
  if (!currentOrganizationId || !gatewayId) return null;
  return (
    <RevisionReview
      key={`${currentOrganizationId}:${gatewayId}:${canEdit}`}
      organizationId={currentOrganizationId}
      gatewayId={gatewayId}
      canEdit={canEdit}
    />
  );
}
TrustTab.propTypes = { gatewayId: PropTypes.string };
