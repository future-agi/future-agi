import React, { useMemo } from "react";
import CardWrapper from "./CardWrapper";
import PropTypes from "prop-types";
import {
  Divider,
  Stack,
  Typography,
  LinearProgress,
  useTheme,
  Grid,
  Box,
} from "@mui/material";
import { addColorsToData, getColor, getLabel } from "./common";
import { formatPercentage } from "../../../utils/utils";
import DeterministicEvaluationChart from "./DeterministicEvaluationChart.jsx";
import { ShowComponent } from "src/components/show";

function EvalsCard({ keyName, value }) {
  return (
    <Stack gap={0.25}>
      <Stack
        direction={"row"}
        justifyContent={"space-between"}
        alignItems={"center"}
      >
        <Typography
          typography={"s2"}
          color={"text.primary"}
          fontWeight={"fontWeightRegular"}
        >
          {getLabel(keyName)}
        </Typography>
        <Typography
          typography={"s1"}
          color={"text.primary"}
          fontWeight={"fontWeightRegular"}
        >
          {formatPercentage(value)}
        </Typography>
      </Stack>
      <LinearProgress
        sx={{
          height: (theme) => theme.spacing(0.75),
          "& .MuiLinearProgress-bar": {
            backgroundColor: getColor(value),
          },
          backgroundColor: "background.neutral",
        }}
        variant={"determinate"}
        value={value}
      />
    </Stack>
  );
}

EvalsCard.propTypes = {
  keyName: PropTypes.string,
  value: PropTypes.number,
};

export default function EvaluationMetrics({
  data,
  expanded,
  isPending,
  status,
}) {
  const theme = useTheme();
  const evals = data?.evalMetrics || {};
  const deterministicEvals = useMemo(() => {
    if (!data?.deterministicEvals) return [];

    return data.deterministicEvals.map((item) => ({
      ...item,
      data: addColorsToData(
        [...item.data].sort((a, b) => b.value - a.value), // sort by value descending
        theme,
      ),
    }));
  }, [data?.deterministicEvals, theme]);
  const keysLength = Object.keys(evals)?.length + deterministicEvals?.length;
  const hasNoMetrics =
    keysLength === 0 &&
    !isPending &&
    !!status &&
    !["pending", "running", "queued", "evaluating"].includes(status);
  const isAwaitingEvals = keysLength === 0 && !hasNoMetrics;

  return (
    <CardWrapper
      expanded={expanded}
      title={
        isAwaitingEvals
          ? "Evaluation Metrics"
          : `Evaluation Metrics (${keysLength})`
      }
    >
      {isAwaitingEvals ? (
        <Box
          sx={{
            py: 5,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 1,
          }}
        >
          <Typography
            typography={"s2"}
            color={"text.secondary"}
            fontWeight={"fontWeightRegular"}
          >
            Evaluation results will appear shortly
          </Typography>
        </Box>
      ) : hasNoMetrics && !isPending ? (
        <Box
          sx={{
            py: 4,
            px: 2,
            textAlign: "center",
          }}
        >
          <Typography
            typography={"body1"}
            color={"text.secondary"}
            fontWeight={"fontWeightRegular"}
          >
            No evaluation metrics added
          </Typography>
        </Box>
      ) : (
        <>
          <ShowComponent condition={Object.keys(evals).length > 0}>
            <Stack
              direction={"column"}
              gap={3}
              sx={{
                my: 1.375,
              }}
            >
              {Object.entries(evals)
                ?.sort((a, b) => a[1] - b[1])
                .map(([key, value]) => (
                  <EvalsCard key={key} keyName={key} value={value} />
                ))}
            </Stack>
          </ShowComponent>
          <ShowComponent
            condition={
              deterministicEvals?.length > 0 && Object.keys(evals)?.length > 0
            }
          >
            <Divider
              sx={{
                my: 2,
              }}
            />
          </ShowComponent>
          <Grid container columnSpacing={2}>
            {deterministicEvals.map((item, index) => {
              const isEven = index % 2 === 0;
              const isLastItem = index === deterministicEvals.length - 1;
              const showBorder =
                isEven && !(isLastItem && deterministicEvals.length % 2 !== 0);

              return (
                <Grid
                  item
                  xs={12}
                  md={6}
                  key={item.id}
                  sx={{
                    borderRight: showBorder ? "1px solid" : "none",
                    paddingRight: isEven ? 1.5 : 0,
                    borderColor: "divider",
                    my: 2,
                  }}
                >
                  <DeterministicEvaluationChart
                    title={item.title}
                    data={item.data}
                  />
                </Grid>
              );
            })}
          </Grid>
        </>
      )}
    </CardWrapper>
  );
}

EvaluationMetrics.propTypes = {
  data: PropTypes.object.isRequired,
  expanded: PropTypes.bool,
  isPending: PropTypes.bool,
  status: PropTypes.string,
};
