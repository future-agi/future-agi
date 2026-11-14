import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Chip, Divider, Skeleton, Stack, Typography } from "@mui/material";
import HeadingAndSubheading from "../HeadingAndSubheading/HeadingAndSubheading";
import { ShowComponent } from "../show/ShowComponent";
import { useSelectedNode } from "./useSelectedNode";
import { getObservationSpanById, useTraceErrorAnalysis } from "./common";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import { camelCaseToTitleCase } from "src/utils/utils";
import ScoreChip from "./ScoreChip";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useFeedDetailStore } from "src/pages/dashboard/feed/store/store";

const ErrorAnalysis = ({ traceId, traceDetail, defaultExpanded = false }) => {
  const { data: traceErrorAnalysis, isPending: isPendingTraceErrorAnalysis } =
    useTraceErrorAnalysis(traceId);
  const { errorName } = useFeedDetailStore();
  const { errors, summary } = traceErrorAnalysis ?? {
    errors: [],
    summary: "",
  };

  useEffect(() => {
    if (!isPendingTraceErrorAnalysis) {
      trackEvent(Events.observeTraceidClicked, {
        [PropertyName.id]: traceId,
        [PropertyName.toggle]: traceErrorAnalysis?.analysisExists,
      });
    }
  }, [isPendingTraceErrorAnalysis, traceId, traceErrorAnalysis]);
  const [activeIndex, setActiveIndex] = useState(null);

  const { setSelectedNode } = useSelectedNode();
  const treeData = traceDetail?.observation_spans;

  useEffect(() => {
    if (!errors?.length || !treeData) return;

    // make sure activeIndex is initialized from errorName
    let index = activeIndex;
    if (index === null) {
      const matchIndex = errors?.findIndex(
        (err) => err?.category === errorName,
      );
      index = matchIndex >= 0 ? matchIndex : 0;
      setActiveIndex(index);
    }

    // whenever index changes, update span
    const spans = errors?.[index]?.locationSpans;
    if (spans?.length) {
      const firstSpan = getObservationSpanById(treeData, spans[0]);
      const sessionId = traceDetail?.trace?.session;
      if (firstSpan) {
        setSelectedNode({ ...firstSpan, sessionId });
        return;
      }
    }
  }, [errors, errorName, activeIndex, treeData, setSelectedNode]);

  return isPendingTraceErrorAnalysis ? (
    <Skeleton height={300} variant="rectangular" sx={{ borderRadius: "4px" }} />
  ) : (
    <Box display="flex" flexDirection="column" gap={1.5}>
      <Typography
        typography="s2_1"
        sx={{
          fontWeight: 600,
          display: "flex",
          flexDirection: "row",
          gap: 1,
        }}
      >
        Trace ID:{" "}
        <Typography typography="s2_1">{traceDetail?.trace?.id}</Typography>
      </Typography>

      <Accordion
        onChange={(_, expanded) => {
          trackEvent(
            expanded
              ? Events.agixTraceInsightsOpened
              : Events.agixTraceInsightsClosed,
            {
              [PropertyName.id]: traceDetail?.trace?.id,
            },
          );
        }}
        defaultExpanded={defaultExpanded}
        disableGutters
      >
        <AccordionSummary
          sx={{
            flexDirection: "row",
            justifyContent: "baseline",
            borderRadius: 0,
            pr: 3,
            bgcolor: "background.neutral",

            borderColor: "divider",
          }}
        >
          <Box>
            <Typography typography="m3" fontWeight={500} mb={0.5}>
              Scores
            </Typography>
            <Box display={"flex"} gap={2} flexDirection={"row"}>
              {Object.entries(traceErrorAnalysis?.scores ?? {}).map(
                ([key, value]) => (
                  <ScoreChip
                    key={key}
                    score={value.score}
                    description={value.reason}
                    total={5}
                    label={camelCaseToTitleCase(key)}
                    onClick={() => {
                      trackEvent(Events.agixMetricChipClicked, {
                        [PropertyName.id]: traceDetail?.trace?.id,
                        [PropertyName.name]: camelCaseToTitleCase(key),
                      });
                    }}
                  />
                ),
              )}
            </Box>
          </Box>
        </AccordionSummary>
        <AccordionDetails
          sx={{
            px: 3,
            bgcolor: "background.neutral",
          }}
        >
          <Box
            display={"flex"}
            gap={1}
            borderRadius={1}
            border={1}
            p={2}
            borderColor="divider"
            bgcolor="background.paper"
          >
            <Box display="flex" flexDirection="column" gap={2}>
              <ShowComponent condition={errors?.length > 0}>
                <Stack direction="row" gap={1}>
                  {errors?.map((error, index) => (
                    <Typography
                      key={error.errorId}
                      typography="s2_1"
                      fontWeight={500}
                      color="text.secondary"
                      onClick={() => setActiveIndex(index)}
                      sx={{
                        cursor: "pointer",
                        borderRadius: 1,
                        border: "1px solid",
                        borderColor: "text.secondary",
                        py: 0.5,
                        px: 1.5,
                        "&:hover": {
                          bgcolor: "divider",
                        },
                        ...(activeIndex === index && {
                          border: "1px solid",
                          borderColor: "primary.main",
                          color: "primary.main",
                          bgcolor: "action.hover",
                          "&:hover": {
                            borderColor: "primary.main",
                            color: "primary.main",
                            bgcolor: "action.hover",
                          },
                        }),
                      }}
                    >
                      {error?.category}
                    </Typography>
                  ))}
                </Stack>
              </ShowComponent>
              <Stack direction="column" gap={2} maxHeight={200} overflow="auto">
                <ShowComponent
                  condition={errors?.[activeIndex]?.recommendation?.length > 0}
                >
                  <HeadingAndSubheading
                    heading={
                      <Typography
                        typography="s2"
                        fontWeight={500}
                        color="text.primary"
                      >
                        Recommendation
                      </Typography>
                    }
                    subHeading={
                      <Typography
                        typography="s2_1"
                        sx={{
                          backgroundColor: "status.success.bg",
                          py: 1,
                          px: 1.5,
                          borderRadius: "4px",
                        }}
                        color="text.primary"
                      >
                        {errors?.[activeIndex]?.recommendation}
                      </Typography>
                    }
                  />
                </ShowComponent>
                <ShowComponent condition={errors?.[activeIndex]?.immediateFix}>
                  <HeadingAndSubheading
                    heading={
                      <Typography
                        typography="s2"
                        fontWeight={500}
                        color="text.primary"
                      >
                        Immediate Fix
                      </Typography>
                    }
                    subHeading={
                      <Typography typography="s2_1" color="text.primary">
                        {errors?.[activeIndex]?.immediateFix}
                      </Typography>
                    }
                  />
                </ShowComponent>
                <ShowComponent condition={summary?.insights}>
                  <HeadingAndSubheading
                    heading={
                      <Typography
                        typography="s2"
                        fontWeight={500}
                        color="text.primary"
                      >
                        Insights
                      </Typography>
                    }
                    subHeading={
                      <Typography typography="s2_1" color="text.primary">
                        {summary?.insights}
                      </Typography>
                    }
                  />
                </ShowComponent>
                <ShowComponent condition={errors?.[activeIndex]?.description}>
                  <HeadingAndSubheading
                    heading={
                      <Typography
                        typography="s2"
                        fontWeight={500}
                        color="text.primary"
                      >
                        Description
                      </Typography>
                    }
                    subHeading={
                      <Typography typography="s2_1" color="text.primary">
                        {errors?.[activeIndex]?.description}
                      </Typography>
                    }
                  />
                </ShowComponent>
                <ShowComponent
                  condition={
                    errors?.[activeIndex]?.evidenceSnippets?.length > 0
                  }
                >
                  <HeadingAndSubheading
                    heading={
                      <Typography
                        typography="s2"
                        fontWeight={500}
                        color="text.primary"
                      >
                        Evidence
                      </Typography>
                    }
                    subHeading={
                      <ol
                        style={{
                          paddingLeft: 16,
                          display: "flex",
                          flexDirection: "column",
                          gap: 4,
                          margin: 0,
                        }}
                      >
                        {errors?.[activeIndex]?.evidenceSnippets?.map(
                          (snippet, index) => (
                            <li key={index}>
                              <Typography
                                typography="s2_1"
                                color="text.primary"
                              >
                                {snippet}
                              </Typography>
                            </li>
                          ),
                        )}
                      </ol>
                    }
                  />
                </ShowComponent>
                <ShowComponent
                  condition={errors?.[activeIndex]?.rootCauses?.length > 0}
                >
                  <HeadingAndSubheading
                    heading={
                      <Typography
                        typography="s2"
                        fontWeight={500}
                        color="text.primary"
                      >
                        Root Causes
                      </Typography>
                    }
                    subHeading={
                      <ol
                        style={{
                          paddingLeft: 16,
                          display: "flex",
                          flexDirection: "column",
                          gap: 4,
                          margin: 0,
                        }}
                      >
                        {errors?.[activeIndex]?.rootCauses?.map(
                          (cause, index) => (
                            <li key={index}>
                              <Typography
                                typography="s2_1"
                                color="text.primary"
                              >
                                {cause}
                              </Typography>
                            </li>
                          ),
                        )}
                      </ol>
                    }
                  />
                </ShowComponent>
              </Stack>
              <Divider />
              <ShowComponent
                condition={errors?.[activeIndex]?.locationSpans?.length > 0}
              >
                <Stack direction="column" gap={1}>
                  <Typography
                    typography="s2"
                    fontWeight={500}
                    color="text.primary"
                  >
                    Spans
                  </Typography>
                  <Box display="flex" flexDirection="row" gap={1}>
                    {errors?.[activeIndex]?.locationSpans?.map(
                      (span, index) => {
                        const spanData = getObservationSpanById(treeData, span);
                        return (
                          <Chip
                            key={index}
                            color="primary"
                            onClick={() => {
                              setSelectedNode(spanData);
                              trackEvent(Events.agixMetricChipClicked, {
                                [PropertyName.id]: traceDetail?.trace?.id,
                                [PropertyName.id]: span,
                              });
                            }}
                            variant="outlined"
                            label={spanData?.name}
                          />
                        );
                      },
                    )}
                  </Box>
                </Stack>
              </ShowComponent>
            </Box>
          </Box>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
};

export default ErrorAnalysis;

ErrorAnalysis.propTypes = {
  traceId: PropTypes.string,
  traceDetail: PropTypes.object,
  defaultExpanded: PropTypes.bool,
};
