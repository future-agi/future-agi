import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import SvgColor from "src/components/svg-color";
import {
  getFileType,
  getFileTypeFromMime,
} from "./DevelopCellRenderer/CellRenderers/common";
import { getFileIcon } from "../knowledge-base/sheet-view/icons";
import _ from "lodash";

const DocumentDatapointCard = ({ value, column }) => {
  const theme = useTheme();
  const cellValue = value?.cell_value;
  const valueInfos = value?.value_infos;
  const hasDocument = Boolean(cellValue);
  const { fileName, fileType } = useMemo(() => {
    const fileName = cellValue?.split("/")?.pop() || cellValue;
    let fileType = getFileType(cellValue?.split(".")?.pop());
    if (!cellValue?.startsWith("data:")) {
      if (
        valueInfos?.documentName &&
        cellValue?.split(".")?.pop()?.includes("/")
      ) {
        const mimeMatch = valueInfos?.documentName?.match(/data:([^;]+)/);
        if (mimeMatch) {
          const mimeType = mimeMatch[1];
          fileType = getFileType(getFileTypeFromMime(mimeType));
        }
      }
    }
    return {
      fileName,
      fileType,
    };
  }, [cellValue, valueInfos]);

  return (
    <Accordion defaultExpanded disableGutters>
      <AccordionSummary
        aria-label={`Expand ${column?.headerName}`}
        sx={{
          flexDirection: "row",
          paddingLeft: 1,
          paddingRight: 2,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <SvgColor
            src={`/assets/icons/files/ic_file_head.svg`}
            sx={{ width: 20, height: 20, color: "text.secondary" }}
          />
          <Typography
            variant="s1"
            color="text.disabled"
            fontWeight="fontWeightMedium"
          >
            {column?.headerName}
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 2, pb: 1 }}>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            mb: 1,
          }}
        >
          <Box
            sx={{
              px: 2,
              py: 1.5,
              backgroundColor: "background.default",
              borderRadius: "6px",
            }}
          >
            {hasDocument ? (
              <Stack
                direction={"row"}
                alignItems={"flex-start"}
                gap={theme.spacing(1.5)}
              >
                <Box
                  component={"img"}
                  sx={{
                    height: "20px",
                    width: "20px",
                  }}
                  alt="document icon"
                  src={getFileIcon(fileType, "pdf")}
                />
                <Stack direction={"column"} gap={theme.spacing(0.25)}>
                  <Typography
                    variant="s2"
                    color={"text.primary"}
                    fontWeight={"fontWeightMedium"}
                  >
                    {fileName}
                  </Typography>
                  <Typography
                    variant="s2"
                    color={"text.primary"}
                    fontWeight={"fontWeightRegular"}
                  >
                    {_.upperCase(fileType)}
                  </Typography>
                </Stack>
              </Stack>
            ) : (
              <Box
                sx={{
                  height: 150,
                  flex: 1,
                  maxWidth: 150,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: "var(--bg-input)",
                  borderRadius: "8px",
                  border: "1px dashed var(--border-default)",
                }}
              >
                <Typography>No document added</Typography>
              </Box>
            )}
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

DocumentDatapointCard.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
};

export default DocumentDatapointCard;
