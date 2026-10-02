import React from "react";
import {
  SuggestionAccordion,
  SuggestionAccordionSummary,
  SuggestionAccordionDetails,
} from "./ShareComponents";
import SuggestionCard from "./SuggestionCard";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import Iconify from "../../../components/iconify";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import SvgColor from "../../../components/svg-color";
import { PriorityStatus } from "./common";
import { useTestDetailStoreShallow } from "../states";
import { useTestDetail } from "../context/TestDetailContext";

const NonFixableRecommendations = ({
  humanComparisonSummary,
  notFixableActionableRecommendations,
}) => {
  const {
    selectedNonFixableRecommendations,
    toggleSelectedNonFixableRecommendation,
  } = useTestDetailStoreShallow((s) => ({
    selectedNonFixableRecommendations: s.selectedNonFixableRecommendations,
    toggleSelectedNonFixableRecommendation:
      s.toggleSelectedNonFixableRecommendation,
  }));
  const theme = useTheme();

  const { getGridApi } = useTestDetail();
  const gridApi = getGridApi();

  return (
    <ShowComponent condition={notFixableActionableRecommendations?.length}>
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
              Infra based suggestions (
              {notFixableActionableRecommendations?.length ?? 0})
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
              These suggestions aren&apos;t supported by the optimizer. Please
              make the updates manually.
            </Typography>

            <ShowComponent condition={humanComparisonSummary}>
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
                  Human Comparison Summary
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
                  {humanComparisonSummary}
                </Typography>
              </Box>
            </ShowComponent>

            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: 1,
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
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "12px",
                  width: "100%",
                }}
              >
                {notFixableActionableRecommendations?.map(
                  (recommendation, index) => {
                    return (
                      <SuggestionCard
                        key={recommendation.heading}
                        title={recommendation.heading}
                        description={recommendation.recommendation}
                        isPriority={
                          recommendation.priority === PriorityStatus.HIGH
                        }
                        breakdown={recommendation.breaking_points}
                        callExecutionIds={recommendation?.call_execution_ids}
                        isSelected={selectedNonFixableRecommendations.find(
                          (r) => r.index === index,
                        )}
                        toggleSelection={() => {
                          if (
                            recommendation?.call_execution_ids === undefined ||
                            recommendation?.call_execution_ids === null
                          ) {
                            return;
                          }
                          toggleSelectedNonFixableRecommendation(
                            index,
                            recommendation?.call_execution_ids,
                          );
                          gridApi?.onFilterChanged?.();
                        }}
                      />
                    );
                  },
                )}
              </Box>
            </Box>
          </Box>
        </SuggestionAccordionDetails>
      </SuggestionAccordion>
    </ShowComponent>
  );
};

NonFixableRecommendations.propTypes = {
  humanComparisonSummary: PropTypes.string,
  notFixableActionableRecommendations: PropTypes.array,
};

export default NonFixableRecommendations;
