import React, { useRef, useState } from "react";
import { Box, useTheme, Button } from "@mui/material";
import Iconify from "src/components/iconify";

import PageHeadings from "../develop-detail/Common/PageHeadings";

import WorkbenchLandingPage from "./WorkbenchLandingPage";
import WorkbenchDetailView from "./workbenchDetailView/WorkbenchDetailView";
import InfoWorkbenchModal from "./workbenchDetailView/help/InfoWorkbenchModal";

const WorkbenchView = () => {
  const [open, setOpen] = useState(false);
  const theme = useTheme();
  const [hasData, setHasData] = useState(true);
  const gridRef = useRef(null);

  return hasData ? (
    <Box
      padding={theme.spacing(2)}
      height={"100%"}
      sx={{
        backgroundColor: "background.paper",
        display: "flex",
        flexDirection: "column",
        gap: theme.spacing(3),
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <PageHeadings
          title="Prompt management"
          description="Generate, test and refine prompts for your language Models"
        />
        <Button
          size="small"
          variant="text"
          color="inherit"
          endIcon={
            <Iconify icon="solar:question-circle-linear" color="text.primary" />
          }
          onClick={() => setOpen(true)}
        >
          Help
        </Button>
      </Box>
      <InfoWorkbenchModal open={open} onClose={() => setOpen(false)} />
      <WorkbenchDetailView setHasData={setHasData} ref={gridRef} />
    </Box>
  ) : (
    <WorkbenchLandingPage />
  );
};

export default WorkbenchView;
