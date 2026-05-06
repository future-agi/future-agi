import { Box, Card, LinearProgress, Tab, Tabs, useTheme } from "@mui/material";
import React, { useContext, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Outlet, useLocation, useNavigate, useParams } from "react-router-dom";
import { HeaderComponent } from "src/sections/HeaderComponent";
import { useGetMetricOptions } from "../../../api/model/metric";
import { DatasetContext } from "./DatasetContext";
import Label from "src/components/label";
import { useGetModelDetail } from "src/api/model/model";
import { useGetOptimization } from "src/api/model/optimize";

function CustomTabPanel(props) {
  const { children, value, panelValue, loading, ...other } = props;

  return (
    <div hidden={panelValue !== value} role="tabpanel" {...other}>
      {loading && <LinearProgress />}
      {value === panelValue && !loading && <Box>{children}</Box>}
    </div>
  );
}

CustomTabPanel.propTypes = {
  children: PropTypes.node,
  panelValue: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  loading: PropTypes.bool,
};

const allTabs = [
  "performance",
  "custom-metrics",
  "datasets",
  "optimize",
  "config",
  "report",
];

const ModelDetail = () => {
  const { pathname } = useLocation();

  const selectedTab = useMemo(() => {
    return allTabs.reduce((acc, curr) => {
      if (pathname.includes(curr)) acc = curr;
      return acc;
    }, null);
  }, [pathname]);

  const { id } = useParams();

  const navigate = useNavigate();

  const handleChange = (event, newValue) => {
    setSelectedOptimization(null);
    setDatasetSelectMode(false);
    //@ts-ignore
    navigate(`/dashboard/models/${id}/${newValue}`);
  };

  const [, setSelectedOptimization] = useState(null);

  const { state } = useLocation();

  const { isPending: isLoadingDatasetOptions } = useGetMetricOptions(id);

  const {
    data: modelDetails,
    isPending: isLoadingModelDetails,
    error,
  } = useGetModelDetail(id);

  const optimizationId = useMemo(() => {
    const paths = pathname.split("/").filter(Boolean);

    if (paths.includes("optimize") && paths.length === 5) {
      return paths[paths.length - 1];
    }
    return null;
  }, [pathname]);

  const { data: optimizeData, isLoading: isLoadingOptimization } =
    useGetOptimization(id, optimizationId, {
      enabled: Boolean(optimizationId),
    });

  const loadingModelDetails =
    isLoadingDatasetOptions || isLoadingModelDetails || isLoadingOptimization;

  const theme = useTheme();

  useEffect(() => {
    if (error) {
      navigate("/dashboard/models");
    }
  }, [error]);

  const links = useMemo(() => {
    const l = [
      {
        name: "Model",
        href: "/dashboard/models",
      },
      {
        name:
          state?.model?.userModelId || modelDetails?.userModelId || "New Model",
        href: `/dashboard/models/${id}/performance`,
      },
    ];

    const trailingRoute = pathname.split("/").splice(5);

    const data = optimizeData?.name;

    trailingRoute.forEach((r) => {
      l.push({
        name: state?.pathLabel || data || r,
        href: pathname,
      });
    });

    return l;
  }, [state, modelDetails, id, pathname, optimizeData]);

  const { setDatasetSelectMode } = useContext(DatasetContext);

  function renderOutlet(condition) {
    if (condition) return <></>;

    return loadingModelDetails ? <LinearProgress /> : <Outlet />;
  }

  return (
    <>
      <HeaderComponent links={links} routeDepth={2} />
      <Box
        sx={{
          paddingX: "20px",
        }}
      >
        <Card
          sx={{
            width: "100%",
            minHeight: selectedTab !== "config" ? "calc(100vh - 90px)" : "",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Box
            sx={{ borderBottom: 1, borderColor: "divider", paddingLeft: 2.5 }}
          >
            <Tabs
              value={selectedTab}
              onChange={handleChange}
              aria-label="basic tabs example"
              textColor="primary"
              TabIndicatorProps={{
                style: {
                  backgroundColor: theme.palette.primary.main,
                },
              }}
            >
              <Tab label="Performance" value="performance" />
              <Tab label="Custom Metrics" value="custom-metrics" />
              <Tab
                label="Datasets"
                value="datasets"
                onClick={() => {
                  navigate(`/dashboard/models/${id}/datasets`);
                }}
              />
              {modelDetails?.modelType === "GenerativeLLM" && (
                <Tab
                  label={
                    <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
                      Optimize<Label color="primary">Alpha</Label>
                    </Box>
                  }
                  value="optimize"
                />
              )}
              <Tab label="Report" value="report" />
              <Tab label="Config" value="config" />
            </Tabs>
          </Box>
          {renderOutlet(selectedTab == "config")}
        </Card>
      </Box>
      {renderOutlet(selectedTab !== "config")}
    </>
  );
};

export default ModelDetail;
