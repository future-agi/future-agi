import {
  Box,
  Checkbox,
  Divider,
  FormControlLabel,
  Popover,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useDebounce } from "src/hooks/use-debounce";

const ALL_COLUMNS = [
  { field: "name", label: "Name", locked: true },
  { field: "description", label: "Description" },
  { field: "simulationType", label: "Agent Type" },
  { field: "tags", label: "Attributes" },
  { field: "createdBy", label: "Created By" },
  { field: "lastUpdated", label: "Last updated" },
];

const PersonasColumnsPopover = ({
  anchorEl,
  open,
  onClose,
  hiddenColumns,
  onToggleColumn,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearch = useDebounce(searchQuery.trim(), 300);

  const filteredColumns = useMemo(
    () =>
      ALL_COLUMNS.filter((col) =>
        col.label.toLowerCase().includes(debouncedSearch.toLowerCase()),
      ),
    [debouncedSearch],
  );

  const visibleCount = ALL_COLUMNS.filter(
    (c) => !hiddenColumns.includes(c.field),
  ).length;

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: -8, horizontal: "right" }}
      slotProps={{
        paper: {
          sx: { p: 0, minWidth: 320, maxHeight: 400, borderRadius: "8px" },
        },
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column" }}>
        <Box
          sx={{
            position: "sticky",
            top: 0,
            zIndex: 10,
            backgroundColor: "background.paper",
          }}
        >
          <Box sx={{ p: 1.5, pb: 1 }}>
            <FormSearchField
              size="small"
              fullWidth
              placeholder="Search columns"
              searchQuery={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </Box>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              px: 2,
              pb: 0.5,
            }}
          >
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: "12px" }}
            >
              {visibleCount} of {ALL_COLUMNS.length} visible
            </Typography>
          </Box>
          <Divider />
        </Box>

        <Box sx={{ p: 1, display: "flex", flexDirection: "column" }}>
          {filteredColumns.map((col) => {
            const isVisible = !hiddenColumns.includes(col.field);
            return (
              <FormControlLabel
                key={col.field}
                sx={{
                  mx: 0,
                  px: 1,
                  py: 0.25,
                  borderRadius: "4px",
                  "&:hover": { backgroundColor: "action.hover" },
                  opacity: col.locked ? 0.6 : 1,
                }}
                control={
                  <Checkbox
                    size="small"
                    checked={isVisible}
                    disabled={col.locked}
                    onChange={() => onToggleColumn(col.field)}
                    sx={{ p: 0.5 }}
                  />
                }
                label={
                  <Typography
                    variant="body2"
                    sx={{ fontSize: "13px", ml: 0.5 }}
                  >
                    {col.label}
                    {col.locked && (
                      <Iconify
                        icon="solar:lock-linear"
                        width={12}
                        sx={{
                          ml: 0.5,
                          verticalAlign: "text-bottom",
                          color: "text.disabled",
                        }}
                      />
                    )}
                  </Typography>
                }
              />
            );
          })}
        </Box>
      </Box>
    </Popover>
  );
};

PersonasColumnsPopover.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  hiddenColumns: PropTypes.arrayOf(PropTypes.string).isRequired,
  onToggleColumn: PropTypes.func.isRequired,
};

export default PersonasColumnsPopover;
