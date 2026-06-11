import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Divider,
  Typography,
  Collapse,
  Stack,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import SvgColor from "src/components/svg-color";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

export default function MoreActions({
  isMoreOpen,
  setMoreOpen,
  loadingPrompt,
  currentTab,
  setVersionHistoryOpen,
  trackEvent,
  Events,
  PropertyName,
  id,
  selectedVersions,
  theme,
  isAddingDraft,
  addToCompare,
  userRole,
}) {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "row",
        gap: isMoreOpen ? "8px" : 0,
      }}
    >
      <CustomTooltip
        show
        title={isMoreOpen ? "Minimize" : "Expand"}
        arrow
        size="small"
        type="black"
        slotProps={{
          tooltip: {
            sx: {
              maxWidth: "200px !important",
            },
          },
        }}
      >
        <Button
          sx={{
            borderRadius: "4px",
            backgroundColor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            height: "30px",
            minWidth: "30px",
            paddingLeft: isMoreOpen ? "12px" : "8px",
            paddingRight: isMoreOpen ? "12px" : "8px",
            "& .MuiButton-startIcon": {
              ...(isMoreOpen && { mr: 0 }),
            },
            "&:hover": {
              backgroundColor: `${theme.palette.background.neutral} !important`,
            },
          }}
          onClick={() => setMoreOpen(!isMoreOpen)}
          startIcon={
            <SvgColor
              sx={{
                width: "16px",
                height: "16px",
                color: "text.primary",
              }}
              src={
                isMoreOpen
                  ? "/assets/icons/ic_more_close.svg"
                  : "/assets/icons/ic_more_open.svg"
              }
            />
          }
        >
          {!isMoreOpen && (
            <Typography variant="s1" fontWeight={"fontWeightMedium"}>
              More
            </Typography>
          )}
        </Button>
      </CustomTooltip>

      <Collapse
        in={isMoreOpen}
        orientation="horizontal"
        timeout={400}
        sx={{
          display: "flex",
          alignItems: "center",
          flexWrap: "nowrap",
          whiteSpace: "nowrap",
          overflow: "hidden",
        }}
      >
        <Stack direction={"row"} gap={1}>
          <Divider
            flexItem
            orientation="vertical"
            sx={{
              height: "30px",
              borderColor: "divider",
              marginBottom: 1,
              alignSelf: "center",
              mx: 0.75,
            }}
          />
          <CustomTooltip
            show
            title="View all previous versions of your prompt, restore older ones, and see the tags associated"
            arrow
            size="small"
            type="black"
            slotProps={{
              tooltip: {
                sx: {
                  maxWidth: "200px !important",
                },
              },
            }}
          >
            <span
              style={{
                display: "inline-block",
              }}
            >
              <Button
                sx={{
                  borderRadius: "4px",
                  backgroundColor: "background.paper",
                  border: "1px solid",
                  borderColor: "divider",
                  height: "30px",
                  whiteSpace: "nowrap",
                  cursor: "pointer",
                  "&:hover": {
                    backgroundColor: `${theme.palette.background.neutral} !important`,
                  },
                }}
                disabled={
                  loadingPrompt ||
                  currentTab === "Evaluation" ||
                  currentTab === "Metrics"
                }
                onClick={() => {
                  setVersionHistoryOpen(true);
                  trackEvent(Events.promptVersionHistoryClicked, {
                    [PropertyName.promptId]: id,
                  });
                }}
                startIcon={
                  <SvgColor
                    sx={{
                      width: "16px",
                      height: "16px",
                      color:
                        loadingPrompt ||
                        currentTab === "Evaluation" ||
                        currentTab === "Metrics"
                          ? "divider"
                          : "text.primary",
                    }}
                    src="/assets/icons/ic_history.svg"
                  />
                }
              >
                <Typography variant="s1" fontWeight={"fontWeightMedium"}>
                  History
                </Typography>
              </Button>
            </span>
          </CustomTooltip>

          <CustomTooltip
            show
            title="Compare prompts side-by-side by tweaking words, model settings, and other parameters "
            arrow
            size="small"
            type="black"
            slotProps={{
              tooltip: {
                sx: {
                  maxWidth: "200px !important",
                },
              },
            }}
          >
            <span
              style={{
                display: "inline-block",
              }}
            >
              <IconButton
                sx={{
                  borderRadius: "4px",
                  backgroundColor: "background.paper",
                  border: "1px solid",
                  borderColor: "divider",
                  gap: theme.spacing(0.5),
                  height: "30px",
                  whiteSpace: "nowrap",
                  "&:hover": {
                    backgroundColor: `${theme.palette.background.neutral} !important`,
                  },
                }}
                disabled={
                  selectedVersions.length >= 3 ||
                  currentTab === "Evaluation" ||
                  currentTab === "Metrics" ||
                  isAddingDraft
                }
                onClick={() => {
                  addToCompare();
                  trackEvent(Events.promptCompareClicked, {
                    [PropertyName.promptId]: id,
                    [PropertyName.type]: "workbench",
                  });
                }}
              >
                <SvgColor
                  sx={{
                    width: "16px",
                    height: "16px",
                    color:
                      loadingPrompt ||
                      currentTab === "Evaluation" ||
                      currentTab === "Metrics" ||
                      selectedVersions.length >= 3 ||
                      isAddingDraft ||
                      !RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]
                        ? "divider"
                        : "text.primary",
                    cursor: "pointer",
                  }}
                  src="/assets/icons/ic_compare.svg"
                />

                <Typography
                  typography="s1"
                  fontWeight={"fontWeightMedium"}
                  color={
                    selectedVersions.length >= 3 ||
                    loadingPrompt ||
                    currentTab === "Evaluation" ||
                    currentTab === "Metrics" ||
                    isAddingDraft
                      ? "divider"
                      : "text.primary"
                  }
                >
                  Compare
                </Typography>
              </IconButton>
            </span>
          </CustomTooltip>
        </Stack>
      </Collapse>
    </Box>
  );
}

MoreActions.propTypes = {
  isMoreOpen: PropTypes.bool.isRequired,
  setMoreOpen: PropTypes.func.isRequired,
  loadingPrompt: PropTypes.bool.isRequired,
  currentTab: PropTypes.string.isRequired,
  setVersionHistoryOpen: PropTypes.func.isRequired,
  trackEvent: PropTypes.func.isRequired,
  Events: PropTypes.object.isRequired,
  PropertyName: PropTypes.object.isRequired,
  id: PropTypes.string.isRequired,
  selectedVersions: PropTypes.array.isRequired,
  theme: PropTypes.object.isRequired,
  isAddingDraft: PropTypes.bool.isRequired,
  addToCompare: PropTypes.func.isRequired,
  userRole: PropTypes.string,
};
