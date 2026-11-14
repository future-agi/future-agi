import PropTypes from "prop-types";
import { Box, Select, Typography } from "@mui/material";
import Section from "src/components/section";
import React, { useEffect, useState } from "react";
import { Panel, PanelGroup } from "react-resizable-panels";
import HorizontalResizeHandle from "src/components/resize/horizontal-resize-handler";
import VerticalResizeHandle from "src/components/resize/vertical-resize-handler";
import Umap from "src/components/three/umap";

import ClusterTable from "./cluster-table";
import ClusterViewSettings from "./cluster-view-settings";

const primaryColor = [255, 0, 0]; // Red
const comparisonColor = [0, 0, 255]; // Blue

export default function EmbeddingExplorer({ isDimensionVisible }) {
  const [gradientColors, setGradientColors] = useState([]);

  const clusterData = {
    cluster: [
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
      {
        name: "cluster 1",
        metric: { primary: 1, comparison: 1 },
        breakdown: { total: 10, primary: 10, comparison: 90 },
      },
    ],
    metric: "FNR",
    showComparison: true,
  };

  useEffect(() => {
    const steps = clusterData?.cluster?.length;
    const gradient = generateGradient(primaryColor, comparisonColor, steps);
    setGradientColors(gradient);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Section variant="panel">
      <Box
        sx={{
          p: 2,
        }}
      >
        {isDimensionVisible ? (
          <Typography variant="h6">
            {" "}
            Explore Embeddings for
            <Select size="small" sx={{ ml: 1 }} />
          </Typography>
        ) : null}

        <PanelGroup direction="horizontal">
          <Panel>
            <PanelGroup direction="vertical">
              <Panel>
                <ClusterTable
                  clusterData={clusterData}
                  gradientColors={gradientColors}
                />
              </Panel>
              <VerticalResizeHandle />
              <Panel>
                <ClusterViewSettings />
              </Panel>
            </PanelGroup>
          </Panel>
          <HorizontalResizeHandle />
          <Panel>
            <Umap />
          </Panel>
        </PanelGroup>
      </Box>
    </Section>
  );
}

EmbeddingExplorer.propTypes = {
  isDimensionVisible: PropTypes.bool,
};

function interpolateColor(color1, color2, factor) {
  if (arguments.length < 3) {
    factor = 0.5;
  }
  const result = color1.slice();
  for (let i = 0; i < 3; i++) {
    result[i] = Math.round(result[i] + factor * (color2[i] - color1[i]));
  }
  return result;
}

function generateGradient(startColor, endColor, steps) {
  const stepFactor = 1 / (steps - 1),
    gradient = [];

  for (let i = 0; i < steps; i++) {
    gradient.push(interpolateColor(startColor, endColor, stepFactor * i));
  }

  return gradient;
}
