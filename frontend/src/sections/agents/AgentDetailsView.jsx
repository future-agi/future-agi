import { Box, Button, Stack, useTheme } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import VersionDetails from "src/sections/agents/VersionDetails/VersionDetails";
import VersionManagement from "src/sections/agents/VersionManagement/VersionManagement";
import { useLocation, useNavigate, useParams } from "react-router";
import VersionsListSkeleton from "./skeletons/VersionsListSkeleton";
import { AgentDropdown } from "./VersionDetails/AgentDropdown";
import Iconify from "src/components/iconify";
import { useAgentDetailsStore } from "./store/agentDetailsStore";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import { useAgentDetail } from "src/api/agents/agentDetail";
import SvgColor from "src/components/svg-color";
import CreateAgentScenarioHelp from "./CreateAgentScenarioHelp";

const AgentDetailsView = () => {
  const { agentDefinitionId } = useParams();
  const navigate = useNavigate();
  const { pathname, state } = useLocation();
  const theme = useTheme();
  const { selectedVersion, setSelectedVersion } = useAgentDetailsStore();
  const [openScenarioHelp, setOpenScenarioHelp] = useState(false);
  const isNewAgentRef = useRef(state?.newAgent);

  const [, setUrlVersion] = useUrlState("version", "");

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } =
    useAgentDefinitionVersions({ selectedAgentId: agentDefinitionId });
  const agent = useAgentDetail(agentDefinitionId);

  // To access the flattened results:
  const versions = useMemo(
    () => data?.pages?.flatMap((page) => page?.results) ?? [],
    [data],
  );

  useEffect(() => {
    if (selectedVersion) {
      setUrlVersion(selectedVersion);
    }
  }, [selectedVersion, setUrlVersion]);

  useEffect(() => {
    if (versions?.length && !selectedVersion) {
      const latest = versions.find((v) => v.is_latest);
      if (latest) {
        setSelectedVersion(latest.id);
      }
    }
  }, [versions, selectedVersion, setSelectedVersion]);

  useEffect(() => {
    if (isNewAgentRef.current) {
      const timer = setTimeout(() => {
        setOpenScenarioHelp(true);
      }, 4000);

      return () => clearTimeout(timer);
    }
  }, []);

  const handleBack = () => {
    const pathSplit = pathname?.split("/");
    navigate(pathSplit?.slice(0, -1).join("/"));
  };

  return (
    <Box
      display="flex"
      flexDirection="column"
      height="100vh" // full page height
      bgcolor="background.neutral"
    >
      {/* Breadcrumbs header */}
      <Box bgcolor="background.paper">
        <Box
          p={2}
          display={"flex"}
          gap={2}
          justifyContent={"space-between"}
          flexDirection={"row"}
        >
          <Stack gap={2} direction={"row"}>
            <Button
              startIcon={
                <Iconify
                  icon="line-md:chevron-left"
                  width={16}
                  height={16}
                  color={"text.primary"}
                />
              }
              onClick={handleBack}
              variant="outlined"
              sx={{
                color: "text.primary",
                padding: theme.spacing(0.125, 1.5),
                height: 32,
                fontWeight: "fontWeightMedium",
                borderRadius: theme.spacing(0.5),
                borderColor: "action.selected",
              }}
            >
              Back
            </Button>
            <AgentDropdown />
          </Stack>

          <Button
            size="small"
            variant="outlined"
            color="primary"
            onClick={() => setOpenScenarioHelp(true)}
            sx={{
              typography: "s2",
              fontWeight: "fontWeightMedium",
              flexShrink: 0,
              borderRadius: theme.spacing(0.5),
              padding: theme.spacing(0.75, 1.5),
            }}
            startIcon={
              <SvgColor
                src="/assets/icons/ic_add.svg"
                sx={{
                  bgcolor: "primary.main",
                  height: "16px",
                  width: "16px",
                }}
              />
            }
          >
            Create scenarios
          </Button>
        </Box>
      </Box>
      <Box display="flex" flex={1} overflow="hidden">
        <Box
          maxWidth={"233px"}
          borderRight="1px solid"
          borderColor="divider"
          overflow="hidden"
          bgcolor="background.paper"
        >
          {isLoading ? (
            <VersionsListSkeleton />
          ) : (
            <VersionManagement
              agentId={agentDefinitionId}
              connected={!!agent.data?.connected}
              fetchNextVersions={fetchNextPage}
              hasNextVersions={hasNextPage}
              isFetchingNextVersions={isFetchingNextPage}
              versions={versions}
            />
          )}
        </Box>
        <Box bgcolor={"background.paper"} flex={4} overflow="auto">
          <VersionDetails />
        </Box>
      </Box>
      <CreateAgentScenarioHelp
        open={openScenarioHelp}
        onClose={() => {
          isNewAgentRef.current = false;
          setOpenScenarioHelp(false);
        }}
      />
    </Box>
  );
};

export default AgentDetailsView;
