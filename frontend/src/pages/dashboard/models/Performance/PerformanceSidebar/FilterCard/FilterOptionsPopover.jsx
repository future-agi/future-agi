import {
  Box,
  Checkbox,
  Chip,
  Divider,
  InputAdornment,
  Popover,
  TextField,
} from "@mui/material";
import React, { useState } from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import {
  FilterList,
  FilterListItem,
  FilterListItemButton,
  FilterListItemText,
} from "./FilterListComponents";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { useDebounce } from "src/hooks/use-debounce";
import { getPerformanceTagColor, getTabLabel } from "src/utils/utils";

const LeftSidebarOptions = [
  { label: "All", value: "all" },
  { label: "Performance Metric", value: "performanceMetric" },
  { label: "Property", value: "properties" },
  { label: "Meta Tags", value: "metaTags" },
  { label: "Performance Tags", value: "performanceTags" },
];

const FilterOptionsPopover = ({ open, onClose, ancElem, onSelect, filter }) => {
  const { id } = useParams();

  const [selectedAggregation, setSelectedAggregation] = useState("all");

  const [searchText, setSearchText] = useState("");

  const debouncedSearchText = useDebounce(searchText);

  const { data: allOptions } = useQuery({
    queryFn: () =>
      axios.get(endpoints.performance.getFilterOptions(id), {
        params: { search_query: debouncedSearchText },
      }),
    queryKey: ["performance-filter-options", id, debouncedSearchText],
    staleTime: 30 * 60 * 1000, // 30 min stale time
    select: (d) => d.data?.result,
  });

  const renderOptions = () => {
    const components = [];

    if (
      selectedAggregation === "all" ||
      selectedAggregation === "performanceMetric"
    ) {
      if (
        selectedAggregation === "all" &&
        allOptions?.performanceMetric?.length
      )
        components.push(
          <FilterListItem>
            <FilterListItemText
              sx={{
                fontWeight: 600,
                fontSize: "12px",
                color: "text.disabled",
                paddingLeft: 1,
              }}
            >
              Performance metric
            </FilterListItemText>
          </FilterListItem>,
        );
      allOptions?.performanceMetric.forEach(({ name, id }) => {
        components.push(
          <FilterListItem key={id}>
            <FilterListItemButton
              onClick={() => {
                onSelect({
                  datatype: "number",
                  operator: "equal",
                  type: "performanceMetric",
                  values: [],
                  key: name,
                  keyId: id,
                });
                onClose();
              }}
            >
              <FilterListItemText>{name}</FilterListItemText>
            </FilterListItemButton>
          </FilterListItem>,
        );
      });
    }

    if (selectedAggregation === "all" || selectedAggregation === "properties") {
      if (selectedAggregation === "all" && allOptions?.properties?.length)
        components.push(
          <FilterListItem>
            <FilterListItemText
              sx={{
                fontWeight: 600,
                fontSize: "12px",
                color: "text.disabled",
                paddingLeft: 1,
              }}
            >
              Properties
            </FilterListItemText>
          </FilterListItem>,
        );
      allOptions?.properties.forEach(({ name, id, datatype, values }) => {
        components.push(
          <FilterListItem key={id}>
            <FilterListItemButton
              onClick={() => {
                onSelect({
                  type: "property",
                  datatype: datatype,
                  operator: "equal",
                  values: [],
                  key: name,
                  keyId: id,
                  options: values,
                });
                onClose();
              }}
            >
              <FilterListItemText>{name}</FilterListItemText>
            </FilterListItemButton>
          </FilterListItem>,
        );
      });
    }

    if (selectedAggregation === "all" || selectedAggregation === "metaTags") {
      if (selectedAggregation === "all" && allOptions?.metaTags?.length)
        components.push(
          <FilterListItem>
            <FilterListItemText
              sx={{
                fontWeight: 600,
                fontSize: "12px",
                color: "text.disabled",
                paddingLeft: 1,
              }}
            >
              Meta Tags
            </FilterListItemText>
          </FilterListItem>,
        );
      allOptions?.metaTags.forEach((tag) => {
        components.push(
          <FilterListItem key={tag}>
            <FilterListItemButton>
              <FilterListItemText>{tag}</FilterListItemText>
            </FilterListItemButton>
          </FilterListItem>,
        );
      });
    }

    if (
      selectedAggregation === "all" ||
      selectedAggregation === "performanceTags"
    ) {
      if (selectedAggregation === "all" && allOptions?.performanceTags?.length)
        components.push(
          <FilterListItem>
            <FilterListItemText
              sx={{
                fontWeight: 600,
                fontSize: "12px",
                color: "text.disabled",
                paddingLeft: 1,
              }}
            >
              Performance Tags
            </FilterListItemText>
          </FilterListItem>,
        );
      allOptions?.performanceTags.forEach((tag) => {
        const color = getPerformanceTagColor(tag);
        const selected = filter.values.includes(tag);

        const handleChange = () => {
          onSelect((e) => ({
            type: "performanceTag",
            datatype: "string",
            operator: "equal",
            key: "Performance Tag",
            keyId: "",
            values: selected
              ? e.values.filter((t) => t !== tag)
              : [...e.values, tag],
          }));
          onClose();
        };

        components.push(
          <FilterListItem key={tag}>
            <FilterListItemButton sx={{ display: "flex", gap: 1 }}>
              <Checkbox
                checked={selected}
                size="small"
                sx={{ p: 0 }}
                onChange={handleChange}
              />
              <Chip
                variant="soft"
                color={color}
                key={tag}
                label={getTabLabel(tag)}
                clickable
                size="small"
                sx={{
                  fontSize: "11px",
                }}
                onClick={handleChange}
              />
            </FilterListItemButton>
          </FilterListItem>,
        );
      });
    }
    return components;
  };

  return (
    <Popover
      anchorEl={ancElem}
      open={open}
      onClose={onClose}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "right",
      }}
      transformOrigin={{
        vertical: "top",
        horizontal: "right",
      }}
    >
      <Box
        sx={{
          width: "519px",
          height: "362px",
          padding: 0.5,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Box>
          <TextField
            size="small"
            sx={{ flex: 1 }}
            placeholder="Search metric..."
            fullWidth
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="eva:search-fill"
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
            }}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
        </Box>

        <Box
          sx={{
            display: "flex",
            paddingTop: 0.5,
            gap: 0.5,
            flex: 1,
            overflow: "auto",
          }}
        >
          <Box
            sx={{
              width: "170px",
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
              height: "100%",
              overflow: "auto",
            }}
          >
            <FilterList sx={{ flex: 1 }}>
              {LeftSidebarOptions.map(({ label, value }) => {
                const selected = selectedAggregation === value;
                return (
                  <FilterListItem key={value}>
                    <FilterListItemButton
                      selected={selected}
                      onClick={() => setSelectedAggregation(value)}
                    >
                      <FilterListItemText
                        sx={{
                          ".MuiListItemText-primary": {
                            fontWeight: selected ? 600 : 400,
                          },
                        }}
                      >
                        {label}
                      </FilterListItemText>
                    </FilterListItemButton>
                  </FilterListItem>
                );
              })}
            </FilterList>
          </Box>
          <Divider orientation="vertical" flexItem />
          <Box sx={{ flex: 1, overflow: "auto", height: "100%" }}>
            <FilterList sx={{ flex: 1 }}>{renderOptions()}</FilterList>
          </Box>
        </Box>
      </Box>
    </Popover>
  );
};

FilterOptionsPopover.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  ancElem: PropTypes.any,
  onSelect: PropTypes.func,
  filter: PropTypes.object,
};

export default FilterOptionsPopover;
