import React, { useMemo, useRef } from "react";
import {
  Badge,
  Box,
  Button,
  Divider,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import useUsersStore from "./Store/usersStore";
import CustomTooltip from "src/components/tooltip";
import SvgColor from "src/components/svg-color";
import { UsersColumnConfigureDropDown } from "./UsersColumnConfigureDropDown";
import { getComplexFilterValidation } from "src/components/ComplexFilter/common";
import { useDebounce } from "src/hooks/use-debounce";
import { userDefaultFilter } from "./common";

export const UsersHeaderActions = () => {
  const theme = useTheme();
  const columnConfigureRef = useRef(null);
  const {
    selectedRowsData,
    clearSelection,
    toggleColumnPanel,
    toggleOpenUserListFilter,
    filters,
  } = useUsersStore();

  const wrapperStyle = useMemo(
    () => ({
      display: "flex",
      alignItems: "center",
      gap: theme.spacing(2),
    }),
    [theme],
  );

  const selectedBoxStyle = useMemo(
    () => ({
      display: "flex",
      border: "1px solid",
      borderColor: "divider",
      borderRadius: "4px",
      paddingLeft: theme.spacing(1.5),
      paddingY: theme.spacing(0.5),
      alignItems: "center",
      gap: theme.spacing(1),
      marginRight: theme.spacing(1.5),
      height: "38px",
    }),
    [theme],
  );

  const dividerStyle = useMemo(
    () => ({
      borderRightWidth: "1px",
      marginLeft: theme.spacing(1),
      height: "20px",
      mt: theme.spacing(0.5),
    }),
    [theme],
  );

  const cancelButtonStyle = useMemo(
    () => ({
      display: "flex",
      alignItems: "center",
      gap: theme.spacing(1),
      pr: theme.spacing(1.5),
      cursor: "pointer",
    }),
    [theme],
  );

  const iconButtonStyle = useMemo(
    () => ({
      color: "text.primary",
    }),
    [],
  );

  const iconStyles = useMemo(
    () => ({
      width: 18,
      height: 18,
    }),
    [],
  );

  const refs = useRef({
    previousValidatedFilters: [],
  });

  const validatedFilters = useMemo(() => {
    if (!Array.isArray(filters)) return [];

    const flatFilters = filters.flat();
    const newValidatedFilters = flatFilters
      .map((filter) => {
        const result = getComplexFilterValidation(true).safeParse(filter);
        return result.success ? result.data : false;
      })
      .filter(Boolean);

    if (
      JSON.stringify(refs.current.previousValidatedFilters) ===
      JSON.stringify(newValidatedFilters)
    ) {
      return refs.current.previousValidatedFilters;
    }

    refs.current.previousValidatedFilters = newValidatedFilters;
    return newValidatedFilters;
  }, [JSON.stringify(filters)]);

  const debouncedValidatedFilters = useDebounce(validatedFilters, 500);

  // Calculate if filters are applied without useEffect
  const isFilterApplied = useMemo(() => {
    const hasValidFilters =
      debouncedValidatedFilters && debouncedValidatedFilters.length > 0;
    const hasNonDefaultFilters = filters.some((filter) => {
      const filterConfig = filter.filter_config || {};
      const defaultConfig = userDefaultFilter.filter_config || {};

      return (
        filter.column_id !== userDefaultFilter.column_id ||
        filterConfig.filter_op !== defaultConfig.filter_op ||
        filterConfig.filter_value !== defaultConfig.filter_value ||
        filterConfig.filter_type !== defaultConfig.filter_type ||
        (filterConfig.filter_value && filterConfig.filter_value.length > 0)
      );
    });

    return hasValidFilters || hasNonDefaultFilters;
  }, [debouncedValidatedFilters, filters]);

  if (selectedRowsData.length > 0) {
    return (
      <Box sx={wrapperStyle}>
        <Box sx={selectedBoxStyle}>
          <Typography fontWeight="500" typography="s1" color="primary.main">
            {selectedRowsData.length} Selected
          </Typography>
          <Divider orientation="vertical" flexItem sx={dividerStyle} />
          <Button size="small" sx={cancelButtonStyle} onClick={clearSelection}>
            Cancel
          </Button>
        </Box>
      </Box>
    );
  }

  return (
    <>
      <Box display={"flex"} paddingX={1.5} gap={2}>
        <CustomTooltip
          show={true}
          title="Filters"
          placement="bottom"
          arrow
          size="small"
        >
          <IconButton
            size="small"
            sx={iconButtonStyle}
            onClick={toggleOpenUserListFilter}
          >
            {isFilterApplied ? (
              <Badge
                variant="dot"
                color="error"
                overlap="circular"
                anchorOrigin={{ vertical: "top", horizontal: "right" }}
                sx={{ "& .MuiBadge-badge": { top: 1, right: 1 } }}
              >
                <SvgColor
                  src={`/assets/icons/components/ic_newfilter.svg`}
                  sx={iconStyles}
                />
              </Badge>
            ) : (
              <SvgColor
                src={`/assets/icons/components/ic_newfilter.svg`}
                sx={iconStyles}
              />
            )}
          </IconButton>
        </CustomTooltip>
        <Divider
          orientation="vertical"
          flexItem
          sx={{ my: theme.spacing(1) }}
        />
        <CustomTooltip
          show
          title="Columns"
          placement="bottom"
          arrow
          size="small"
        >
          <IconButton
            size="small"
            sx={iconButtonStyle}
            onClick={toggleColumnPanel}
            ref={columnConfigureRef}
          >
            <SvgColor
              src="/assets/icons/action_buttons/ic_column.svg"
              sx={iconStyles}
            />
          </IconButton>
        </CustomTooltip>
      </Box>
      <UsersColumnConfigureDropDown anchorRef={columnConfigureRef} />
    </>
  );
};

export default UsersHeaderActions;
