import {
  Box,
  Chip,
  Divider,
  styled,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";

import { OutlinedButton } from "../ProjectDetailComponents";

import TraceDataList from "./TraceDataList";
import RunTraceTree from "./RunTraceTree";
import AnnotateSection from "./AnnotateSection";
import CompareEvalGrid from "./CompareEvalGrid";

const Section = styled(Box)(({ theme }) => ({
  border: "1.5px solid",
  borderColor: theme.palette.background.neutral,
  borderLeft: "0px",
  borderRight: "0px",
  borderBottom: "0px",
  padding: "14px",
}));

const CustomIconButton = styled(OutlinedButton)(() => ({
  "& .MuiButton-startIcon": {
    margin: 0,
  },
  padding: "2px 8px",
  minWidth: 0,
}));

const CompareSection = ({
  traceData,
  selectedEvals,
  globalTraceOpen,
  globalAnnotateOpen,
  totalRuns,
}) => {
  const theme = useTheme();
  const [isTraceOpen, setIsTraceOpen] = useState(globalTraceOpen);
  const [isAnnotateOpen, setIsAnnotateOpen] = useState(globalAnnotateOpen);
  const [height, setHeight] = useState(300);
  const [isDragging] = useState(false);
  const boxRef = useRef(null);

  const evaluationMetrics = useMemo(() => {
    return traceData?.evalsMetrics || {};
  }, [traceData?.evalsMetrics]);
  const systemMetrics = traceData?.systemMetrics || {};
  const observationSpans = useMemo(() => {
    if (!traceData?.observationSpans) {
      return;
    }
    return traceData?.observationSpans;
  }, [traceData]);
  const annotationLabels = traceData?.annotationLabels || [];
  const annotationValues = traceData?.annotationValues || [];

  useEffect(() => {
    setIsAnnotateOpen(globalAnnotateOpen);
    if (globalAnnotateOpen) {
      setIsTraceOpen(false);
    }
  }, [globalAnnotateOpen]);

  useEffect(() => {
    setIsTraceOpen(globalTraceOpen);
    if (globalTraceOpen) {
      setIsAnnotateOpen(false);
    }
  }, [globalTraceOpen]);

  const toggleOpen = (option, val) => {
    if (option === "annotate") {
      if (!val) {
        return setIsAnnotateOpen(val);
      }
      setIsAnnotateOpen(val);
      setIsTraceOpen(!val);
    } else {
      if (!val) {
        return setIsTraceOpen(val);
      }
      setIsTraceOpen(val);
      setIsAnnotateOpen(!val);
    }
  };

  const toggleHeight = () => {
    setHeight(height > 0 ? 0 : 300);
  };

  const rowData = useMemo(() => {
    const rows = Object.entries(evaluationMetrics).filter(([id]) =>
      selectedEvals.includes(id),
    );
    return rows.map((i) => i[1]);
  }, [evaluationMetrics, selectedEvals]);

  return (
    <Box
      sx={{
        width: totalRuns === 2 ? "50vw" : "570px",
        flexShrink: 0,
        display: "flex",
      }}
    >
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflowX: "hidden",
        }}
      >
        <Section>
          <Typography
            color="text.primary"
            lineHeight={"22px"}
            fontSize="14px"
            fontWeight={600}
          >
            {traceData?.projectVersionName}
          </Typography>
        </Section>

        <Section
          ref={boxRef}
          sx={{
            height: `${height}px`,
            position: "relative",
            display: "flex",
            flexDirection: "column",
            gap: "14px",
            transition: isDragging ? "none" : "height 400ms ease-in-out",
          }}
        >
          <Box
            sx={{
              gap: "14px",
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <Box
              sx={{
                height: "260px",
                overflowY: "auto",
                width: "100%",
              }}
            >
              <CompareEvalGrid rowData={rowData} />
            </Box>
          </Box>
          <Box
            sx={{
              position: "absolute",
              bottom: "-12px",
              left: "10px",
              borderRadius: "50%",
              zIndex: 10,
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              border: "1px solid",
              borderColor: "divider",
              padding: 0.3,
              cursor: "pointer",
              backgroundColor: "background.paper",
            }}
            onClick={toggleHeight}
          >
            <Iconify
              icon="bi:arrows-collapse"
              width={16}
              sx={{ color: "text.disabled" }}
            />
          </Box>
        </Section>

        <Section sx={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          <Typography
            color="text.primary"
            lineHeight={"22px"}
            fontSize="14px"
            fontWeight={500}
          >
            System Metrics:
          </Typography>
          <Box sx={{ display: "flex", gap: "8px" }}>
            <Box sx={{ display: "flex" }}>
              <Chip
                size="small"
                variant="outlined"
                label={
                  <Box
                    sx={{ display: "flex", alignItems: "center", gap: "8px" }}
                  >
                    <Iconify
                      icon="dashicons:money-alt"
                      width="12px"
                      color="text.disabled"
                      sx={{ lineHeight: "18px" }}
                    />
                    Total Cost: ${systemMetrics?.avgCost}
                  </Box>
                }
                sx={{
                  backgroundColor: theme.palette.blue.o10,
                  borderColor: theme.palette.blue[200],
                  color: "text.primary",
                  fontSize: "12px",
                  fontWeight: "400",
                  lineHeight: "18px",
                  height: "22px",
                  borderRadius: "8px",
                  padding: "6px 0px",
                }}
              />
            </Box>
            <Box sx={{ display: "flex" }}>
              <Chip
                size="small"
                variant="outlined"
                label={
                  <Box
                    sx={{ display: "flex", alignItems: "center", gap: "8px" }}
                  >
                    <Iconify
                      icon="solar:clock-circle-linear"
                      width="12px"
                      color="text.disabled"
                      sx={{ lineHeight: "18px" }}
                    />
                    Latency: {systemMetrics?.avgLatencyMs}ms
                  </Box>
                }
                sx={{
                  backgroundColor: theme.palette.orange.o5,
                  borderColor: theme.palette.orange[200],
                  color: "text.primary",
                  fontSize: "12px",
                  fontWeight: "400",
                  lineHeight: "18px",
                  borderRadius: "8px",
                  height: "22px",
                  padding: "6px 0px",
                }}
              />
            </Box>
          </Box>
        </Section>
        <Section sx={{ flex: 1, padding: 0 }}>
          <Box sx={{ display: "flex", height: "100%" }}>
            <ShowComponent condition={isTraceOpen}>
              <RunTraceTree observationSpans={observationSpans} />
            </ShowComponent>
            <ShowComponent condition={isAnnotateOpen}>
              <AnnotateSection
                annotationLabels={annotationLabels}
                annotationValues={annotationValues}
                projectVersionId={traceData.projectVersion}
                traceId={traceData.id}
              />
            </ShowComponent>
            <ShowComponent condition={isTraceOpen || isAnnotateOpen}>
              <Divider orientation="vertical" flexItem />
            </ShowComponent>
            <Box
              sx={{
                flex: 1,
                padding: "14px",
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <Typography
                  color="text.primary"
                  fontSize="14px"
                  lineHeight={"22px"}
                  fontWeight={500}
                >
                  <ShowComponent condition={!isTraceOpen && !isAnnotateOpen}>
                    Chain
                  </ShowComponent>
                </Typography>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <CustomIconButton
                    size="small"
                    variant="outlined"
                    sx={{
                      fontSize: "14px",
                      borderRadius: "4px",
                      backgroundColor: isTraceOpen ? "divider" : "",
                    }}
                    onClick={() => toggleOpen("trace", !isTraceOpen)}
                    startIcon={
                      <Iconify
                        icon="oui:apm-trace"
                        sx={{ color: "text.disabled", marginRight: "5px" }}
                        width={"13px"}
                      />
                    }
                  >
                    Trace
                  </CustomIconButton>
                  <CustomIconButton
                    size="small"
                    variant="outlined"
                    sx={{
                      fontSize: "14px",
                      borderRadius: "4px",
                      backgroundColor: isAnnotateOpen ? "divider" : "",
                    }}
                    onClick={() => toggleOpen("annotate", !isAnnotateOpen)}
                    startIcon={
                      <Iconify
                        icon="line-md:star"
                        sx={{ color: "text.disabled", marginRight: "5px" }}
                        width={"16px"}
                      />
                    }
                  >
                    Annotate
                  </CustomIconButton>
                </Box>
              </Box>
              <TraceDataList traceData={traceData} />
            </Box>
          </Box>
        </Section>
      </Box>
      <Divider orientation="vertical" />
    </Box>
  );
};

CompareSection.propTypes = {
  traceData: PropTypes.object,
  selectedEvals: PropTypes.array,
  globalTraceOpen: PropTypes.bool,
  globalAnnotateOpen: PropTypes.bool,
  totalRuns: PropTypes.number,
};

export default CompareSection;
