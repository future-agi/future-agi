import React, { useCallback } from "react";
import { Box, Tab, Tabs } from "@mui/material";
import { useNavigate, useParams } from "react-router-dom";
import { paths } from "src/routes/paths";
import GatewayEmptyState from "../components/GatewayEmptyState";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";

const TABS = [
  {
    slug: "credits",
    label: "Credits",
    description:
      "The managed-credit balance, top-ups and low-balance settings.",
  },
  {
    slug: "history",
    label: "History",
    description:
      "Every grant, charge, refund and adjustment, with a CSV export and monthly statements.",
  },
  {
    slug: "usage",
    label: "Usage",
    description:
      "Spend split between managed credits and your own provider keys.",
  },
];

const BillingSection = () => {
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();
  const tabIndex = Math.max(
    TABS.findIndex((tab) => tab.slug === tabSlug),
    0,
  );

  const handleTabChange = useCallback(
    (_, index) =>
      navigate(paths.dashboard.gateway.billing[TABS[index].slug], {
        replace: true,
      }),
    [navigate],
  );

  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.billing}
        title="Billing & Credits"
        subtitle="Managed-credit balance, top-ups, history and usage"
      />
      <Tabs
        value={tabIndex}
        onChange={handleTabChange}
        sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}
      >
        {TABS.map((tab) => (
          <Tab key={tab.slug} label={tab.label} />
        ))}
      </Tabs>
      <GatewayEmptyState
        title="Not in this release"
        description={TABS[tabIndex].description}
      />
    </Box>
  );
};

export default BillingSection;
