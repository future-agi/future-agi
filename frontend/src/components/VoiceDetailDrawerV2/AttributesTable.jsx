import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import NestedJsonTable from "./NestedJsonTable";

/**
 * Attributes tab for the voice drawer. Wraps the shared `NestedJsonTable`
 * with a search input + copy-all button so it looks like the trace
 * drawer's AttributesCard but shares rendering with the Logs and
 * Messages detail panels.
 */
const AttributesTable = ({ attributes, maxHeight = "65vh" }) => {
  const [query, setQuery] = useState("");
  // `appliedQuery` drives the filter and is committed as an URGENT update.
  // It must never go through `startTransition`/`useDeferredValue`: both are
  // interruptible, and this drawer re-renders often enough to restart such
  // an update indefinitely — the query then never reaches the table and the
  // search silently does nothing. The 120ms debounce alone is what keeps the
  // re-filter to once per typing burst.
  const [appliedQuery, setAppliedQuery] = useState("");
  const debounceRef = useRef(null);

  const scheduleQueryApply = (value) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setAppliedQuery(value), 120);
  };

  const handleQueryChange = (e) => {
    const value = e.target.value;
    setQuery(value);
    scheduleQueryApply(value);
  };

  const handleClearQuery = () => {
    setQuery("");
    if (debounceRef.current) clearTimeout(debounceRef.current);
    setAppliedQuery("");
  };

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  const handleCopyAll = () => {
    navigator.clipboard
      .writeText(JSON.stringify(attributes, null, 2))
      .then(() => {
        enqueueSnackbar("All attributes copied", {
          variant: "info",
          autoHideDuration: 1200,
        });
      });
  };

  const hasAny =
    attributes &&
    (typeof attributes !== "object" || Object.keys(attributes).length > 0);

  if (!hasAny) {
    return (
      <Box
        sx={{
          p: 2,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: 120,
        }}
      >
        <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
          No attributes available
        </Typography>
      </Box>
    );
  }

  return (
    <Stack gap={1} sx={{ width: "100%", minWidth: 0 }}>
      {/* Search + Copy */}
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Box
          sx={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            px: 1,
            py: 0.25,
          }}
        >
          <Iconify icon="mdi:magnify" width={14} color="text.disabled" />
          <Box
            component="input"
            placeholder="Search attributes"
            value={query}
            onChange={handleQueryChange}
            sx={{
              flex: 1,
              border: "none",
              outline: "none",
              bgcolor: "transparent",
              fontSize: 11,
              color: "text.primary",
              fontFamily: "inherit",
              py: 0.25,
              "&::placeholder": { color: "text.disabled" },
            }}
          />
          {query && (
            <Iconify
              icon="mdi:close"
              width={12}
              onClick={handleClearQuery}
              sx={{
                cursor: "pointer",
                color: "text.disabled",
                "&:hover": { color: "text.primary" },
              }}
            />
          )}
        </Box>
        <Tooltip title="Copy all" arrow placement="top">
          <IconButton
            size="small"
            onClick={handleCopyAll}
            sx={{
              width: 24,
              height: 24,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
            }}
          >
            <Iconify icon="tabler:copy" width={13} />
          </IconButton>
        </Tooltip>
      </Stack>

      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          maxHeight,
          overflow: "auto",
          p: 0.75,
        }}
      >
        <NestedJsonTable
          data={attributes}
          searchQuery={appliedQuery}
          emptyMessage="No attributes available"
        />
      </Box>
    </Stack>
  );
};

AttributesTable.propTypes = {
  attributes: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
  maxHeight: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};

export default AttributesTable;
