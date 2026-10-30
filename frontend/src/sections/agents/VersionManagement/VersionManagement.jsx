import { Box, Button, Divider, Typography, useTheme } from "@mui/material";
import React, { useEffect, useState } from "react";
import Iconify from "src/components/iconify";
import AddNewVersionDrawer from "./AddNewVersionDrawer";
import PropTypes from "prop-types";
import { useNavigate } from "react-router";
import { agentDetailPath } from "src/sections/agent-playground/connected/detail/agentDetailTabs";
import { useAgentDetailsStore } from "../store/agentDetailsStore";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import VersionList from "src/components/VersionList/VersionList";

const VersionManagement = ({
  agentId,
  connected = false,
  versions,
  fetchNextVersions,
  hasNextVersions,
  isFetchingNextVersions,
}) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const { selectedVersion, setSelectedVersion, setLatestVersionNumber } =
    useAgentDetailsStore();

  const scrollContainerRef = useScrollEnd(() => {
    if (hasNextVersions && !isFetchingNextVersions) {
      fetchNextVersions();
    }
  }, [hasNextVersions, isFetchingNextVersions, fetchNextVersions]);

  const latestVersionNumber = versions?.length
    ? Math.max(...versions.map((v) => v.version_number))
    : 0;

  useEffect(() => {
    setLatestVersionNumber(latestVersionNumber);
  }, [latestVersionNumber, setLatestVersionNumber]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(1),
          p: theme.spacing(1.5),
          px: theme.spacing(2.5),
        }}
      >
        <Typography
          typography="s1"
          color="text.primary"
          fontWeight={"fontWeightSemiBold"}
        >
          Version Management
        </Typography>

        <Button
          variant="contained"
          size="small"
          fullWidth
          sx={{
            px: "24px",
            borderRadius: "4px",
            height: "30px",
            backgroundColor: "primary.main",
            "&:hover": { backgroundColor: "primary.main" },
            textTransform: "none",
          }}
          startIcon={
            <Iconify
              icon={
                connected ? "solar:arrow-right-up-linear" : "octicon:plus-24"
              }
              color="background.paper"
              sx={{ width: 20, height: 20 }}
            />
          }
          onClick={() =>
            connected
              ? navigate(agentDetailPath(agentId, "versions"))
              : setDrawerOpen(true)
          }
        >
          <Typography
            typography="s2"
            fontWeight="fontWeightMedium"
            color="background.paper"
          >
            {connected
              ? "Manage versions on the agent page"
              : "Create new version"}
          </Typography>
        </Button>
      </Box>
      <Divider sx={{ borderColor: "divider" }} />

      {/* Scrollable Section */}
      <VersionList
        versions={versions}
        selectedVersion={selectedVersion}
        onVersionChange={setSelectedVersion}
        isFetchingNextVersions={isFetchingNextVersions}
        scrollContainerRef={scrollContainerRef}
      />

      <AddNewVersionDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        latestVersion={latestVersionNumber}
      />
    </Box>
  );
};

VersionManagement.propTypes = {
  agentId: PropTypes.string,
  connected: PropTypes.bool,
  versions: PropTypes.array,
  fetchNextVersions: PropTypes.func,
  hasNextVersions: PropTypes.bool,
  isFetchingNextVersions: PropTypes.bool,
};

export default VersionManagement;
