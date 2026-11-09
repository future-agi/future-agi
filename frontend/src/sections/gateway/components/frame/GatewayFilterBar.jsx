import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import FormSearchField from "src/components/FormSearchField/FormSearchField";

// The row above a gateway table: the Future AGI search field, the page's
// filters (FA selects, chips or date pickers, passed as children) and, on the
// right, actions such as Columns or Export. It wraps at narrow widths.
export default function GatewayFilterBar({
  search,
  onSearchChange,
  searchPlaceholder = "Search",
  actions,
  children,
}) {
  return (
    <Box
      role="search"
      sx={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 1,
      }}
    >
      {onSearchChange && (
        <FormSearchField
          size="small"
          placeholder={searchPlaceholder}
          searchQuery={search}
          onChange={(event) => onSearchChange(event.target.value)}
          inputProps={{ "aria-label": searchPlaceholder }}
          sx={{
            minWidth: { xs: "100%", sm: 250 },
            flex: { xs: "1 1 100%", sm: "0 1 320px" },
            "& .MuiOutlinedInput-root": { height: "30px" },
          }}
        />
      )}
      {children}
      {actions && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            ml: { xs: 0, sm: "auto" },
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
