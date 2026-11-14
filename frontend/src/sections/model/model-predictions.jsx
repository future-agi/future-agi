import { Box, Button, Tab, Tabs, Typography, alpha } from "@mui/material";
import Section from "src/components/section";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { m } from "framer-motion";
import { varHover } from "src/components/animate";
import Iconify from "src/components/iconify";
import { usePopover } from "src/components/custom-popover";
import PredictionTable from "./prediction-table";

const _defaultColumns = [
  "Event Name",
  "Time",
  "AI Model",
  "Model Input",
  "Model Output",
];

export default function ModelPredictions({
  primaryDataset,
  comparisonDataset,
}) {
  const columnsPopover = usePopover();
  const [currentTab, setCurrentTab] = useState("primary");
  const [tabOptions, setTabOptions] = useState([
    { value: "primary", label: "Primary" },
  ]);

  const [modPropertiesData, _setModPropertiesData] = useState([]);

  useEffect(() => {}, [currentTab]);

  useEffect(() => {
    if (comparisonDataset && tabOptions.length == 1) {
      setTabOptions((prevValue) => [
        ...prevValue,
        ...[{ value: "comparison", label: "Comparison" }],
      ]);
    }
  }, [primaryDataset, comparisonDataset]);

  function handleTab(event, newValue) {
    setCurrentTab(newValue);
  }

  return (
    <Section variant="panel">
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          p: 2,
        }}
      >
        <Typography variant="h6">Predictions</Typography>
        <Button
          component={m.button}
          whileTap="tap"
          whileHover="hover"
          variants={varHover(1.05)}
          startIcon={<Iconify icon="solar:pen-linear" />}
          onClick={columnsPopover.onOpen}
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
          Edit Columns |{" "}
          {modPropertiesData?.filter((value) => value.selected).length}
        </Button>
      </Box>

      <Box
        sx={{
          px: 2.5,
          boxShadow: (theme) =>
            `inset 0 -2px 0 0 ${alpha(theme.palette.text.disabled, 0.08)}`,
        }}
      >
        <Tabs value={currentTab} onChange={handleTab}>
          {tabOptions.map((tab) => (
            <Tab
              key={tab.value}
              iconPosition="end"
              value={tab.value}
              label={tab.label}
            />
          ))}
        </Tabs>
      </Box>

      <PredictionTable
      // tableData={}
      // properties={}
      />
    </Section>
  );
}

ModelPredictions.propTypes = {
  model: PropTypes.object,
  primaryDataset: PropTypes.object,
  comparisonDataset: PropTypes.object,
};
