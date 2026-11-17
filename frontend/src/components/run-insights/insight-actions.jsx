import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Typography,
  Chip,
  FormControlLabel,
  useTheme,
} from "@mui/material";
import Iconify from "../iconify"; // Assuming this is a custom Icon component
import { LightCheckbox } from "src/sections/project-detail/StyledComponents";
import { ShowComponent } from "../show";

const InsightActions = ({ evalMetrics, setSelectedTraceIds }) => {
  const theme = useTheme();

  const data = useMemo(
    () =>
      Object.entries(evalMetrics || {}).filter(([_, v]) => v?.totalErrorsCount),
    [evalMetrics],
  );

  const [selectedMetrics, setSelectedMetrics] = useState({});

  const handleCheckboxChange = (key, checked) => {
    setSelectedMetrics((prev) => {
      const updated = {
        ...prev,
        [key]: checked,
      };

      // Compute trace ID set from all selected metrics
      const selectedIds = Object.entries(evalMetrics || {})
        .filter(([metricKey, _]) => updated[metricKey])
        .flatMap(([_, value]) => value.failedTraceIds);

      // Remove duplicates
      const uniqueIds = Array.from(new Set(selectedIds));

      setSelectedTraceIds(uniqueIds);

      return updated;
    });
  };

  return (
    <Box>
      <ShowComponent condition={data?.length > 0}>
        {data.map(([key, metric]) => {
          const isSelected = !!selectedMetrics[key];

          return (
            <Box
              key={key}
              sx={{
                py: (theme) => theme.spacing(2),
                borderBottom: "1px solid",
                borderColor: "divider",
                display: "flex",
                flexDirection: "column",
                gap: (theme) => theme.spacing(1.5),
              }}
            >
              {/* Row with Checkbox and No of Samples */}
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  marginLeft: (theme) => theme.spacing(1),
                }}
              >
                <FormControlLabel
                  sx={{
                    "& .MuiButtonBase-root": {
                      padding: "0px !important",
                    },
                  }}
                  control={
                    <LightCheckbox
                      size="small"
                      checked={isSelected}
                      onChange={(_, checked) =>
                        handleCheckboxChange(key, checked)
                      }
                    />
                  }
                  label={
                    <Typography
                      marginLeft={(theme) => theme.spacing(2)}
                      fontWeight="fontWeightRegular"
                      variant="s1"
                      color="text.primary"
                    >
                      No of Samples:{" "}
                      {String(metric?.failedTraceIds.length).padStart(2, "0")}
                    </Typography>
                  }
                />
              </Box>

              <Box
                sx={{
                  display: "flex",
                  gap: (theme) => theme.spacing(1.5),
                  flexWrap: "wrap",
                  marginBottom: (theme) => theme.spacing(1),
                }}
              >
                <Chip
                  avatar={
                    <Iconify
                      icon="solar:danger-triangle-linear"
                      color={`${theme.palette.red[500]} !important`}
                      width={6}
                      sx={{
                        padding: (theme) => theme.spacing(0.6),
                      }}
                    />
                  }
                  label={metric?.name}
                  variant="error"
                  sx={{
                    backgroundColor: "status.danger.bg",
                    typography: "s2",
                    marginLeft: (theme) => theme.spacing(5),
                    color: "status.danger.fg",
                    transition: "none",
                    cursor: "default",
                  }}
                />
              </Box>
            </Box>
          );
        })}
      </ShowComponent>

      <ShowComponent condition={!data?.length}>
        <Box
          sx={{
            display: "flex",
            position: "absolute",
            top: "65%",
            left: "25%",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography
            fontWeight="fontWeightRegular"
            color="text.primary"
            variant="s1"
            sx={{ marginLeft: 1.5 }}
          >
            No abnormal samples detected
          </Typography>
        </Box>
      </ShowComponent>
    </Box>
  );
};

// Prop validation
InsightActions.propTypes = {
  evalMetrics: PropTypes.object,
  setSelectedTraceIds: PropTypes.func,
};

export default InsightActions;
