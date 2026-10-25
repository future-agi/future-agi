import React, { useCallback } from "react";
import { Box, Tab, Tabs } from "@mui/material";
import { useNavigate, useParams } from "react-router-dom";
import { paths } from "src/routes/paths";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import AccessTab from "./access/AccessTab";
import DataTab from "./data/DataTab";
import PresetsTab from "./presets/PresetsTab";
import VersionsTab from "./versions/VersionsTab";
import RolloutTab from "./rollout/RolloutTab";

const TABS = [
  { slug: "access", label: "Access", Content: AccessTab },
  { slug: "data", label: "Data", Content: DataTab },
  { slug: "presets", label: "Presets", Content: PresetsTab },
  { slug: "versions", label: "Versions", Content: VersionsTab },
  { slug: "rollout", label: "Rollout", Content: RolloutTab },
];

const PoliciesSection = () => {
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();
  const tabIndex = Math.max(
    TABS.findIndex((tab) => tab.slug === tabSlug),
    0,
  );
  const { Content } = TABS[tabIndex];

  const handleTabChange = useCallback(
    (_, index) =>
      navigate(paths.dashboard.gateway.policies[TABS[index].slug], {
        replace: true,
      }),
    [navigate],
  );

  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.policies}
        title="Policies"
        subtitle="Who can call which models, what data may leave, and how policy changes roll out"
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
      <Content />
    </Box>
  );
};

export default PoliciesSection;
