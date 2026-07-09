import React, { useState } from "react";
import {
  SuggestionAccordion,
  SuggestionAccordionSummary,
  SuggestionAccordionDetails,
} from "./ShareComponents";
import SuggestionCard from "./SuggestionCard";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import Iconify from "../../../components/iconify";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { styled } from "@mui/material/styles";
import SvgColor from "../../../components/svg-color";
import CustomTooltip from "../../../components/tooltip/CustomTooltip";
import { LevelType, MetricsTabs, PriorityStatus } from "./common";
import { useTestDetailStoreShallow } from "../states";
import { useTestDetail } from "../context/TestDetailContext";
import CustomTabsPrimaryColorTheme from "src/components/tabs/CustomTabsPrimaryColorTheme";
import { useFixMyAgentDrawerStoreShallow } from "./state";
import { format } from "date-fns";

const RefreshIconButton = styled(IconButton)(({ theme }) => ({
  borderRadius: theme.spacing(0.5),
  border: `1px solid ${theme.palette.divider}`,
  padding: theme.spacing(0.625),
  color: "text.primary",
}));

const FixableRecommendations = ({
  summary = "",
  domainLevelFixableRecommendations,
  agentLevelFixableRecommendations,
  refetch,
  lastUpdated,
}) => {
  const {
    selectedFixableRecommendations,
    toggleSelectedFixableRecommendation,
  } = useTestDetailStoreShallow((s) => ({
    selectedFixableRecommendations: s.selectedFixableRecommendations,
    toggleSelectedFixableRecommendation: s.toggleSelectedFixableRecommendation,
  }));
  const theme = useTheme();
  const { getGridApi } = useTestDetail();
  const gridApi = getGridApi();
  const [selectedTab, setSelectedTab] = useState(LevelType.AGENT);
  const { setCreateEditOptimizationOpen } = useFixMyAgentDrawerStoreShallow(
    (state) => ({
      setCreateEditOptimizationOpen: state.setCreateEditOptimizationOpen,
    }),
  );
  const updatedFixableActionableRecommendations =
    selectedTab === LevelType.AGENT
      ? agentLevelFixableRecommendations
      : domainLevelFixableRecommendations;

  const totalSuggestions =
    (agentLevelFixableRecommendations?.length ?? 0) +
    (domainLevelFixableRecommendations?.length ?? 0);

  const getLastUpdatedText = () => {
    try {
      if (lastUpdated) {
        return `Last updated at ${format(new Date(lastUpdated), "MM/dd/yyyy, HH:mm:ss")}`;
      }
    } catch {
      return "";
    }
  };

  return (
    <ShowComponent
      condition={
        Boolean(summary) ||
        domainLevelFixableRecommendations?.length > 0 ||
        agentLevelFixableRecommendations?.length > 0
      }
    >
      <SuggestionAccordion defaultExpanded disableGutters>
        <SuggestionAccordionSummary
          expandIcon={
            <Iconify
              icon="material-symbols:keyboard-arrow-down-rounded"
              width={24}
              height={24}
            />
          }
        >
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ flex: 1 }}
          >
            <SvgColor
              src="/assets/icons/bulb_start.svg"
              sx={{ width: 16, height: 16, color: "yellow.500" }}
            />
            <Typography fontSize={14} fontWeight="fontWeightSemiBold">
              Prompt based suggestions
            </Typography>
          </Stack>
        </SuggestionAccordionSummary>
        <SuggestionAccordionDetails>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: "12px",
              width: "100%",
            }}
          >
            <Typography
              variant="s3"
              sx={{ color: theme.palette.text.secondary }}
            >
              Suggestions that can be fixed by our optimizer
            </Typography>

            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
                <Typography variant="s1" fontWeight="fontWeightMedium">
                  Suggestions ({totalSuggestions})
                </Typography>
                <ShowComponent condition={lastUpdated}>
                  <Typography
                    variant="s3"
                    sx={{ color: theme.palette.text.secondary }}
                  >
                    {getLastUpdatedText()}
                  </Typography>
                </ShowComponent>
              </Box>
              <Box sx={{ gap: 1, display: "flex", alignItems: "center" }}>
                <CustomTooltip
                  show
                  title="Refresh"
                  arrow
                  size="small"
                  type="black"
                >
                  <RefreshIconButton onClick={refetch}>
                    <SvgColor
                      sx={{ width: 20, height: 20 }}
                      src="/assets/icons/ic_rerun.svg"
                    />
                  </RefreshIconButton>
                </CustomTooltip>
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  startIcon={
                    <SvgColor src="/assets/icons/navbar/ic_optimize.svg" />
                  }
                  onClick={() => {
                    setCreateEditOptimizationOpen(true);
                  }}
                >
                  Optimize My Agent
                </Button>
              </Box>
            </Box>

            <ShowComponent condition={Boolean(summary)}>
              <Box
                sx={{
                  padding: 2,
                  border: "1px solid",
                  background:
                    theme.palette.mode === "dark"
                      ? theme.palette.background.default
                      : "#FFFFFF",
                  borderColor: theme.palette.divider,
                  borderRadius: 1,
                }}
              >
                <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
                  Summary
                </Typography>
                <Typography
                  typography={"s1"}
                  sx={{
                    maxHeight: "176px",
                    overflowY: "scroll",
                    "&::-webkit-scrollbar": {
                      height: "6px",
                      width: "6px",
                    },
                    "&::-webkit-scrollbar-track": {
                      backgroundColor: "transparent",
                    },
                    "&::-webkit-scrollbar-thumb": {
                      backgroundColor: theme.palette.action.hover,
                      borderRadius: "12px",
                      "&:hover": {
                        backgroundColor: theme.palette.action.selected,
                      },
                    },
                    scrollbarWidth: "thin",
                    scrollbarColor: `${theme.palette.action.hover} transparent`,
                  }}
                  fontWeight={"fontWeightRegular"}
                >
                  {summary}
                </Typography>
              </Box>
            </ShowComponent>
            <Box
              sx={{
                padding: 2,
                border: "1px solid",
                background:
                  theme.palette.mode === "dark"
                    ? theme.palette.background.default
                    : "#FFFFFF",
                borderColor: theme.palette.divider,
                borderRadius: 1,
                display: "flex",
                flexDirection: "column",
                gap: 1,
              }}
            >
              <Typography typography={"s2_1"} fontWeight={"fontWeightMedium"}>
                Actionable Suggestions ({totalSuggestions})
              </Typography>
              <CustomTabsPrimaryColorTheme
                value={selectedTab}
                onChange={(e, newValue) => setSelectedTab(newValue)}
                tabs={MetricsTabs}
              />
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  flexDirection: "row",
                }}
              >
                <Box
                  sx={{
                    gap: "12px",
                    paddingY: 0.5,
                    borderRadius: 0.5,
                    display: "flex",
                    alignItems: "center",
                  }}
                >
                  <SvgColor
                    src="/assets/icons/ic_info.svg"
                    sx={{ color: "blue.500", width: 16, height: 16 }}
                  />
                  <Typography variant="s2">
                    Click on multiple suggestions to view its associated calls
                  </Typography>
                </Box>
                <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
                  Suggestions(
                  {updatedFixableActionableRecommendations?.length ?? 0})
                </Typography>
              </Box>
              <ShowComponent
                condition={
                  updatedFixableActionableRecommendations?.length !== 0
                }
              >
                <Box
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "12px",
                    overflowY: "auto",
                    width: "100%",
                  }}
                >
                  {updatedFixableActionableRecommendations?.map(
                    (recommendation, index) => {
                      return (
                        <SuggestionCard
                          key={recommendation.heading}
                          title={recommendation.heading}
                          description={recommendation.recommendation}
                          breakdown={recommendation.breaking_points}
                          isPriority={
                            recommendation.priority === PriorityStatus.HIGH
                          }
                          callExecutionIds={recommendation?.call_execution_ids}
                          isSelected={selectedFixableRecommendations.find(
                            (r) => r.index === index,
                          )}
                          toggleSelection={() => {
                            if (
                              recommendation?.call_execution_ids ===
                                undefined ||
                              recommendation?.call_execution_ids === null
                            ) {
                              return;
                            }
                            toggleSelectedFixableRecommendation(
                              index,
                              recommendation?.call_execution_ids,
                            );
                            gridApi?.onFilterChanged?.();
                          }}
                          branchCategory={recommendation?.branch_category}
                        />
                      );
                    },
                  )}
                </Box>
              </ShowComponent>
              <ShowComponent
                condition={
                  updatedFixableActionableRecommendations?.length === 0
                }
              >
                <Typography
                  variant="s2"
                  sx={{
                    textAlign: "center",
                    color: theme.palette.text.secondary,
                    mt: 2,
                    height: "150px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  No recommendations are available for this category.
                </Typography>
              </ShowComponent>
            </Box>
          </Box>
        </SuggestionAccordionDetails>
      </SuggestionAccordion>
    </ShowComponent>
  );
};

FixableRecommendations.propTypes = {
  fixableActionableRecommendations: PropTypes.array,
  domainLevelFixableRecommendations: PropTypes.array,
  agentLevelFixableRecommendations: PropTypes.array,
  summary: PropTypes.string,
  refetch: PropTypes.func,
  lastUpdated: PropTypes.string,
};

export default FixableRecommendations;
