import React, { useMemo } from "react";
import { Box } from "@mui/material";
import Iconify from "src/components/iconify";
import AnnotationValueCellRenderer from "src/components/traceDetailDrawer/CustomRenderer/AnnotationValueCellRenderer";

export const useAnnotationsColumnDefs = () => {
  return useMemo(
    () => [
      {
        headerName: "Annotation Name",
        field: "annotationName",
        flex: 1,
        headerStyle: {
          fontWeight: "600",
          fontSize: "16px",
          color: "text.disabled",
        },
      },
      {
        headerName: "Value",
        field: "value",
        flex: 1,
        headerStyle: {
          fontWeight: "600",
          fontSize: "16px",
          color: "text.disabled",
        },
        cellRenderer: AnnotationValueCellRenderer,
      },
      {
        headerName: "Annotated By",
        field: "updatedBy",
        flex: 1,
        headerStyle: {
          fontWeight: "600",
          fontSize: "16px",
          color: "var(--text-secondary)",
        },
        cellRenderer: (params) => (
          <Box display="flex" alignItems="center" height="100%" width="100%">
            <Box
              flexShrink={0} // Prevent icon from shrinking
              display="flex"
              alignItems="center"
              mr={1}
            >
              <Iconify
                icon="solar:user-circle-linear"
                color="text.disabled"
                width={24}
                height={24}
              />
            </Box>
            <Box
              flex={1}
              overflow="hidden"
              textOverflow="ellipsis"
              whiteSpace="nowrap"
            >
              {params.value}
            </Box>
          </Box>
        ),
      },
    ],
    [],
  );
};

export const MapTypeToName = {
  thumbs_up_down: "Thumbs up & Down",
  star: "Star",
  numeric: "Number",
  text: "Text",
  categorical: "Categorical",
};

export const columnOptions = [
  { key: "latency", label: "Latency", visible: true },
  { key: "tokens", label: "Tokens", visible: false },
  { key: "cost", label: "Cost", visible: true },
  { key: "evals", label: "Evals", visible: false },
  { key: "annotations", label: "Annotations", visible: false },
  { key: "events", label: "Events", visible: false },
];
