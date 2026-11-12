import {
  Box,
  styled,
  Table,
  TableBody,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
  useTheme,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "src/routes/hooks";
import PropTypes from "prop-types";
import {
  getScorePercentage,
  interpolateColorBasedOnScore,
} from "src/utils/utils";
import CustomTableCell from "src/components/table/custom-table-cell";
import TemplateAccordion from "src/sections/common/TemplateAccordion";

const HeaderTableCell = styled(CustomTableCell)(({ theme }) => ({
  color: theme.palette.text.secondary,
}));

const ScoreCell = styled(CustomTableCell)(() => ({
  color: "common.white",
  padding: 0,
  margin: 0,
}));

const PromptTemplateResults = ({ selectedOptimization }) => {
  const { id } = useParams();

  const { data } = useQuery({
    queryFn: () =>
      axios.post(
        endpoints.optimization.getPromptTemplateResults(
          id,
          selectedOptimization.id,
        ),
      ),
    select: (d) => d.data,
    queryKey: ["propmp-template-results", id, selectedOptimization.id],
  });

  const allTemplates = data?.kPrompts || [];

  const tableData = data?.results || [];

  const theme = useTheme();

  return (
    <Box
      sx={{
        padding: "20px",
        display: "flex",
        gap: "16px",
        flexDirection: "column",
        minHeight: "calc(100vh - 210px)",
        overflowY: "auto",
      }}
    >
      {allTemplates.length > 0 && (
        <TemplateAccordion templates={allTemplates} />
      )}
      <Box sx={{ display: "flex", alignItems: "center", gap: "14px" }}>
        <Iconify icon="solar:card-linear" />
        <Typography fontWeight={600} fontSize="13px">
          Template Score
        </Typography>
      </Box>
      <TableContainer>
        <Table>
          <TableHead>
            <HeaderTableCell>Metrics</HeaderTableCell>
            {allTemplates.map((t, idx) => (
              <HeaderTableCell key={t}>Template {idx + 1}</HeaderTableCell>
            ))}
            <CustomTableCell sx={{ color: theme.palette.text.disabled }}>
              Old Template
            </CustomTableCell>
          </TableHead>
          <TableBody>
            {tableData.map(({ metricName, templates, oldTemplate }) => (
              <TableRow key={metricName}>
                <CustomTableCell sx={{ padding: 0 }}>
                  {metricName}
                </CustomTableCell>
                {allTemplates.map((t, idx) => (
                  <ScoreCell key={t}>
                    <Box
                      sx={{
                        padding: "0px",
                        backgroundColor: interpolateColorBasedOnScore(
                          templates?.[idx],
                        ),
                        textAlign: "center",
                        marginX: "2px",
                        marginY: "4px",
                        borderRadius: "4px",
                      }}
                    >
                      {getScorePercentage(templates?.[idx]).toFixed(0)}%
                    </Box>
                  </ScoreCell>
                ))}
                <ScoreCell>
                  <Box
                    sx={{
                      padding: "0px",
                      backgroundColor:
                        interpolateColorBasedOnScore(oldTemplate),
                      textAlign: "center",
                      margin: "2px",
                      borderRadius: "4px",
                    }}
                  >
                    {getScorePercentage(oldTemplate).toFixed(0)}%
                  </Box>
                </ScoreCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
};

PromptTemplateResults.propTypes = {
  selectedOptimization: PropTypes.object,
};

export default PromptTemplateResults;
