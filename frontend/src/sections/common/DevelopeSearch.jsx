import { Box, Collapse, IconButton, InputAdornment } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import Iconify from "src/components/iconify";
import { useLocation } from "react-router";
import FormSearchField from "src/components/FormSearchField/FormSearchField";

const DevelopeSearch = ({
  experimentSearch,
  setExperimentSearch,
  observeSearch,
  setObserveSearch,
  onSearchClick,
}) => {
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const location = useLocation();
  const [searchType, setSearchType] = useState("experiment");
  const onClose = () => {
    setIsSearchOpen(false);
    setExperimentSearch("");
    setObserveSearch("");
  };

  useEffect(() => {
    if (location.pathname.includes("/observe")) {
      setSearchType("observe");
    } else if (location.pathname.includes("/experiment")) {
      setSearchType("experiment");
    }
  }, [location.pathname]);
  const handleSearchChange = (e) => {
    const value = e.target.value;
    if (searchType === "experiment") {
      setExperimentSearch(value);
    } else {
      setObserveSearch(value);
    }
  };

  // useEffect(() => {
  //   if (experimentSearch) {
  //     trackEvent(Events.expSearched, {
  //       name: experimentSearch,
  //     });
  //   }
  // }, [experimentSearch]);

  return (
    <Box sx={{ display: "flex", alignItems: "center" }}>
      <Collapse
        in={!isSearchOpen}
        timeout="auto"
        unmountOnExit
        orientation="horizontal"
      >
        <IconButton
          size="small"
          sx={{ color: "text.disabled" }}
          onClick={() => {
            setIsSearchOpen(!isSearchOpen);
            if (onSearchClick) {
              onSearchClick();
            }
          }}
        >
          <Iconify icon="solar:magnifier-linear" color="text.disabled" />
        </IconButton>
      </Collapse>
      <Collapse
        in={isSearchOpen}
        timeout="auto"
        unmountOnExit
        orientation="horizontal"
      >
        <FormSearchField
          autoFocus
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
            endAdornment: (
              <InputAdornment position="end">
                <IconButton size="small" onClick={onClose}>
                  <Iconify icon="solar:close-linear" />
                </IconButton>
              </InputAdornment>
            ),
          }}
          placeholder="Search"
          size="small"
          sx={{ width: "180px" }}
          // value={experimentSearch}
          searchQuery={
            searchType === "experiment" ? experimentSearch : observeSearch
          }
          // onChange={(e) => setExperimentSearch(e.target.value)}
          onChange={handleSearchChange}
        />
      </Collapse>
    </Box>
  );
};

DevelopeSearch.propTypes = {
  experimentSearch: PropTypes.string,
  setExperimentSearch: PropTypes.func,
  observeSearch: PropTypes.string,
  setObserveSearch: PropTypes.func,
  onSearchClick: PropTypes.func,
};

export default DevelopeSearch;
