import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Autocomplete,
  Box,
  CircularProgress,
  TextField,
  Typography,
} from "@mui/material";
import { useDebounce } from "src/hooks/use-debounce";
import { useEvalAttributesInfinite } from "src/hooks/use-eval-attributes";

const SCROLL_FETCH_THRESHOLD_PX = 48;
const SEARCH_DEBOUNCE_MS = 250;
const PAGE_SIZE = 50;

// Server-search Autocomplete for the eval-attrs picker.
// Falls back to client-side filter of ``staticOptions`` when no projectId.
export default function AttributePathAutocomplete({
  projectId,
  rowType,
  filters,
  staticOptions,
  value,
  onChange,
  placeholder = "Select column...",
  size = "small",
  disabled,
  sx,
}) {
  const [inputValue, setInputValue] = useState("");
  // ``searchQuery`` only tracks user-typed input. ``inputValue`` also
  // syncs to the selected option's label (via MUI's reason="reset")
  // so the chip's text renders correctly after Enter / click.
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearch = useDebounce(searchQuery.trim(), SEARCH_DEBOUNCE_MS);

  const useServer = Boolean(projectId);

  const {
    items: serverItems,
    isLoading,
    isFetchingNextPage,
    hasMore,
    fetchMore,
  } = useEvalAttributesInfinite({
    projectId,
    rowType,
    filters,
    search: debouncedSearch,
    pageSize: PAGE_SIZE,
    enabled: useServer,
  });

  const staticFiltered = useMemo(() => {
    if (useServer) return [];
    const all = staticOptions || [];
    if (!debouncedSearch) return all;
    const needle = debouncedSearch.toLowerCase();
    return all.filter((opt) => String(opt).toLowerCase().includes(needle));
  }, [staticOptions, debouncedSearch, useServer]);

  const options = useServer ? serverItems : staticFiltered;

  // MUI hides the selected chip if its value isn't in ``options``; splice
  // it back in when server-search has narrowed it out.
  const optionsWithValue = useMemo(() => {
    if (!value || options.includes(value)) return options;
    return [value, ...options];
  }, [options, value]);

  const handleScroll = (event) => {
    if (!useServer || !hasMore || isFetchingNextPage) return;
    const node = event.currentTarget;
    if (
      node.scrollHeight - node.scrollTop - node.clientHeight <
      SCROLL_FETCH_THRESHOLD_PX
    ) {
      fetchMore();
    }
  };

  return (
    <Autocomplete
      size={size}
      disabled={disabled}
      options={optionsWithValue}
      value={value || null}
      onChange={(_, next) => onChange?.(next || "")}
      inputValue={inputValue}
      onInputChange={(_, next, reason) => {
        setInputValue(next);
        if (reason === "input") setSearchQuery(next);
      }}
      filterOptions={useServer ? (opts) => opts : undefined}
      isOptionEqualToValue={(opt, val) => opt === val}
      ListboxProps={{ onScroll: handleScroll }}
      loading={isLoading || isFetchingNextPage}
      noOptionsText={
        isLoading ? "Loading attributes…" : "No matching attributes"
      }
      sx={sx}
      renderInput={(params) => (
        <TextField
          {...params}
          placeholder={placeholder}
          InputProps={{
            ...params.InputProps,
            endAdornment: (
              <>
                {(isLoading || isFetchingNextPage) && useServer ? (
                  <CircularProgress size={14} />
                ) : null}
                {params.InputProps.endAdornment}
              </>
            ),
          }}
          sx={{ "& .MuiInputBase-root": { fontSize: "12px" } }}
        />
      )}
      renderOption={(props, option) => (
        <Box component="li" {...props} sx={{ py: 0.5 }}>
          <Typography variant="body2" sx={{ fontSize: "12px" }}>
            {String(option)}
          </Typography>
        </Box>
      )}
    />
  );
}

AttributePathAutocomplete.propTypes = {
  projectId: PropTypes.string,
  rowType: PropTypes.string,
  filters: PropTypes.object,
  staticOptions: PropTypes.array,
  value: PropTypes.string,
  onChange: PropTypes.func,
  placeholder: PropTypes.string,
  size: PropTypes.string,
  disabled: PropTypes.bool,
  sx: PropTypes.object,
};
