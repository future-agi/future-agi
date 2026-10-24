import React from "react";
import { Box, Button, Divider, Grid, Stack, Typography } from "@mui/material";
import Section from "src/components/section";
import PropTypes from "prop-types";
import { useGetDatasetDetails } from "src/api/model/dataset";
import { useBoolean } from "src/hooks/use-boolean";

import BaselineDialog from "./performance/baseline-dialog";
import PerformanceConfigTable from "./performance/performance-config-table";

export default function ModelConfigView({ model }) {
  const { datasetDetails } = useGetDatasetDetails(model.id);

  const baselineDialog = useBoolean();

  return (
    <>
      <Stack spacing={3} direction={"column"}>
        <Section variant="panel">
          <Box sx={{ my: 2, mx: 2 }}>
            <Grid container>
              <Grid item xs>
                <Typography gutterBottom variant="h6">
                  Model Baseline
                </Typography>
              </Grid>
              <Grid item>
                <Button
                  variant="outlined"
                  onClick={baselineDialog.onTrue}
                  size="small"
                >
                  Configure Baseline
                </Button>
              </Grid>
            </Grid>
          </Box>
          <Divider variant="middle" />
          <Box sx={{ m: 2 }}>
            <Typography gutterBottom variant="body1">
              {`Your model's baseline is`} {model?.baselineModelEnvironment}{" "}
              {model?.baselineModelVersion} {model?.baselineModelBatch}
            </Typography>
          </Box>
        </Section>

        <Section variant="panel">
          <Box sx={{ my: 2, mx: 2 }}>
            <Grid container>
              <Grid item xs>
                <Typography gutterBottom variant="h6">
                  Performance Configuration
                </Typography>
              </Grid>
            </Grid>
          </Box>
          <Divider variant="middle" />
          <Box sx={{ m: 2 }}>
            <PerformanceConfigTable
              datasetDetails={datasetDetails}
              model={model}
            />
          </Box>
        </Section>
      </Stack>

      <BaselineDialog dialog={baselineDialog} model={model} />
    </>
  );
}

ModelConfigView.propTypes = {
  model: PropTypes.object,
};
