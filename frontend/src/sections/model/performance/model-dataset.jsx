import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  Grid,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import React, { useState } from "react";
import { useGetModelDataset } from "src/api/model/dataset";
import { useBoolean } from "src/hooks/use-boolean";

import DatasetSummary from "./dataset-summary";
import BaselineDialog from "./baseline-dialog";

export default function ModelDataset({ model }) {
  const { datasets } = useGetModelDataset(model.id);

  const [selectedRow, setSelectedRow] = useState(null);

  const baselineDialog = useBoolean();
  const exportDialog = useBoolean();

  const handleRowClick = (row, index) => {
    setSelectedRow({
      row,
      index,
    });
  };

  return (
    <>
      <Section variant="panel">
        <Box sx={{ my: 2, mx: 2 }}>
          <Grid container>
            <Grid item xs>
              <Typography gutterBottom variant="h4">
                Model Baseline
              </Typography>
            </Grid>
            <Grid item>
              <Typography gutterBottom variant="h6">
                <Button variant="outlined" onClick={baselineDialog.onTrue}>
                  Configure Baseline
                </Button>
              </Typography>
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

      <Section variant="panel" sx={{ mt: 2 }}>
        <Box sx={{ my: 2, mx: 2 }}>
          <Grid container>
            <Grid item xs>
              <Typography gutterBottom variant="h4">
                Dataset
              </Typography>
            </Grid>
            <Grid item>
              <Typography gutterBottom variant="h6">
                <Button variant="outlined" onClick={exportDialog.onTrue}>
                  Export
                </Button>
              </Typography>
            </Grid>
          </Grid>
        </Box>
        <TableContainer>
          <Table size={"small"}>
            <TableHead>
              <TableRow>
                <TableCell>Start Date</TableCell>
                <TableCell>End Date</TableCell>
                <TableCell>Environment</TableCell>
                <TableCell>Version</TableCell>
                <TableCell>Batch name</TableCell>
                <TableCell>Prediction Volume</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {datasets?.map((row, index) => (
                <TableRow
                  hover
                  selected={selectedRow?.index === index}
                  key={index}
                  sx={{ "&:last-child td, &:last-child th": { border: 0 } }}
                  style={{ cursor: "pointer" }}
                  onClick={() => handleRowClick(row, index)}
                >
                  <TableCell>{row.startDate}</TableCell>
                  <TableCell>{row.endDate}</TableCell>
                  <TableCell>{row.environment}</TableCell>
                  <TableCell>{row.modelVersion}</TableCell>
                  <TableCell>{row.batchId}</TableCell>
                  <TableCell>{row.count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Section>

      <Section variant="panel" sx={{ mt: 2 }}>
        <DatasetSummary dataset={selectedRow?.row} model={model} />
      </Section>

      <BaselineDialog dialog={baselineDialog} model={model} />
    </>
  );
}

ModelDataset.propTypes = {
  model: PropTypes.object,
};
