import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { Box, IconButton, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";

const CustomHeader = (props) => {
  const { displayName, showColumnMenu, column, progressSort } = props;
  const refButton = useRef(null);
  // R2D-04: clicking the header text sorts (asc → desc → none), like the
  // column menu; the arrow and aria-sort follow the column's sort.
  const sortable = Boolean(column?.getColDef?.()?.sortable);
  const [sort, setSort] = useState(column?.getSort?.() ?? null);
  useEffect(() => {
    if (!column?.addEventListener) return undefined;
    const onSort = () => setSort(column.getSort?.() ?? null);
    column.addEventListener("sortChanged", onSort);
    return () => column.removeEventListener?.("sortChanged", onSort);
  }, [column]);
  const scheduleIconItems = ["Start Time", "Latency", "End Time", "Duration"];
  const isScheduleIcon = scheduleIconItems.includes(displayName);
  const renderIcon = () => {
    if (
      props?.group ||
      props?.column?.colDef?.col?.groupBy === "Evaluation Metrics"
    ) {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify
            icon="material-symbols:check-circle-outline"
            color="green.500"
          />
        </Box>
      );
    } else if (props?.displayName === "Node Type") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify icon="ph:tree-view" color="text.secondary" />
        </Box>
      );
    } else if (props?.displayName === "User Email") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify icon="mage:email" color="text.primary" />
        </Box>
      );
    } else if (props?.displayName === "User Phone Number") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify icon="line-md:phone" color="text.primary" />
        </Box>
      );
    } else if (isScheduleIcon) {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify
            icon="material-symbols:schedule-outline"
            width="16px"
            height="16px"
            color="text.secondary"
          />
        </Box>
      );
    } else if (props?.displayName === "Total Tokens") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify icon="system-uicons:coins" color="text.disabled" />
        </Box>
      );
    } else if (props?.displayName === "Total Cost") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <SvgColor
            // @ts-ignore
            src={`/assets/icons/components/ic_cost.svg`}
            sx={{ width: 16, height: 16 }}
          />
        </Box>
      );
    } else if (props?.displayName === "Total Traces") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify icon="tabler:list-tree" color="text.disabled" />
        </Box>
      );
    }
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
        }}
      >
        <SvgColor
          src="/assets/icons/ic_col_header.svg"
          sx={{
            height: 20,
            width: 20,
          }}
        />
      </Box>
    );
  };

  const handleMenuClick = () => {
    if (showColumnMenu) {
      showColumnMenu(refButton.current);
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
      }}
    >
      <Box
        role={sortable ? "button" : undefined}
        tabIndex={sortable ? 0 : undefined}
        aria-sort={
          sort === "asc" ? "ascending" : sort === "desc" ? "descending" : "none"
        }
        title={sortable ? `Sort by ${displayName}` : undefined}
        onClick={(e) => sortable && progressSort?.(e.shiftKey)}
        onKeyDown={(e) => {
          if (sortable && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            progressSort?.(e.shiftKey);
          }
        }}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          minWidth: 0,
          cursor: sortable ? "pointer" : "default",
          "&:hover .sort-hint": { opacity: 1 },
        }}
      >
        {renderIcon()}
        <Typography
          variant="s1"
          fontWeight={"fontWeightMedium"}
          color={sort ? "text.primary" : "text.secondary"}
          noWrap
        >
          {displayName}
        </Typography>
        {sortable && (
          <Iconify
            className="sort-hint"
            icon={sort === "asc" ? "mdi:arrow-up" : "mdi:arrow-down"}
            width={14}
            sx={{
              color: sort ? "primary.main" : "text.disabled",
              opacity: sort ? 1 : 0,
              transition: "opacity 120ms",
              flexShrink: 0,
            }}
          />
        )}
      </Box>
      <IconButton
        size="small"
        ref={refButton}
        onClick={handleMenuClick}
        aria-label={`Show menu for ${displayName} column`}
        title={`Column menu for ${displayName}`}
      >
        <Iconify icon="mdi:dots-vertical" />
      </IconButton>
    </Box>
  );
};

// Validate Props
CustomHeader.propTypes = {
  displayName: PropTypes.string.isRequired,
  column: PropTypes.object.isRequired,
  api: PropTypes.object.isRequired,
  menuIcon: PropTypes.string,
  dataType: PropTypes.string,
  group: PropTypes.bool,
  showColumnMenu: PropTypes.func.isRequired,
  progressSort: PropTypes.func,
};

export default CustomHeader;
