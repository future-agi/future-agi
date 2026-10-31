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
import { searchCatalog } from "./catalogSearch";
import { quoteIdentifier } from "./sqlCompletions";

export default function QueryCatalog({ datasets = [], search = "", onInsert }) {
  const [expanded, setExpanded] = useState({});
  const matches = searchCatalog(datasets, search);
  if (!matches.length)
    return (
      <Box sx={{ p: 2 }} role="status">
        <Typography variant="subtitle2">
          No matching datasets or columns
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          Try a dataset name, field, or data type, such as String.
        </Typography>
      </Box>
    );
  return (
    <Box>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", px: 2, py: 1 }}
      >
        {matches.length} {matches.length === 1 ? "dataset" : "datasets"} ·
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
                fontSize: 13,
                bgcolor: open ? "action.hover" : "transparent",
                "&:hover": { bgcolor: "action.hover" },
                "&:focus-visible": {
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
                  fontSize: 13,
                  fontWeight: 600,
                  overflowWrap: "anywhere",
                }}
              >
                {dataset.name}
                {unavailable ? " (unavailable)" : ""}
              </Typography>
              <Typography
                component="span"
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", pl: 2 }}
              >
                {search.trim()
                  ? `${dataset.columns.length} of ${dataset.totalColumns}`
                  : dataset.totalColumns}{" "}
                columns
              </Typography>
            </Box>
            <Stack spacing={1} sx={{ px: 2, py: 1.5 }}>
              <Typography variant="caption" color="text.secondary">
                {dataset.description}
              </Typography>
              {unavailable && (
                <Typography variant="caption">
                  {dataset.unavailable_reason ||
                    "This dataset is not available in this project."}
                </Typography>
              )}
              <Button
                size="small"
                variant="outlined"
                disabled={unavailable}
                aria-label={`Insert dataset ${dataset.name}`}
                onClick={() =>
                  !unavailable && onInsert(quoteIdentifier(dataset.name))
                }
                sx={{ alignSelf: "flex-start" }}
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
                      fontSize: 12,
                      flexShrink: 0,
                      maxWidth: "100%",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {column.name}
                  </Typography>
                  <Typography
                    component="span"
                    variant="caption"
                    color="text.secondary"
                    sx={{
                      fontFamily: "monospace",
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
