import React, { useMemo } from "react";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import EmptyCard from "./EmptyCard";
import { useRunAnnotationsStore } from "../states";
import AnnotatorPerformace from "./AnnotatorPerformace";
import SvgColor from "src/components/svg-color";
import AnnotationsChartsRenderer from "./AnnotationsChartsRenderer";
import { useParams } from "react-router";
import { camelizeKeys, useAnnotationSummary } from "./summaryApi";
import AnnotationCardLoading from "./Loaders/AnnotationCardLoading";

// Utility function to convert seconds to human-readable format
const formatTimeFromSeconds = (seconds) => {
  if (!seconds || seconds === 0) return "0 seconds";

  const units = [
    { name: "year", seconds: 365 * 24 * 60 * 60 },
    { name: "month", seconds: 30 * 24 * 60 * 60 },
    { name: "day", seconds: 24 * 60 * 60 },
    { name: "hour", seconds: 60 * 60 },
    { name: "minute", seconds: 60 },
    { name: "second", seconds: 1 },
  ];

  let response = "";
  let remainingSeconds = seconds;
  let unitCount = 0;
  const maxUnits = 2;

  for (const unit of units) {
    if (unitCount >= maxUnits) break;

    const value = Math.floor(remainingSeconds / unit.seconds);
    if (value >= 1) {
      response += ` ${value} ${unit.name}${value > 1 ? "s" : ""}`;
      remainingSeconds = remainingSeconds % unit.seconds;
      unitCount++;
    }
  }

  return response.trim();
};

const headerData = [
  {
    id: "1",
    icon: "/assets/icons/summary/database.svg",
    title: "Dataset coverage",
    valueKey: "datasetCoverage",
    unit: "%",
    backgroundColor: "orange.o5",
    color: "orange.500",
  },
  {
    id: "2",
    icon: "/assets/icons/summary/overall-agreement.svg",
    title: "Overall agreement",
    valueKey: "overallAgreement",
    unit: "",
    backgroundColor: "pink.o5",
    color: "pink.500",
  },
  {
    id: "3",
    icon: "/assets/icons/summary/timmer.svg",
    title: "Completion ETA",
    valueKey: "completionEta",
    unit: "",
    backgroundColor: "blue.o5",
    color: "blue.500",
  },
];

const AnnotationCard = (props) => {
  const {
    setCurrentTab,
    datasetId,
    summary,
    showHeader = true,
    hideEmpty = false,
  } = props;
  const { setOpenRunAnnotations } = useRunAnnotationsStore();
  const { dataset } = useParams();

  const activeDatasetId = datasetId || dataset;

  // The API is snake_case (R6): camelize the keys the charts read, but keep
  // each label's graph_data keys (they are label values, e.g. "Not sure").
  const query = useAnnotationSummary(activeDatasetId);
  const raw = summary ?? query.data;
  const isPending = summary ? false : query.isPending;
  const isLoading = summary ? false : query.isLoading;
  const { header, annotators, labels } = useMemo(() => {
    if (!raw) return { header: {}, annotators: [], labels: [] };
    return {
      header: camelizeKeys(raw.header ?? {}),
      annotators: camelizeKeys(raw.annotators ?? []),
      labels: (raw.labels ?? []).map((l) => {
        const label = {
          ...camelizeKeys({ ...l, graph_data: undefined }),
          graphData: l.graph_data,
        };
        if (label.labelCoverage != null)
          label.labelCoverage = `${label.labelCoverage}%`;
        if (label.type === "star" && l.graph_data) {
          const entries = Object.entries(l.graph_data);
          const total = entries.reduce((s, [, n]) => s + n, 0);
          const happy = (l.graph_data["4"] ?? 0) + (l.graph_data["5"] ?? 0);
          if (total) {
            label.satisfactionRate = `${Math.round((happy / total) * 100)}%`;
            label.modeValue =
              label.modeValue ??
              `${entries.sort((a, b) => b[1] - a[1])[0][0]} stars`;
          }
        }
        return label;
      }),
    };
  }, [raw]);

  const isEmpty = useMemo(() => {
    if (
      annotators?.length > 0 ||
      labels?.length > 0 ||
      header?.datasetCoverage ||
      header?.overallAgreement ||
      header?.completionEta
    ) {
      return false;
    }
    return true;
  }, [header, annotators, labels]);

  if (isPending || isLoading) {
    return <AnnotationCardLoading />;
  }

  return (
    <Box display={"flex"} gap={2} flexDirection={"column"} height="97%">
      <ShowComponent condition={!isEmpty}>
        <Box display={showHeader ? "flex" : "none"} gap={2}>
          {headerData.map((item) => {
            return (
              <Box
                key={item.id}
                sx={{
                  flex: 1,
                  display: "flex",
                  gap: "12px",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "4px",
                  padding: "12px",
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    width: "70px",
                    height: "70px",
                    backgroundColor: item.backgroundColor,
                    padding: "12px",
                    borderRadius: "8px",
                  }}
                >
                  <SvgColor
                    // @ts-ignore
                    src={item.icon}
                    sx={{
                      backgroundColor: item.color,
                      width: "32px",
                      height: "32px",
                    }}
                  />
                </Box>
                <Box display={"flex"} flexDirection={"column"} gap={"2px"}>
                  <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
                    {item.title}
                  </Typography>
                  <Typography
                    typography={"l1"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    {item.valueKey === "completionEta" &&
                    header?.[item?.valueKey]
                      ? formatTimeFromSeconds(header[item.valueKey])
                      : header?.[item?.valueKey] ?? "N/A"}
                    {item.valueKey !== "completionEta" &&
                    (header?.[item?.valueKey] || header?.[item?.valueKey] == 0)
                      ? item.unit
                      : ""}
                  </Typography>
                </Box>
              </Box>
            );
          })}
        </Box>
        <AnnotationsChartsRenderer labels={labels} names={annotators} />
        <AnnotatorPerformace annotatorList={annotators} />
      </ShowComponent>
      <ShowComponent condition={isEmpty && !hideEmpty}>
        <EmptyCard
          tab={"annotation"}
          setCurrentTab={setCurrentTab}
          action={() => setOpenRunAnnotations(true)}
          datasetId={datasetId}
          icon="/assets/icons/summary/empty-annotation.svg"
          title="No annotation labels added"
          description="To review annotation scores and better understand your data, add annotation labels"
        />
      </ShowComponent>
    </Box>
  );
};

export default AnnotationCard;

AnnotationCard.propTypes = {
  setCurrentTab: PropTypes.func,
  summary: PropTypes.object,
  showHeader: PropTypes.bool,
  // no empty state when the page already shows human review (judge reviews)
  hideEmpty: PropTypes.bool,
  datasetId: PropTypes.string,
  selectedColumns: PropTypes.array,
  datasetIndex: PropTypes.number,
};
