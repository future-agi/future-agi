import React, { useMemo } from "react";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import AnnotationChartHeaderItem from "./AnnotationChartHeaderItem";
import {
  categoricalGraphHeader,
  numericGraphHeader,
  textGraphHeader,
  thumbsGraphHeader,
  starGraphHeader,
} from "./annotationChartHeaderkeys";

const AnnotationChartHeader = ({ chartHeading, item = { type: "" } }) => {
  const headerData = useMemo(() => {
    let data = [];
    if (item.type === "categorical") {
      data = categoricalGraphHeader.map((temp) => ({
        ...temp,
        value: item[temp.valueKey],
        missing: temp.missing,
      }));
    }
    if (item.type === "numeric") {
      data = numericGraphHeader.map((temp) => ({
        ...temp,
        value: item[temp.valueKey],
        missing: temp.missing,
      }));
    }
    if (item.type === "text") {
      data = textGraphHeader.map((temp) => ({
        ...temp,
        value: item[temp.valueKey],
        missing: temp.missing,
      }));
    }
    if (item.type === "thumb") {
      data = thumbsGraphHeader.map((temp) => ({
        ...temp,
        value: item[temp.valueKey],
        missing: temp.missing,
      }));
    }
    if (item.type === "star") {
      data = starGraphHeader.map((temp) => ({
        ...temp,
        value: item[temp.valueKey],
        missing: temp.missing,
      }));
    }
    return data;
  }, [item]);

  return (
    <Box display="flex" flexDirection={"column"} gap={2}>
      <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
        {chartHeading}
      </Typography>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
        {headerData.map((item, index) => {
          return (
            <Box key={index} sx={{ width: "calc(50% - 8px)" }}>
              <AnnotationChartHeaderItem
                title={item.text}
                value={item.value}
                missing={item.missing}
              />
            </Box>
          );
        })}
      </Box>
    </Box>
  );
};

export default AnnotationChartHeader;

AnnotationChartHeader.propTypes = {
  chartHeading: PropTypes.string,
  item: PropTypes.object,
};
