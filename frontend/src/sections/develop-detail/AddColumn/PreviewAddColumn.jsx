import { Box, Collapse, Tab, Tabs, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { ShowComponent } from "src/components/show";
import CellMarkdown from "src/sections/common/CellMarkdown";
import CustomTooltip from "src/components/tooltip";
import { VOCAB } from "src/sections/common/vocab";

/**
 * v2.1 g8 (dyncol:api-calls): an API call to a host other than this machine
 * is answered by the prototype standing in for that host; the Test result
 * says so exactly as the endpoint source's HTTP Test does.
 */
const StandInTag = ({ result }) => {
  if (!result?.simulated) return null;
  const host = result.host ?? "that host";
  return (
    <CustomTooltip
      show
      title={`Stands in for ${host} answering. The run fills exactly as a real one would.`}
      size="small"
      placement="top"
    >
      <Typography
        component="span"
        typography="s3"
        color="text.secondary"
        data-testid="api-column-test-simulated"
        sx={{
          display: "inline-block",
          mb: 0.5,
          px: 0.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
        }}
      >
        {`${VOCAB.SIMULATED} · ${host}`}
      </Typography>
    </CustomTooltip>
  );
};
StandInTag.propTypes = { result: PropTypes.object };

const PreviewAddColumn = ({ open, previewData }) => {
  const [selectedTab, setSelectedTab] = useState("markdown");

  const handleChange = (event, newValue) => {
    setSelectedTab(newValue);
  };

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
          <Typography fontWeight={600} color="text.secondary">
            Test output
          </Typography>
        </Box>
        <Tabs
          value={selectedTab}
          onChange={handleChange}
          aria-label="basic tabs example"
          textColor="primary"
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
            {previewData?.data?.result?.preview_results?.map((r) => (
              <Box
                key={r.row_id}
                sx={{
                  padding: "10px",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  wordWrap: "break-word",
                  whiteSpace: "normal",
                }}
              >
                <StandInTag result={r} />
                <Typography>{r.output}</Typography>
              </Box>
            ))}
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
            {previewData?.data?.result?.preview_results?.map((r) => (
              <Box
                key={r.row_id}
                sx={{
                  padding: "10px",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  wordWrap: "break-word",
                  whiteSpace: "normal",
                }}
              >
                <StandInTag result={r} />
                <CellMarkdown spacing={0} text={r.output} />
              </Box>
            ))}
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
