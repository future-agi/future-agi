import React from "react";
import { Box, Button, Tab, Tabs, Typography, alpha } from "@mui/material";
import { m } from "framer-motion";
import { varHover } from "src/components/animate";
import Iconify from "src/components/iconify";

export default function DataImportJobs() {
  return (
    <>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          p: 2,
        }}
      >
        <Typography variant="h6">Jobs</Typography>
        <Button
          component={m.button}
          whileTap="tap"
          whileHover="hover"
          variants={varHover(1.05)}
          startIcon={<Iconify icon="solar:pen-linear" />}
          // onClick={columnsPopover.onOpen}
          // sx={{
          //   width: 40,
          //   height: 40,
          //   background: (theme) => alpha(theme.palette.text.disabled, 0.08),
          //   ...(columnsPopover.open && {
          //     background: (theme) =>
          //       `linear-gradient(135deg, ${theme.palette.primary.light} 0%, ${theme.palette.primary.main} 100%)`,
          //   }),
          // }}
        >
          New Job
        </Button>
      </Box>

      <Box
        sx={{
          px: 2.5,
          boxShadow: (theme) =>
            `inset 0 -2px 0 0 ${alpha(theme.palette.text.disabled, 0.08)}`,
        }}
      >
        <Tabs
        // value={currentTab}
        // onChange={handleTab}
        >
          <Tab value={"Cloud DB"} label={"cloudDB"} />
          <Tab value={"Cloud Storage"} label={"cloudStorage"} />
          <Tab value={"analytics"} label={"Analytics"} />
          <Tab value={"local"} label={"Local File"} />
        </Tabs>
      </Box>
    </>
  );
}
