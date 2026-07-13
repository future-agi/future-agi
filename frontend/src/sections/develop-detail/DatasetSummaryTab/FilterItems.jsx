import {
  Box,
  Button,
  Checkbox,
  Divider,
  Popover,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { forwardRef, useMemo, useRef, useState } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

const filterlist = [
  { title: "Prompts", value: "prompts" },
  { title: "Columns", value: "columns" },
  {
    title: "Evaluation",
    value: "evaluation",
  },
];

const columnType = {
  evaluation: "evaluation",
  run_prompt: "prompts",
  OTHERS: "columns",
  evaluation_reason: "columns",
  text: "columns",
  annotation_label: "columns",
  array: "columns",
  integer: "columns",
  float: "columns",
  boolean: "columns",
  datetime: "columns",
  json: "columns",
  image: "columns",
  audio: "columns",
};

const FilterItems = forwardRef(
  (
    {
      open,
      onClose,
      columnLists,
      handleApplyFilter,
      selectedColumn,
      setSelectedColumn,
      onCancled,
    },
    ref,
  ) => {
    const popperRef = useRef(null);
    const [searchText, setSearchText] = useState("");

    const [filterItem, setFilterItem] = useState(
      filterlist?.map((item) => item.value) || [],
    );

    const id = useMemo(() => (open ? `eval-filter-popper` : undefined), [open]);
    const filteredColumnList = useMemo(() => {
      const newData =
        columnLists?.map((item) => {
          const originType = item.origin_type;
          const dataType = item.data_type;
          return {
            ...item,
            type: columnType[originType || dataType],
          };
        }) || [];
  
      return newData?.filter((item) => {
        return filterItem.includes(item.type);
      });
    }, [columnLists, filterItem]);
    const handleApply = () => {
      handleApplyFilter();
      onClose();
    };

    const handleCancled = () => {
      onCancled();
      onClose();
    };

    const iconStyle = {
      color: "text.secondary",
    };

    const renderIcon = (col) => {
      const originType = col.origin_type;
      const dataType = col.data_type;
      if (originType === "run_prompt") {
        return (

          <SvgColor
            src={`/assets/icons/action_buttons/ic_run_prompt.svg`}
            sx={{ width: 20, height: 20, color: "info.main" }}
          />
        );
      } else if (originType === "evaluation") {
        return (
          <Iconify
            icon="material-symbols:check-circle-outline"
            sx={{ color: "info.success" }}
          />
        );
      } else if (
        originType === "optimisation" ||
        originType === "optimisation_evaluation"
      ) {
        return (
          <SvgColor
            src={`/assets/icons/action_buttons/ic_optimize.svg`}
            sx={{ width: 20, height: 20, color: "primary.main" }}
          />
        );
      } else if (originType === "annotation_label") {
        return <Iconify icon="jam:write" sx={iconStyle} />;
      } else if (dataType === "text") {
        return <Iconify icon="material-symbols:notes" sx={iconStyle} />;
      } else if (dataType === "array") {
        return <Iconify icon="material-symbols:data-array" sx={iconStyle} />;
      } else if (dataType === "integer") {
        return <Iconify icon="material-symbols:tag" sx={iconStyle} />;
      } else if (dataType === "float") {
        return <Iconify icon="tabler:decimal" sx={iconStyle} />;
      } else if (dataType === "boolean") {
        return (
          <Iconify icon="material-symbols:toggle-on-outline" sx={iconStyle} />
        );
      } else if (dataType === "datetime") {
        return <Iconify icon="tabler:calendar" sx={iconStyle} />;
      } else if (dataType === "json") {
        return <Iconify icon="material-symbols:data-object" sx={iconStyle} />;
      } else if (dataType === "image") {
        return (
          <SvgColor
            src={`/assets/icons/action_buttons/ic_image.svg`}
            sx={{ width: 20, height: 20, color: "text.secondary" }}
          />
        );
      } else if (dataType === "audio") {
        return (
          <SvgColor
            src={`/assets/icons/action_buttons/ic_audio.svg`}
            sx={{ width: 20, height: 20, color: "text.secondary" }}
          />
        );
      }
    };

    const allSelected = useMemo(
      () => filterlist.length === filterItem.length,
      [filterItem],
    );

    return (
      <Popover
        id={id}
        anchorEl={ref?.current}
        open={open}
        ref={popperRef}
        onClose={() => {
          onClose();
        }}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "right",
        }}
        disableRestoreFocus
        disableEnforceFocus
        disableAutoFocus
        sx={{
          mt: 0.2,
          "& .MuiPaper-root": {
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            p: "12px",
            borderRadius: "4px !important",
            width: "350px",
            height: "330px",
          },
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "right",
        }}
      >
        <Box display={"flex"} gap={1.5} flexDirection={"column"}>
          <Box display={"flex"} gap={1}>
            <Button
              size="small"
              variant={allSelected ? "contained" : "outlined"}
              {...(allSelected ? { color: "primary" } : {})}
              sx={{
                borderColor: "divider",
                borderRadius: "4px",
                color: allSelected ? "background.paper" : "text.secondary",
              }}
              onClick={() =>
                setFilterItem((pre) =>
                  pre.length === filterlist?.length
                    ? []
                    : filterlist?.map((item) => item.value),
                )
              }
            >
              All
            </Button>
            {filterlist?.map((item) => {
              const active = !allSelected && filterItem.includes(item.value);
              return (
                <Button
                  key={item.value}
                  size="small"
                  variant={active ? "contained" : "outlined"}
                  {...(active ? { color: "primary" } : {})}
                  sx={{
                    // backgroundColor: active ? "primary.main" : "transparent",
                    borderRadius: "4px",
                    color: active ? "background.paper" : "text.secondary",
                    borderColor: "divider",
                  }}
                  onClick={() =>
                    setFilterItem((pre) =>
                      filterItem.includes(item.value)
                        ? allSelected
                          ? [item.value]
                          : pre.filter((temp) => temp != item.value)
                        : [...pre, item.value],
                    )
                  }
                >
                  {item.title}
                </Button>
              );
            })}
          </Box>
          <FormSearchField
            placeholder="Search"
            size="small"
            searchQuery={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            fullWidth
            onFocus={(e) => e.stopPropagation()}
          />
          <Box
            display="flex"
            flexDirection={"column"}
            gap={0}
            sx={{ overflowY: "auto", height: "150px" }}
          >
            {filteredColumnList?.map((item) => {
              const onClick = () => {
                setSelectedColumn((pre) =>
                  pre.some((temp) => temp.id === item.id)
                    ? pre.filter((temp) => temp.id !== item.id)
                    : [...pre, item],
                );
              };

              return (
                <Box
                  key={item.id}
                  onClick={onClick}
                  display={"flex"}
                  alignItems={"center"}
                  fontWeight={"fontWeightRegular"}
                  sx={{
                    cursor: "pointer",
                    borderRadius: 0.5,
                    "&:hover": {
                      backgroundColor: "action.hover",
                      "& div p": { fontWeight: "fontWeightMedium" },
                    },
                  }}
                >
                  <Checkbox
                    checked={selectedColumn.some((temp) => temp.id === item.id)}
                  />
                  <Box sx={{ display: "flex", gap: 0.5 }}>
                    {renderIcon(item)}
                    <Typography typography={"s2"}>{item.name}</Typography>
                  </Box>
                </Box>
              );
            })}
          </Box>
          <Divider />
          <Box width={"100%"} display="flex" gap={1.5}>
            <Button
              variant="outlined"
              size="small"
              fullWidth
              onClick={handleCancled}
            >
              Cancel
            </Button>
            <Button
              variant="contained"
              color="primary"
              size="small"
              fullWidth
              onClick={handleApply}
            >
              Apply
            </Button>
          </Box>
        </Box>
      </Popover>
    );
  },
);

FilterItems.displayName = "FilterItems";

export default FilterItems;

FilterItems.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  columnLists: PropTypes.array,
  handleApplyFilter: PropTypes.func,
  selectedColumn: PropTypes.array,
  setSelectedColumn: PropTypes.func,
  onCancled: PropTypes.func,
};
