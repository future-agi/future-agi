import {
  Box,
  Chip,
  FormControl,
  InputLabel,
  MenuItem,
  OutlinedInput,
  Select,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef, useState } from "react";
import FilterOperator from "./FilterOperator";
import FilterValue from "./FilterValue";
import FilterOptionsPopover from "./FilterOptionsPopover";
import { getPerformanceTagColor, getTabLabel } from "src/utils/utils";

const FilterSection = ({ setFilter, filter }) => {
  const [isFilterOptionsOpen, setIsFilterOptionsOpen] = useState(false);

  const filterSelectRef = useRef(null);

  const renderSelect = () => {
    if (filter?.type === "performanceTag") {
      return (
        <FormControl size="small">
          <InputLabel>Performance Tag</InputLabel>
          <Select
            label="Performance Tag"
            open={isFilterOptionsOpen}
            onClose={() => setIsFilterOptionsOpen(false)}
            onOpen={() => setIsFilterOptionsOpen(true)}
            ref={filterSelectRef}
            value={filter?.values || []}
            multiple
            input={<OutlinedInput label="Performance Tag" />}
            MenuProps={{
              PaperProps: {
                style: {
                  display: "none",
                },
              },
            }}
            renderValue={(selected) => (
              <Box
                sx={{
                  display: "flex",
                  gap: 0.5,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {selected.slice(0, 1).map((value) => (
                  <Chip
                    variant="soft"
                    color={getPerformanceTagColor(value)}
                    key={value}
                    label={getTabLabel(value)}
                    size="small"
                    sx={{
                      fontSize: "11px",
                    }}
                  />
                ))}
                {Boolean(selected.length - 1) && (
                  <Typography variant="subtitle" color="text.disabled">
                    + {selected.length - 1} more
                  </Typography>
                )}
              </Box>
            )}
          >
            {filter?.values.map((t) => (
              <MenuItem value={t} key={t} />
            ))}
          </Select>
        </FormControl>
      );
    }
    return (
      <FormControl size="small">
        <InputLabel>Property</InputLabel>
        <Select
          label="Property"
          open={isFilterOptionsOpen}
          onClose={() => setIsFilterOptionsOpen(false)}
          onOpen={() => {
            setIsFilterOptionsOpen(true);
          }}
          ref={filterSelectRef}
          value={filter?.key || ""}
          MenuProps={{
            PaperProps: {
              style: {
                display: "none",
              },
            },
          }}
        >
          <MenuItem value={filter?.key}>{filter?.key}</MenuItem>
        </Select>
      </FormControl>
    );
  };

  return (
    <Box
      sx={{
        paddingTop: 1,
        gap: 1,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {renderSelect()}
      {filter?.type !== "performanceTag" && (
        <>
          <FilterOperator
            operator={filter?.operator || "equal"}
            setOperator={(newOperator) => {
              setFilter((prev) => ({
                ...prev,
                operator: newOperator,
              }));
            }}
            dataType={filter.datatype}
          />
          <FilterValue
            dataType={filter.datatype}
            options={filter?.options || []}
            updateFilter={(updaterFn) =>
              setFilter((prev) => {
                const updated = updaterFn(prev);
                return {
                  ...prev,
                  values: updated.values,
                };
              })
            }
            value={filter?.values || []}
            disabled={false}
          />
        </>
      )}
      <FilterOptionsPopover
        ancElem={filterSelectRef?.current}
        open={isFilterOptionsOpen}
        onClose={() => setIsFilterOptionsOpen(false)}
        onSelect={(update) => {
          setFilter((existingFilter) => {
            let newFilter = update;
            if (typeof update === "function")
              newFilter = update(existingFilter);
            return { ...existingFilter, ...newFilter };
          });
        }}
        filter={filter}
      />
    </Box>
  );
};

FilterSection.propTypes = {
  setFilter: PropTypes.func,
  filter: PropTypes.object,
};

export default FilterSection;
