import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, Button, Chip, Popover, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import ComplexFilter from "src/components/ComplexFilter/ComplexFilter";
import { getRandomId } from "src/utils/utils";

const FilterPanel = ({
  anchorEl,
  open,
  onClose,
  filters,
  setFilters,
  filterDefinition,
  defaultFilter,
}) => {
  const [tempFilters, setTempFilters] = useState(filters);
  const [activeTab, setActiveTab] = useState("basic");

  // Sync temp state when panel opens
  useEffect(() => {
    if (open) {
      setTempFilters(filters);
    }
  }, [open, filters]);

  const handleApply = useCallback(() => {
    setFilters(tempFilters);
    onClose();
  }, [tempFilters, setFilters, onClose]);

  const handleClose = useCallback(() => {
    setTempFilters(filters); // discard changes
    onClose();
  }, [filters, onClose]);

  const handleCloseFilter = useCallback(() => {
    // When last filter is removed inside ComplexFilter
    // keep panel open with a fresh empty filter
    setTempFilters([{ ...defaultFilter, id: getRandomId() }]);
  }, [defaultFilter]);

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={handleClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{
        paper: {
          sx: {
            width: 480,
            maxHeight: 400,
            mt: 0.5,
            display: "flex",
            flexDirection: "column",
          },
        },
      }}
    >
      {/* Tabs */}
      <Stack direction="row" spacing={0.5} sx={{ px: 2, pt: 1.5 }}>
        <Chip
          label="Basic"
          size="small"
          variant={activeTab === "basic" ? "filled" : "outlined"}
          color={activeTab === "basic" ? "primary" : "default"}
          onClick={() => setActiveTab("basic")}
          sx={{ fontWeight: 500, fontSize: 12 }}
        />
      </Stack>

      {/* Filter rows */}
      <Box sx={{ flex: 1, overflow: "auto", px: 2, py: 1.5 }}>
        <ComplexFilter
          filters={tempFilters}
          defaultFilter={defaultFilter}
          setFilters={setTempFilters}
          filterDefinition={filterDefinition}
          onClose={handleCloseFilter}
        />
      </Box>

      {/* Footer */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{
          px: 2,
          py: 1.5,
          borderTop: 1,
          borderColor: "divider",
        }}
      >
        <Button
          size="small"
          startIcon={<Iconify icon="mdi:plus" width={16} />}
          onClick={() =>
            setTempFilters((prev) => [
              ...prev,
              { ...defaultFilter, id: getRandomId() },
            ])
          }
          sx={{
            textTransform: "none",
            fontSize: 13,
            color: "text.secondary",
          }}
        >
          Add filter
        </Button>

        <Button
          variant="contained"
          size="small"
          startIcon={<Iconify icon="mdi:check" width={16} />}
          onClick={handleApply}
          sx={{ textTransform: "none", fontSize: 13 }}
        >
          Apply
        </Button>
      </Stack>
    </Popover>
  );
};

FilterPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  filters: PropTypes.array.isRequired,
  setFilters: PropTypes.func.isRequired,
  filterDefinition: PropTypes.array.isRequired,
  defaultFilter: PropTypes.object.isRequired,
};

export default React.memo(FilterPanel);
