import { Box, LinearProgress, Tab, Tabs } from "@mui/material";
import Section from "src/components/section";
import React, { useMemo } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import PropTypes from "prop-types";
import _ from "lodash";
import { formatDashedToTitleCase } from "src/utils/utils";
import { HeaderComponent } from "../HeaderComponent";

const allTabs = ["connectors", "job-status"];

const SyncView = () => {
  const { pathname } = useLocation();

  const navigate = useNavigate();

  const selectedTab = useMemo(() => {
    return allTabs.reduce((acc, curr) => {
      if (pathname.includes(curr)) {
        acc = curr;
      }
      return acc;
    }, null);
  }, [pathname]);

  const handleChange = (event, newValue) => {
    navigate(`/dashboard/sync/${newValue}`);
  };

  const links = useMemo(() => {
    const l = [{ name: "Sync Data", href: "/dashboard/sync" }];

    const paths = pathname.split("/").filter(Boolean);

    if (paths.length === 4) {
      l.push({
        name: formatDashedToTitleCase(paths[paths.length - 1]),
        href: "/dashboard/sync",
      });
    }
    return l;
  }, [pathname]);

  return (
    <>
      <HeaderComponent routeDepth={3} links={links} />
      <Box sx={{ paddingX: "20px" }}>
        <Section
          variant="panel"
          sx={{
            height: "calc(100vh - 90px)",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Box
            sx={{
              paddingLeft: 2.5,
            }}
          >
            <Tabs
              value={selectedTab}
              onChange={handleChange}
              aria-label="basic tabs example"
              textColor="primary"
            >
              <Tab label="Connectors" value="connectors" />
              <Tab label="Job Status" value="job-status" />
            </Tabs>
          </Box>
          <Outlet />
        </Section>
      </Box>
    </>
  );
};

function CustomTabPanel(props) {
  const { children, value, panelValue, loading, ...other } = props;

  return (
    <div
      style={{ height: "100%" }}
      hidden={panelValue !== value}
      role="tabpanel"
      {...other}
    >
      {loading ? <LinearProgress /> : null}
      {value === panelValue && !loading && children}
    </div>
  );
}

CustomTabPanel.propTypes = {
  children: PropTypes.node,
  panelValue: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  loading: PropTypes.bool,
};

export default SyncView;
