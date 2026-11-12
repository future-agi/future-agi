import React, { useState } from "react";
import {
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import { Link, useParams } from "react-router-dom";
import SvgColor from "src/components/svg-color";
import { Events, PropertyName, trackEvent } from "../../../utils/Mixpanel";
import { usePromptStore } from "../store/usePromptStore";

const SORT_OPTIONS = [
  {
    id: "name",
    label: "Name",
    icon: {
      asc: "/assets/icons/ascending-sort-ag-grid.svg",
      desc: "/assets/icons/descending-sort-ag-grid.svg",
    },
  },
  {
    id: "updated_at",
    label: "Last modified",
    icon: {
      asc: "/assets/icons/ascending-sort-ag-grid.svg",
      desc: "/assets/icons/descending-sort-ag-grid.svg",
    },
  },
];

export default function ActionBar({ items = [], setSortConfig, sortConfig }) {
  const hasItems = items.length > 0;
  const { folder } = useParams();
  const _theme = useTheme();
  const totalCount = usePromptStore((s) => s.totalCount);
  const [anchorEl, setAnchorEl] = useState(null);

  const open = Boolean(anchorEl);

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleClick = (event) => {
    event.stopPropagation();
    trackEvent(Events.promptSortClicked, {
      [PropertyName.click]: true,
      [PropertyName.source]: folder,
    });
    setAnchorEl(event.currentTarget);
  };

  const handleSortSelect = (sortOption) => {
    const newDirection =
      sortConfig.field === sortOption.id && sortConfig.direction === "desc"
        ? "asc"
        : "desc";

    const newSortConfig = {
      field: sortOption.id,
      direction: newDirection,
    };

    setSortConfig(newSortConfig);

    // clear any previous timer
    // if (closeTimerRef.current) {
    //   clearTimeout(closeTimerRef.current);
    // }

    // closeTimerRef.current = setTimeout(() => {
    //   handleClose();
    // }, 400);
  };

  // useEffect(() => {
  //   return () => {
  //     if (closeTimerRef.current) {
  //       clearTimeout(closeTimerRef.current);
  //     }
  //   };
  // }, []);

  return (
    <Stack
      direction={"row"}
      justifyContent={"space-between"}
      alignItems={"center"}
      sx={{ px: 2, py: 1.5 }}
    >
      <Stack direction={"row"} alignItems={"center"}>
        <Link
          style={{ textDecoration: "none" }}
          to={`/dashboard/workbench/${folder === "my-templates" ? folder : "all"}`}
        >
          <Typography
            typography={"title_xs"}
            color={!hasItems ? "text.primary" : "text.secondary"}
          >
            {folder === "my-templates" ? "My templates" : "All Prompts"}
          </Typography>
        </Link>
        {items?.map((child, index) => (
          <Stack key={index} direction={"row"} alignItems={"center"}>
            <SvgColor
              sx={{ height: 16, width: 16, color: "text.tertiary" }}
              src="/assets/icons/custom/lucide--chevron-right.svg"
            />
            <Link
              style={{ textDecoration: "none" }}
              to={`/dashboard/workbench/${child?.id}`}
            >
              <Typography typography={"title_xs"} color={"text.primary"}>
                {child?.name}
              </Typography>
            </Link>
          </Stack>
        ))}
        {totalCount !== null && totalCount !== undefined && (
          <Typography
            typography={"s2"}
            fontWeight={"fontWeightRegular"}
            color={"text.tertiary"}
            sx={{ ml: 1.5 }}
          >
            No.of prompts: {totalCount}
          </Typography>
        )}
      </Stack>
      <Button
        size="small"
        sx={{ display: "flex", gap: 1, flexDirection: "row" }}
        onClick={handleClick}
      >
        <Typography
          typography={"s2"}
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
        >
          Sort
        </Typography>
        <SvgColor
          src={"/assets/icons/ic_sort.svg"}
          sx={{ height: 16, width: 16 }}
        />
      </Button>
      <Menu
        anchorEl={anchorEl}
        open={open}
        onClose={handleClose}
        PaperProps={{ sx: { mt: 1, minWidth: 200, p: 0.5 } }}
        transformOrigin={{ horizontal: "right", vertical: "top" }}
        anchorOrigin={{ horizontal: "right", vertical: "bottom" }}
      >
        {SORT_OPTIONS.map((sortOption) => {
          const isSelected = sortConfig.field === sortOption.id;
          const currentDirection = isSelected ? sortConfig.direction : "desc";

          return (
            <MenuItem
              key={sortOption.id}
              sx={{
                px: 1.25,
                py: 0.75,
                backgroundColor: isSelected ? "action.selected" : "transparent",
              }}
              onClick={() => handleSortSelect(sortOption)}
            >
              <ListItemText
                primary={sortOption.label}
                primaryTypographyProps={{
                  fontSize: 13,
                  fontWeight: isSelected ? 500 : 400,
                }}
              />
              {isSelected && (
                <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
                  <SvgColor
                    src={sortOption.icon[currentDirection]}
                    sx={{ width: 16, height: 16, ml: 0.5 }}
                  />
                </ListItemIcon>
              )}
            </MenuItem>
          );
        })}
      </Menu>
    </Stack>
  );
}

ActionBar.propTypes = {
  items: PropTypes.array,
  onSortChange: PropTypes.func,
  sortConfig: PropTypes.shape({
    field: PropTypes.string,
    direction: PropTypes.oneOf(["desc", "asc"]),
  }),
  setSortConfig: PropTypes.func,
};
