import React from "react";
import PropTypes from "prop-types";
import parse from "autosuggest-highlight/parse";
import match from "autosuggest-highlight/match";
import { memo, useState, useCallback } from "react";

import Box from "@mui/material/Box";
import List from "@mui/material/List";
import Stack from "@mui/material/Stack";
import { useTheme } from "@mui/material/styles";
import InputBase from "@mui/material/InputBase";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import Dialog, { dialogClasses } from "@mui/material/Dialog";

import { useRouter } from "src/routes/hooks";

import { useBoolean } from "src/hooks/use-boolean";
import { useResponsive } from "src/hooks/use-responsive";
import { useEventListener } from "src/hooks/use-event-listener";

import Label from "src/components/label";
import Iconify from "src/components/iconify";
import Scrollbar from "src/components/scrollbar";
import SearchNotFound from "src/components/search-not-found";

import ResultItem from "./result-item";
import { useNavData } from "../../dashboard/config-navigation";
import { applyFilter, groupedData, getAllItems } from "./utils";

// ----------------------------------------------------------------------

function Searchbar({ variant }) {
  const theme = useTheme();

  const router = useRouter();

  const search = useBoolean();

  const lgUp = useResponsive("up", "lg");

  const [searchQuery, setSearchQuery] = useState("");

  const navData = useNavData();

  const handleClose = useCallback(() => {
    search.onFalse();
    setSearchQuery("");
  }, [search]);

  const handleKeyDown = (event) => {
    if (event.key === "k" && event.metaKey) {
      search.onToggle();
      setSearchQuery("");
    }
  };

  useEventListener("keydown", handleKeyDown);

  const handleClick = useCallback(
    (path) => {
      if (path.includes("http")) {
        window.open(path);
      } else {
        router.push(path);
      }
      handleClose();
    },
    [handleClose, router],
  );

  const handleSearch = useCallback((event) => {
    setSearchQuery(event.target.value);
  }, []);

  const dataFiltered = applyFilter({
    inputData: getAllItems({ data: navData }),
    query: searchQuery,
  });

  const notFound = searchQuery && !dataFiltered.length;

  const renderItems = () => {
    const data = groupedData(dataFiltered);

    return Object.keys(data)
      .sort((a, b) => -b.localeCompare(a))
      .map((group, index) => (
        <List key={group || index} disablePadding>
          {data[group].map((item) => {
            const { title, path } = item;

            const partsTitle = parse(title, match(title, searchQuery));

            const partsPath = parse(path, match(path, searchQuery));

            return (
              <ResultItem
                path={partsPath}
                title={partsTitle}
                key={`${title}${path}`}
                groupLabel={searchQuery && group}
                onClickItem={() => handleClick(path)}
              />
            );
          })}
        </List>
      ));
  };

  // Design system nav search: a 28px field in the sidebar that opens the
  // same ⌘K dialog.
  const renderNavButton = (
    <Box
      component="button"
      type="button"
      onClick={search.onTrue}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        width: 1,
        height: 28,
        px: 1,
        borderRadius: "6px",
        border: (t) => `1px solid ${t.palette.border.default}`,
        bgcolor: "background.paper",
        color: "text.tertiary",
        font: "inherit",
        fontSize: 13,
        cursor: "text",
        textAlign: "left",
        "&:hover": { borderColor: (t) => t.palette.border.hover },
        "&:focus-visible": {
          outline: (t) => `2px solid ${t.palette.border.focus}`,
          outlineOffset: 1,
        },
      }}
    >
      <Iconify icon="solar:magnifier-linear" width={14} />
      <Box component="span" sx={{ flex: 1 }}>
        Search
      </Box>
      <Box
        component="kbd"
        sx={{
          fontFamily: "inherit",
          fontSize: 11,
          lineHeight: "16px",
          px: 0.5,
          borderRadius: "4px",
          border: (t) => `1px solid ${t.palette.border.default}`,
          color: "text.tertiary",
        }}
      >
        ⌘K
      </Box>
    </Box>
  );

  const renderButton =
    variant === "nav" ? (
      renderNavButton
    ) : (
      <Stack direction="row" alignItems="center">
        <IconButton onClick={search.onTrue}>
          <Iconify icon="solar:magnifier-linear" />
        </IconButton>

        {lgUp && (
          <Label sx={{ px: 0.75, fontSize: 12, color: "text.secondary" }}>
            ⌘K
          </Label>
        )}
      </Stack>
    );

  return (
    <>
      {renderButton}

      <Dialog
        fullWidth
        maxWidth={false}
        open={search.value}
        onClose={handleClose}
        transitionDuration={{
          enter: theme.transitions.duration.shortest,
          exit: 0,
        }}
        PaperProps={{
          sx: { maxWidth: 720, mt: 15, overflow: "unset" },
        }}
        sx={{
          [`& .${dialogClasses.container}`]: {
            alignItems: "flex-start",
          },
        }}
      >
        <Box sx={{ p: 3, borderBottom: `solid 1px ${theme.palette.divider}` }}>
          <InputBase
            fullWidth
            autoFocus
            placeholder="Search..."
            value={searchQuery}
            onChange={handleSearch}
            startAdornment={
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            }
            endAdornment={
              <Label sx={{ letterSpacing: 1, color: "text.secondary" }}>
                esc
              </Label>
            }
            inputProps={{
              sx: { typography: "h6" },
            }}
          />
        </Box>

        <Scrollbar sx={{ p: 3, pt: 2, height: 400 }}>
          {notFound ? (
            <SearchNotFound query={searchQuery} sx={{ py: 10 }} />
          ) : (
            renderItems()
          )}
        </Scrollbar>
      </Dialog>
    </>
  );
}

Searchbar.propTypes = { variant: PropTypes.oneOf(["nav"]) };

const MemoizedSearchbar = memo(Searchbar);

export default MemoizedSearchbar;
