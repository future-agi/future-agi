import { Box, IconButton, Typography, useTheme } from "@mui/material";
import React, { useMemo } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useWorkbenchMetrics } from "./context/WorkbenchMetricsContext";
import CustomTooltip from "src/components/tooltip";
import SvgColor from "src/components/svg-color";

const MetricsHeaderSection = () => {
  const theme = useTheme();
  const {
    activeTab,
    searchQuery,
    setSearchQuery,
    setIsFilterDrawerOpen,
    filters,
  } = useWorkbenchMetrics();

  const searchBarStyle = useMemo(
    () => ({
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      marginBottom: theme.spacing(2),
    }),
    [theme],
  );
  const searchFieldStyle = useMemo(
    () => ({
      minWidth: "400px",
    }),
    [],
  );
  const handleSearchChange = (e) => {
    setSearchQuery(e.target.value);
  };

  const iconStyles = useMemo(
    () => ({
      width: 20,
      height: 20,
      color: "text.primary",
    }),
    [],
  );

  const appliedFilterCount = useMemo(() => {
    if (!filters?.length) return 0;
    return filters.filter((f) => {
      const value = f?.filter_config?.filter_value;
      return (
        f?.column_id ||
        (value !== "" && !(Array.isArray(value) && value.length === 0))
      );
    }).length;
  }, [filters]);

  return (
    <Box sx={searchBarStyle}>
      <FormSearchField
        defaultValue=""
        size="small"
        placeholder="Search"
        sx={{
          ...searchFieldStyle,
          visibility: activeTab === "Metrics" ? "hidden" : "visible",
        }}
        value={searchQuery}
        onChange={handleSearchChange}
      />
      <CustomTooltip show title="Filter" arrow size="small">
        <IconButton
          sx={{
            padding: theme.spacing(2),
            paddingX: theme.spacing(3),
            borderRadius: "4px",
            backgroundColor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            gap: theme.spacing(0.5),
            height: "30px",
          }}
          onClick={() => setIsFilterDrawerOpen(true)}
        >
          <SvgColor
            src={`/assets/icons/components/ic_newfilter.svg`}
            sx={iconStyles}
          />
          <Typography
            typography="s2"
            fontWeight={"fontWeightSemiBold"}
            color={"text.primary"}
          >
            Filter {appliedFilterCount > 0 && ` (${appliedFilterCount})`}
          </Typography>
        </IconButton>
      </CustomTooltip>
    </Box>
  );
};

export default MetricsHeaderSection;
