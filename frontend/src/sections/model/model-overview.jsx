import React from "react";
import { Collapse, Grid, IconButton } from "@mui/material";
import Section from "src/components/section";
import PropTypes from "prop-types";
import { useGetDatasetDetails } from "src/api/model/dataset";
import { DatasetMetricChart } from "src/components/charts";
import Iconify from "src/components/iconify";
import { DatasetFilter } from "src/components/selectors";
import { useBoolean } from "src/hooks/use-boolean";

import DatasetSummary from "./performance/dataset-summary";

export default function ModelOverview({ model }) {
  const collapsible = useBoolean(true);

  const { datasetDetails } = useGetDatasetDetails(model.id);

  const modelSummary = (
    <>
      <Section
        variant="panel"
        title="Model Overview"
        actions={
          <IconButton
            size="small"
            color={collapsible.value ? "inherit" : "default"}
            onClick={collapsible.onToggle}
          >
            <Iconify
              icon={
                collapsible.value
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
            />
          </IconButton>
        }
      >
        <Collapse in={collapsible.value} unmountOnExit>
          <Grid container>
            <Grid item xs={3} />
          </Grid>
        </Collapse>
      </Section>
    </>
  );

  return (
    <>
      {modelSummary}
      <DatasetFilter
        datasets={datasetDetails}
        showComparison={true}
        isBaseline={true}
      />

      <Section variant="panel">
        <DatasetMetricChart isBrush={false} />
      </Section>

      <Section variant="panel" sx={{ mt: 2 }}>
        <DatasetSummary dataset={{}} model={model} />
      </Section>
    </>
  );
}

ModelOverview.propTypes = {
  model: PropTypes.object,
};
