import React from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Tab from "@mui/material/Tab";
import { useNavigate, useParams } from "react-router-dom";

import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";

import { GatewayPageFrame } from "../components/frame";
import { useGatewayContext } from "../context/useGatewayContext";
import { GUARDRAILS_OFF_BANNER } from "../utils/guardrailPipeline";
import FeedbackSummaryCard from "./FeedbackSummaryCard";
import GuardrailAnalyticsTab from "./GuardrailAnalyticsTab";
import TriggeredRequestsTab from "./logs/TriggeredRequestsTab";
import RulesView from "./rules/RulesView";
import GuardrailTestTab from "./test/GuardrailTestTab";
import { useGuardrailsOff } from "./hooks/useGuardrailsOff";

const gr = paths.dashboard.gateway.guardrails;

// One page, five views of the org's guardrails. The old Overview and Rules
// tabs edited the same rules two ways; both URLs now open the one Rules view.
const TABS = [
  {
    id: "rules",
    label: "Rules",
    path: gr.root,
    slugs: [undefined, "overview", "configuration"],
    Content: RulesView,
  },
  {
    id: "analytics",
    label: "Analytics",
    path: gr.analytics,
    slugs: ["analytics"],
    Content: GuardrailAnalyticsTab,
  },
  {
    id: "feedback",
    label: "Feedback",
    path: gr.feedback,
    slugs: ["feedback"],
    Content: FeedbackSummaryCard,
  },
  {
    id: "test",
    label: "Test",
    path: gr.playground,
    slugs: ["playground"],
    Content: GuardrailTestTab,
  },
  {
    id: "logs",
    label: "Logs",
    path: gr.logs,
    slugs: ["logs"],
    Content: TriggeredRequestsTab,
  },
];

const GuardrailManagementSection = () => {
  const { tab: slug } = useParams();
  const navigate = useNavigate();
  const { gatewayId, gateway } = useGatewayContext();
  const current = TABS.find((tab) => tab.slugs.includes(slug)) || TABS[0];
  const { Content } = current;
  // Every view says so first while the gateway runs no check.
  const off = useGuardrailsOff(gatewayId);

  return (
    <GatewayPageFrame
      title="Guardrails"
      description="Checks on the requests through this gateway, before the model is called, and on the model's answer."
    >
      <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
        <CustomTabs
          value={current.id}
          onChange={(_, id) =>
            navigate(TABS.find((tab) => tab.id === id).path, { replace: true })
          }
          variant="scrollable"
          scrollButtons={false}
          aria-label="Guardrails views"
        >
          {TABS.map((tab) => (
            <Tab key={tab.id} value={tab.id} label={tab.label} />
          ))}
        </CustomTabs>
      </Box>
      {off && (
        <Alert severity="warning" data-testid="guardrails-off-banner">
          {GUARDRAILS_OFF_BANNER}
        </Alert>
      )}
      {current.id === "test" ? (
        <Content
          gatewayId={gatewayId}
          delegatedPlaygroundAvailable={gateway?.delegatedPlaygroundAvailable}
        />
      ) : (
        <Content gatewayId={gatewayId} />
      )}
    </GatewayPageFrame>
  );
};

export default GuardrailManagementSection;
