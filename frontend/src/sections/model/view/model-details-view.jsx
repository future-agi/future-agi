import {
  Button,
  Container,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import React, { useCallback, useState } from "react";
import { useParams } from "react-router";
import { useGetModel } from "src/api/model/info";
import EmptyContent from "src/components/empty-content";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import AnnotationTaskView from "../annotate/view/annotation-task-view";
import JourneyView from "../journey/view/journey-view";
import PerformanceView from "../performance/view/performance-view";
import ModelOverview from "../model-overview";
import ModelConfigView from "../model-config-view";

// ----------------------------------------------------------------------

const TABS = [
  {
    value: "overview",
    label: "Overview",
    icon: <Iconify icon="solar:user-id-bold" width={24} />,
  },
  {
    value: "performance",
    label: "Model Performance",
    icon: <Iconify icon="solar:bill-list-bold" width={24} />,
  },
  {
    value: "journey",
    label: "Prediction Journey",
    icon: <Iconify icon="solar:bill-list-bold" width={24} />,
  },
  {
    value: "alerts",
    label: "Alerts",
    icon: <Iconify icon="solar:bill-list-bold" width={24} />,
  },
  {
    value: "dashboards",
    label: "Dashboards",
    icon: <Iconify icon="solar:bill-list-bold" width={24} />,
  },
  // {
  //   value: 'customMetrics',
  //   label: 'Custom Metrics',
  //   icon: <Iconify icon="solar:share-bold" width={24} />,
  // },
  {
    value: "annotate",
    label: "Annotate",
    icon: <Iconify icon="solar:share-bold" width={24} />,
  },
  {
    value: "config",
    label: "Config",
    icon: <Iconify icon="solar:key-linear" width={24} />,
  },
];

// ----------------------------------------------------------------------

export default function ProductDetailsView() {
  const params = useParams();
  const { id } = params;
  const [currentTab, setCurrentTab] = useState("overview");
  const { model, modelError } = useGetModel(id);

  const handleChangeTab = useCallback((event, newValue) => {
    setCurrentTab(newValue);
  }, []);

  const renderError = (
    <EmptyContent
      filled
      title={`${modelError?.detail}`}
      action={
        <Button
          component={RouterLink}
          href={paths.dashboard.models.root}
          startIcon={<Iconify icon="solar:alt-arrow-left-linear" width={16} />}
          sx={{ mt: 3 }}
        >
          Back to List
        </Button>
      }
      sx={{ py: 10 }}
    />
  );

  const renderModel = model && (
    <>
      {/* <Tabs
        value={currentTab}
        onChange={handleChangeTab}
        sx={{
          mb: { xs: 3, md: 5 },
        }}
      >
        {TABS.map((tab) => (
          <Tab key={tab.value} label={tab.label} icon={tab.icon} value={tab.value} />
        ))}
      </Tabs> */}

      <ToggleButtonGroup
        value={currentTab}
        exclusive
        onChange={handleChangeTab}
        aria-label="text alignment"
        size="small"
      >
        {TABS.map((tab) => (
          <ToggleButton
            key={tab.value}
            value={tab.value}
            aria-label={tab.label}
          >
            {tab.icon}
            {tab.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>

      {currentTab === "overview" && <ModelOverview model={model} />}
      {currentTab === "journey" && <JourneyView modelId={id} />}
      {currentTab === "annotate" && <AnnotationTaskView />}
      {currentTab === "performance" && <PerformanceView model={model} />}
      {currentTab === "config" && <ModelConfigView model={model} />}
    </>
  );

  return (
    // <Container maxWidth={settings.themeStretch ? false : 'lg'}>
    <Container maxWidth={false}>
      {modelError ? renderError : null}
      {model ? renderModel : null}
    </Container>
  );
}
