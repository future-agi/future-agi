import React, { useState } from "react";
import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { useErrorFeedList } from "src/api/errorFeed/error-feed";
import ErrorFeedFilters from "./components/ErrorFeedFilters";
import ErrorFeedTable from "./components/ErrorFeedTable";
import { useErrorFeedApiParams } from "./store";

export default function ErrorFeedView() {
  const theme = useTheme();
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
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack gap={0.25}>
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography
              color="text.primary"
              typography="m2"
              fontWeight="fontWeightSemiBold"
            >
              Error Feed
            </Typography>
            <Box
              sx={{
                px: 0.75,
                py: 0.25,
                borderRadius: "4px",
                bgcolor: "action.hover",
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{
                  fontSize: "11px",
                  fontWeight: 600,
                  color: "text.secondary",
                  fontFeatureSettings: "'tnum'",
                }}
              >
                {totalCount}
              </Typography>
            </Box>
          </Stack>
          <Typography
            typography="s2"
            color="text.secondary"
            fontWeight="fontWeightRegular"
          >
            Track, triage, and resolve AI errors — hallucinations, eval
            failures, and pipeline issues
          </Typography>
        </Stack>

        <Stack direction="row" alignItems="center" gap={1}>
          <Button
            variant="outlined"
            size="small"
            startIcon={
              <SvgColor
                src="/assets/icons/ic_docs_single.svg"
                sx={{ width: 15, height: 15 }}
              />
            }
            component="a"
            href="https://docs.futureagi.com/docs/error-feed"
            target="_blank"
            sx={{
              height: 32,
              fontSize: "13px",
              borderColor: "divider",
              color: "text.secondary",
              borderRadius: "6px",
              "&:hover": { borderColor: "border.hover" },
            }}
          >
            Docs
          </Button>
        </Stack>
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
