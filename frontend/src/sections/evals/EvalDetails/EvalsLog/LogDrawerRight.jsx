import React from "react";
import {
  alpha,
  Box,
  Button,
  Chip,
  Divider,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import {
  getLabel,
  getStatusColor,
} from "src/sections/develop-detail/DataTab/common";
import { ShowComponent } from "src/components/show";
import ErrorLocalizeCard from "src/sections/common/ErrorLocalizeCard";
import CellMarkdown from "src/sections/common/CellMarkdown";
import JsonCodeView from "src/components/code/json-code-view";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import { canonicalEntries } from "src/utils/utils";

const LogDrawerRight = ({
  output,
  error,
  addFeedbackClick,
  isNumericOutput,
}) => {
  const theme = useTheme();
  const { role } = useAuthContext();
  const errorAnalysis = error?.error_analysis || error?.errorAnalysis;
  const selectedInputKey = error?.selected_input_key || error?.selectedInputKey;
  const canonicalErrorAnalysis = Object.fromEntries(
    canonicalEntries(errorAnalysis || {}),
  );
  const errorDatapoint = {
    ...error,
    selected_input_key: selectedInputKey,
    input_data: error?.input_data || error?.inputData,
    input_types: error?.input_types || error?.inputTypes,
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        position: "relative",
        height: "100%",
      }}
    >
      <Box
        sx={{
          flex: 1,
          flexDirection: "column",
          gap: "15px",
          overflowY: "auto",
          // height: "100%",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "5px",
            marginBottom: "25px",
          }}
        >
          <Typography fontWeight={500} fontSize={14} color="text.primary">
            Score
          </Typography>
          <Box>
            {!output?.output && output?.output != 0 ? (
              <Box>-</Box>
            ) : (
              <>
                <ShowComponent
                  condition={
                    typeof output?.output === "string" ||
                    typeof output?.output === "number"
                  }
                >
                  <Chip
                    variant="soft"
                    label={
                      isNumericOutput
                        ? output?.output
                        : getLabel(output?.output)
                    }
                    size="small"
                    sx={
                      isNumericOutput
                        ? {
                            transition: "none",
                            "&:hover": { boxShadow: "none" },
                          }
                        : {
                            ...getStatusColor(output?.output, theme),
                            transition: "none",
                            "&:hover": {
                              backgroundColor: getStatusColor(
                                output?.output,
                                theme,
                              )?.backgroundColor,
                              boxShadow: "none",
                            },
                          }
                    }
                  />
                </ShowComponent>
              </>
            )}
          </Box>
        </Box>

        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "8px",
            marginBottom: "25px",
            overflowWrap: "break-word",
          }}
        >
          <Typography fontWeight={500} fontSize={14} color="text.primary">
            Explanation
          </Typography>
          <Box
            sx={{
              border: `1px solid ${alpha(theme.palette.text.disabled, 0.2)}`,
              padding: "16px",
              borderRadius: "4px",
            }}
          >
            <Typography fontWeight={400} fontSize={14} color="text.primary">
              {typeof output.reason === "string" ? (
                output?.reason?.trim() ? (
                  <CellMarkdown spacing={0} text={output?.reason} />
                ) : (
                  "Unable to fetch Explanation"
                )
              ) : (
                output?.reason?.map((item, index) => (
                  <CellMarkdown key={index} spacing={0} text={item} />
                ))
              )}
            </Typography>
          </Box>
        </Box>

        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "8px",
            // height: "100%",
            marginY: "15px",
            overflowWrap: "break-word",
            position: "relative",
          }}
        >
          <Typography fontWeight={500} fontSize={14} color="text.primary">
            Possible Error
          </Typography>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 2,
              overflowY: "auto",
            }}
          >
            {error &&
              typeof error === "object" &&
              errorAnalysis &&
              (() => {
                return canonicalEntries(errorAnalysis)
                  .filter(([_key, value]) => value.length)
                  .map(([key, value]) => (
                    <ErrorLocalizeCard
                      key={key}
                      value={value}
                      column={selectedInputKey || key}
                      tabValue="raw"
                      datapoint={errorDatapoint}
                    />
                  ));
              })()}
          </Box>
        </Box>
        {error && typeof error === "object" && errorAnalysis && (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: "8px",
              // height: "100%",
              marginY: "15px",
              overflowWrap: "break-word",
              position: "relative",
            }}
          >
            <Typography fontWeight={500} fontSize={14} color="text.primary">
              Raw Data
            </Typography>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: (theme) => theme.spacing(1),
                overflow: "auto",
                minHeight: "230px",
                height: "100%",
                position: "relative",
              }}
            >
              <Box
                sx={{
                  position: "sticky",
                  top: 0,
                  width: "100%",
                  zIndex: 1,
                  backgroundColor: "background.paper",
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: 1,
                  }}
                >
                  <Typography
                    typography="s1"
                    fontWeight={"fontWeightRegular"}
                    color="primary.main"
                  >
                    JSON
                  </Typography>
                </Box>
                <Divider orientation="horizontal" />
              </Box>
              <Box
                sx={{
                  minHeight: "calc(100% - 48px)",
                  color: "text.primary",
                  "& pre": {
                    whiteSpace: "pre-wrap",
                  },
                  wordBreak: "break-all",
                  "& div": {
                    backgroundColor: `${theme.palette.background.paper} !important`,
                  },
                }}
              >
                <JsonCodeView data={canonicalErrorAnalysis} />
              </Box>
            </Box>
          </Box>
        )}
      </Box>
      {!isNumericOutput && RolePermission.EVALS[PERMISSIONS.UPDATE][role] && (
        <Box
          paddingY={2}
          textAlign="right"
          sx={{ backgroundColor: "background.paper" }}
        >
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={addFeedbackClick}
          >
            Add Feedback
          </Button>
        </Box>
      )}
    </Box>
  );
};

export default LogDrawerRight;

LogDrawerRight.propTypes = {
  output: PropTypes.object,
  error: PropTypes.object,
  addFeedbackClick: PropTypes.func,
  isNumericOutput: PropTypes.bool,
};
