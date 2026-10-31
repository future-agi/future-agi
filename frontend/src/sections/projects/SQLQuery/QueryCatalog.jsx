import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  List,
  ListItemButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { searchCatalog } from "./catalogSearch";
import { ACTION_BUTTON_SX, META_TEXT_SX, warningText } from "./queryUi";
import { quoteIdentifier } from "./sqlCompletions";

export default function QueryCatalog({ datasets = [], search = "", onInsert }) {
  const [expanded, setExpanded] = useState({});
  const matches = searchCatalog(datasets, search);
  if (!matches.length)
    return (
      <Box sx={{ p: 2 }} role="status">
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
          No matching datasets or columns
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}>
          Try a dataset name, field, or data type, such as String.
        </Typography>
      </Box>
    );
  return (
    <Box>
      <Typography
        sx={{
          display: "block",
          px: 2,
          py: 1,
          typography: "s3",
          color: "text.secondary",
        }}
      >
        {/* The tab badge carries the total; only a search changes the count. */}
        {search.trim()
          ? `${matches.length} matching ${matches.length === 1 ? "dataset" : "datasets"} · `
          : ""}
        Select a column to insert
      </Typography>
      {matches.map((dataset) => {
        const unavailable = dataset.available === false;
        const expansionKey = `${search.trim()}:${dataset.name}`;
        const open =
          expanded[expansionKey] ??
          (Boolean(search.trim()) || dataset.name === datasets[0]?.name);
        return (
          <Box
            component="details"
            key={dataset.name}
            open={open}
            sx={{ borderTop: 1, borderColor: "divider" }}
          >
            <Box
              component="summary"
              onClick={(event) => {
                event.preventDefault();
                setExpanded((value) => ({ ...value, [expansionKey]: !open }));
              }}
              sx={{
                px: 2,
                py: 1.25,
                cursor: "pointer",
                listStyle: "none",
                display: "flex",
                alignItems: "center",
                gap: 1,
                "&::-webkit-details-marker": { display: "none" },
                bgcolor: open ? "action.hover" : "transparent",
                "&:hover": { bgcolor: "action.hover" },
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                  outlineOffset: -2,
                },
              }}
            >
              <Iconify
                icon="solar:alt-arrow-right-linear"
                width={14}
                sx={{
                  color: "text.secondary",
                  flexShrink: 0,
                  transition: "transform 120ms",
                  transform: open ? "rotate(90deg)" : "none",
                }}
              />
              <Typography
                component="span"
                sx={{
                  fontFamily: "monospace",
                  typography: "s2",
                  fontWeight: "fontWeightSemiBold",
                  overflowWrap: "anywhere",
                  color: unavailable ? "text.disabled" : "text.primary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {dataset.name}
                {unavailable ? " (unavailable)" : ""}
              </Typography>
              <Typography
                component="span"
                sx={{ ...META_TEXT_SX, flexShrink: 0 }}
              >
                {search.trim()
                  ? `${dataset.columns.length} of ${dataset.totalColumns}`
                  : dataset.totalColumns}{" "}
                columns
              </Typography>
            </Box>
            <Stack spacing={1} sx={{ px: 2, py: 1.5 }}>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {dataset.description}
              </Typography>
              {unavailable && (
                <Typography sx={{ typography: "s2", color: warningText }}>
                  {dataset.unavailable_reason ||
                    "This dataset is not available in this project."}
                </Typography>
              )}
              <Button
                size="small"
                variant="outlined"
                startIcon={<Iconify icon="solar:add-circle-linear" />}
                disabled={unavailable}
                aria-label={`Insert dataset ${dataset.name}`}
                onClick={() =>
                  !unavailable && onInsert(quoteIdentifier(dataset.name))
                }
                sx={{ ...ACTION_BUTTON_SX, alignSelf: "flex-start" }}
              >
                Insert dataset
              </Button>
            </Stack>
            <List
              dense
              disablePadding
              aria-label={`${dataset.name} columns`}
              sx={{ pb: 1 }}
            >
              {dataset.columns.map((column) => (
                <ListItemButton
                  key={column.name}
                  disabled={unavailable}
                  onClick={() =>
                    !unavailable && onInsert(quoteIdentifier(column.name))
                  }
                  aria-label={`Insert ${dataset.name}.${column.name}`}
                  sx={{
                    px: 2,
                    py: 0.5,
                    minHeight: 32,
                    columnGap: 1,
                    flexWrap: "wrap",
                    alignItems: "center",
                    "&.Mui-focusVisible": {
                      outline: "2px solid",
                      outlineColor: "primary.main",
                      outlineOffset: -2,
                    },
                  }}
                >
                  <Typography
                    component="span"
                    sx={{
                      fontFamily: "monospace",
                      typography: "s2",
                      color: "text.primary",
                      flexShrink: 0,
                      maxWidth: "100%",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {column.name}
                  </Typography>
                  <Typography
                    component="span"
                    sx={{
                      fontFamily: "monospace",
                      typography: "s3",
                      color: "text.secondary",
                      ml: "auto",
                      textAlign: "right",
                      flexShrink: 0,
                      maxWidth: "100%",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {column.type}
                  </Typography>
                </ListItemButton>
              ))}
            </List>
          </Box>
        );
      })}
    </Box>
  );
}
QueryCatalog.propTypes = {
  datasets: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string.isRequired,
      description: PropTypes.string,
      available: PropTypes.bool,
      unavailable_reason: PropTypes.string,
      columns: PropTypes.arrayOf(
        PropTypes.shape({
          name: PropTypes.string.isRequired,
          type: PropTypes.string.isRequired,
        }),
      ).isRequired,
    }),
  ),
  search: PropTypes.string,
  onInsert: PropTypes.func.isRequired,
};
