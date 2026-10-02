import { Box, Collapse, Tab, Tabs, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { ShowComponent } from "src/components/show";
import CellMarkdown from "src/sections/common/CellMarkdown";

const PreviewAddColumn = ({ open, previewData }) => {
  const [selectedTab, setSelectedTab] = useState("markdown");

  const handleChange = (event, newValue) => {
    setSelectedTab(newValue);
  };

  const theme = useTheme();

  return (
    <Collapse in={open} orientation="horizontal" unmountOnExit>
      <Box
        sx={{
          borderRight: "1px solid",
          borderColor: "divider",
          padding: "20px",
          width: "450px",
          height: "100vh",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography fontWeight={700} color="text.secondary">
            Preview Test
          </Typography>
        </Box>
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
          <Tab label="Markdown" value="markdown" />
          <Tab label="Raw" value="raw" />
        </Tabs>
        <ShowComponent condition={selectedTab === "raw"}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 1.5,
              paddingY: 2,
            }}
          >
            {previewData?.data?.result?.preview_results?.map(
              ({ row_id, output }) => (
                <Box
                  key={row_id}
                  sx={{
                    padding: "10px",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "10px",
                    wordWrap: "break-word",
                    whiteSpace: "normal",
                  }}
                >
                  <Typography>{output}</Typography>
                </Box>
              ),
            )}
          </Box>
        </ShowComponent>
        <ShowComponent condition={selectedTab === "markdown"}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 1.5,
              paddingY: 2,
            }}
          >
            {previewData?.data?.result?.preview_results?.map(
              ({ row_id, output }) => (
                <Box
                  key={row_id}
                  sx={{
                    padding: "10px",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "10px",
                    wordWrap: "break-word",
                    whiteSpace: "normal",
                  }}
                >
                  <CellMarkdown spacing={0} text={output} />
                </Box>
              ),
            )}
          </Box>
        </ShowComponent>
      </Box>
    </Collapse>
  );
};

PreviewAddColumn.propTypes = {
  open: PropTypes.bool,
  previewData: PropTypes.any,
};

export default PreviewAddColumn;
