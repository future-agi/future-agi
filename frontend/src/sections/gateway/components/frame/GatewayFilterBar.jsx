import React, { useRef } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";

import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";

// The row above a gateway table: the Future AGI search field, the page's
// filters (FA selects, chips or date pickers, passed as children) and, on the
// right, actions such as Columns or Export. It wraps at narrow widths.
// Every control sits on one line: the text baseline of the fields' values.
// A labelled select is its label above its field, so centring the row would
// count the label; aligning last baselines lines the search box, the selects'
// fields, chips, toggles, plain text and the actions up on the field text.
// A browser without "last baseline" drops that value and would stretch every
// control to the row's height. There the row is centred instead, and the
// search box and the actions sit on the bottom edge the fields share (the
// actions centred in a 30 px band), as before the baseline rule.
const NO_LAST_BASELINE = "@supports not (align-items: last baseline)";

export default function GatewayFilterBar({
  search,
  onSearchChange,
  searchPlaceholder = "Search",
  actions,
  children,
}) {
  const searchInputRef = useRef(null);
  const clearSearch = () => {
    onSearchChange("");
    searchInputRef.current?.focus();
  };

  return (
    <Box
      role="search"
      sx={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "last baseline",
        gap: 1,
        [NO_LAST_BASELINE]: { alignItems: "center" },
      }}
    >
      {onSearchChange && (
        <FormSearchField
          size="small"
          placeholder={searchPlaceholder}
          searchQuery={search}
          onChange={(event) => onSearchChange(event.target.value)}
          inputRef={searchInputRef}
          inputProps={{ "aria-label": searchPlaceholder }}
          InputProps={{
            endAdornment: search ? (
              <InputAdornment position="end">
                <IconButton
                  type="button"
                  size="small"
                  edge="end"
                  aria-label="Clear search"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={clearSearch}
                >
                  <Iconify icon="solar:close-circle-linear" width={16} />
                </IconButton>
              </InputAdornment>
            ) : null,
          }}
          sx={{
            minWidth: { xs: "100%", sm: 250 },
            flex: { xs: "1 1 100%", sm: "0 1 320px" },
            "& .MuiOutlinedInput-root": { height: "30px" },
            [NO_LAST_BASELINE]: { alignSelf: "flex-end" },
          }}
        />
      )}
      {children}
      {actions && (
        <Box
          sx={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: 1,
            minWidth: 0,
            maxWidth: "100%",
            ml: { xs: 0, sm: "auto" },
            [NO_LAST_BASELINE]: { alignSelf: "flex-end", minHeight: 30 },
          }}
        >
          {actions}
        </Box>
      )}
    </Box>
  );
}

GatewayFilterBar.propTypes = {
  search: PropTypes.string,
  onSearchChange: PropTypes.func,
  searchPlaceholder: PropTypes.string,
  actions: PropTypes.node,
  children: PropTypes.node,
};
