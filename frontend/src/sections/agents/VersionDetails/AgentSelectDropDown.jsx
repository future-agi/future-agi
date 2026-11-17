import React, { useState, useMemo, forwardRef } from "react";
import {
  Popover,
  Box,
  Typography,
  MenuItem,
  CircularProgress,
} from "@mui/material";
import { useDebounce } from "src/hooks/use-debounce";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import PropTypes from "prop-types";

const AgentSelectDropDown = forwardRef(
  (
    { open, onClose, fetchOptions, searchPlaceholder, labelText, onSelect },
    ref,
  ) => {
    const [searchText, setSearchText] = useState("");
    const debouncedSearchText = useDebounce(searchText.trim(), 500);

    const {
      data: list = [],
      isLoading,
      error,
    } = fetchOptions(debouncedSearchText);

    const options = useMemo(
      () =>
        list.map((item) => ({
          label: item.agent_name,
          value: item.id,
          description: item.description,
          // Keep reference to original agent object
          agent: item,
        })),
      [list],
    );

    const handleSelect = (option) => {
      // Pass the full agent object instead of just the option
      onSelect(option.agent);
      onClose();
    };

    return (
      <Popover
        open={open}
        anchorEl={ref?.current}
        onClose={onClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        PaperProps={{ sx: { minWidth: ref?.current?.clientWidth || 280 } }}
      >
        <Box>
          <FormSearchField
            placeholder={searchPlaceholder || "Search"}
            size="small"
            searchQuery={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            fullWidth
          />

          <Typography
            sx={{ px: 1, pt: 1 }}
            color="text.tertiary"
            fontWeight={600}
            fontSize={12}
          >
            {labelText || "All Agents"}
          </Typography>

          <Box sx={{ maxHeight: 220, overflowY: "auto" }}>
            {error ? (
              <Box sx={{ p: 2, textAlign: "center" }}>
                <Typography color="error" variant="body2">
                  Failed to load agents
                </Typography>
              </Box>
            ) : isLoading ? (
              <Box
                sx={{
                  p: 2,
                  textAlign: "center",
                  display: "flex",
                  flexDirection: "column",
                  gap: 1,
                }}
              >
                <CircularProgress size={20} />
                <Typography variant="body2" color="text.secondary">
                  Loading agents...
                </Typography>
              </Box>
            ) : options.length === 0 ? (
              <Box sx={{ p: 2, textAlign: "center" }}>
                <Typography variant="body2" color="text.secondary">
                  No agents found
                </Typography>
              </Box>
            ) : (
              options.map((option) => (
                <MenuItem
                  key={option.value}
                  onClick={() => handleSelect(option)}
                >
                  <Box display="flex" flexDirection="column">
                    <Typography variant="s2_1" fontWeight={400}>
                      {option.label}
                    </Typography>
                    <Typography
                      variant="s2"
                      color="text.tertiary"
                      noWrap
                      sx={{ maxWidth: 200 }} // adjust width
                    >
                      {option.description}
                    </Typography>
                  </Box>
                </MenuItem>
              ))
            )}
          </Box>
        </Box>
      </Popover>
    );
  },
);

AgentSelectDropDown.displayName = "AgentSelectDropDown";

AgentSelectDropDown.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  multiple: PropTypes.bool,
  onSelect: PropTypes.func,
  fetchOptions: PropTypes.func.isRequired,
  searchPlaceholder: PropTypes.string,
  labelText: PropTypes.string,
};

export default AgentSelectDropDown;
