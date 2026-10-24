import React, { useState } from "react";
import { Box, Button, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import PageHeader from "src/components/page-header";
import { useErrorFeedList } from "src/api/errorFeed/mockAdapter";
import ErrorFeedFilters from "./components/ErrorFeedFilters";
import ErrorFeedTable from "./components/ErrorFeedTable";
import { useErrorFeedApiParams } from "./store";

export default function ErrorFeedView() {
  const [selected, setSelected] = useState([]);

  const apiParams = useErrorFeedApiParams();
  const { data } = useErrorFeedList(apiParams);
  const totalCount = data?.total ?? 0;

  const handleSelect = (clusterId, checked) => {
    setSelected((prev) =>
      checked ? [...prev, clusterId] : prev.filter((id) => id !== clusterId),
    );
  };

  const handleSelectAll = (checked, ids) => {
    setSelected(checked ? ids : []);
  };

  const handleClearSelection = () => setSelected([]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        height: "100%",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {/* ── Page Header ── */}
      <Box
        sx={{
          px: 2,
          pt: 2,
          pb: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <PageHeader
          sx={{ mb: 0 }}
          title="Error Feed"
          badge={
            <Typography
              sx={{
                typography: "s1",
                fontWeight: 400,
                color: "text.tertiary",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {totalCount}
            </Typography>
          }
          description="Track, triage, and resolve AI errors — hallucinations, eval failures, and pipeline issues"
          actions={
            <Button
              variant="outlined"
              size="small"
              startIcon={
                <SvgColor
                  src="/assets/icons/ic_docs_single.svg"
                  sx={{ width: 15 }}
                />
              }
              component="a"
              href="https://docs.futureagi.com/docs/error-feed"
              target="_blank"
              sx={{
                borderColor: "divider",
                color: "text.secondary",
                "&:hover": { borderColor: "border.hover" },
              }}
            >
              Docs
            </Button>
          }
        />
      </Box>

      {/* ── Content ── */}
      <Box
        sx={{
          flex: 1,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          gap: 1.5,
          p: 2,
        }}
      >
        {/* Filters */}
        <ErrorFeedFilters
          selected={selected}
          onClearSelection={handleClearSelection}
        />

        {/* Table */}
        <ErrorFeedTable
          selected={selected}
          onSelect={handleSelect}
          onSelectAll={handleSelectAll}
        />
      </Box>
    </Box>
  );
}
