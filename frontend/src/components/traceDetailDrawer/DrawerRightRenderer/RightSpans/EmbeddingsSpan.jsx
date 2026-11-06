import React, { useMemo } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../SpanAccordianElements";
import { Box, IconButton, Typography } from "@mui/material";
import { copyToClipboard, getScorePercentage } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
import { allExpanded, defaultStyles, JsonView } from "react-json-view-lite";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { typography } from "src/theme/typography";
import { useNavigate } from "react-router";
import { ShowComponent } from "src/components/show";
import { isJson } from "../getSpanData";

const EmbeddingsSpan = ({
  value,
  column,
  allowCopy = false,
  tabLabel = "",
  promptName,
  promptTemplateId,
}) => {
  const dataType = column?.dataType;
  const navigate = useNavigate();

  const formattedValue = useMemo(() => {
    if (dataType === "float") {
      return `${getScorePercentage(parseFloat(value?.cellValue) * 10)}%`;
    }
    return value?.cellValue;
  }, [value?.cellValue, dataType]);

  const handleClick = () => {
    if (!promptTemplateId) return;

    navigate(`/dashboard/workbench/create/${promptTemplateId}?tab=Metrics`);
  };

  return (
    <Accordion defaultExpanded disableGutters>
      <AccordionSummary>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            justifyContent: "space-between",
            width: "100%",
          }}
        >
          {column?.headerName}{" "}
          <ShowComponent condition={promptName && promptTemplateId}>
            <Box
              onClick={handleClick}
              sx={{
                display: "flex",
                alignItems: "center",
                paddingX: "8px",
                paddingY: "2px",
                borderRadius: (theme) => theme.spacing(1),
                whiteSpace: "nowrap",
                border: "1px solid",
                borderColor: "primary.light",
                backgroundColor: "action.hover",
                gap: (theme) => theme.spacing(1),
              }}
            >
              <Typography
                typography="s2"
                color="primary.dark"
                sx={{
                  maxWidth: "300px",
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                }}
              >
                Prompt : {promptName}
              </Typography>
            </Box>
          </ShowComponent>
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ paddingY: 1, paddingX: 0 }}>
        <Box
          sx={{
            paddingX: 2,
            paddingBottom: 1,
          }}
        >
          <Box
            sx={{
              border: "1px solid",
              borderColor: "action.selected",
              borderRadius: "8px",
              position: "relative",
            }}
          >
            <Box>
              {allowCopy && (
                <Box
                  sx={{
                    position: "absolute",
                    top: 0,
                    right: 10,
                  }}
                >
                  <IconButton
                    onClick={() => {
                      copyToClipboard(value?.cellValue);
                      enqueueSnackbar("Copied to clipboard", {
                        variant: "success",
                      });
                    }}
                  >
                    <Iconify
                      icon="solar:copy-linear"
                      width={20}
                      sx={{ color: "text.secondary" }}
                    />
                  </IconButton>
                </Box>
              )}
              <Box
                sx={{
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography
                  sx={{
                    fontSize: "13px",
                    fontWeight: typography["fontWeightSemiBold"],
                    color: "primary.main",
                    padding: 1.5,
                  }}
                >
                  {tabLabel}
                </Typography>
              </Box>
              <Typography
                sx={{
                  padding: 1.5,
                  fontSize: "13px",
                }}
              >
                {["array", "json"].includes(dataType) &&
                isJson(formattedValue) ? (
                  <JsonView
                    data={JSON.parse(formattedValue)}
                    shouldExpandNode={allExpanded}
                    clickToExpandNode={true}
                    style={defaultStyles}
                  />
                ) : (
                  formattedValue
                )}
              </Typography>
            </Box>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

EmbeddingsSpan.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
  allowCopy: PropTypes.bool,
  showBox: PropTypes.bool,
  tabLabel: PropTypes.string,
  promptName: PropTypes.string,
  promptTemplateId: PropTypes.string,
};

export default EmbeddingsSpan;
