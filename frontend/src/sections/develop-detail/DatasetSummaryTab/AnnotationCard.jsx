import React, { useMemo } from "react";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import EmptyCard from "./EmptyCard";
import { useRunAnnotationsStore } from "../states";
import AnnotatorPerformace from "./AnnotatorPerformace";
import SvgColor from "src/components/svg-color";
import AnnotationsChartsRenderer from "./AnnotationsChartsRenderer";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
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
    backgroundColor: "status.warning.bg",
    color: "status.warning.fg",
  },
  {
    id: "2",
    icon: "/assets/icons/summary/overall-agreement.svg",
    title: "Overall agreement",
    valueKey: "overallAgreement",
    unit: "",
    backgroundColor: "status.discovery.bg",
    color: "status.discovery.fg",
  },
  {
    id: "3",
    icon: "/assets/icons/summary/timmer.svg",
    title: "Completion ETA",
    valueKey: "completionEta",
    unit: "",
    backgroundColor: "status.info.bg",
    color: "status.info.fg",
  },
];

const AnnotationCard = (props) => {
  const { setCurrentTab, datasetId } = props;
  const { setOpenRunAnnotations } = useRunAnnotationsStore();
  const { dataset } = useParams();

  const activeDatasetId = datasetId || dataset;

  const { data, isPending, isLoading } = useQuery({
    queryKey: ["annotation-summary", activeDatasetId],
    queryFn: () =>
      axios.get(
        endpoints.dataset.annotationSummary(activeDatasetId),
        // {
        //   ...(selectedColumns?.length > 0 && {
        //     params: { column_ids: selectedColumns.join(",") },
        //   }),
        // }
      ),
    select: (e) => e?.data?.result || [],
    enabled: Boolean(activeDatasetId),
  });

  const { header, annotators, labels } = data || {
    header: {},
    annotators: [],
    labels: [],
  };

  const isEmpty = useMemo(() => {
    if (
      annotators?.length > 0 ||
      labels?.length > 0 ||
      header?.datasetCoverage ||
      header?.overallAgreement ||
      header?.completionETA
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
        <Box display="flex" gap={2}>
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
      <ShowComponent condition={isEmpty}>
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
  datasetId: PropTypes.string,
  selectedColumns: PropTypes.array,
  datasetIndex: PropTypes.number,
};
