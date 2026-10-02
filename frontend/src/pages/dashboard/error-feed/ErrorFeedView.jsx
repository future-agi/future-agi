import React, { useCallback, useMemo, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import {
  useErrorFeedList,
  useObserveProjectList,
} from "src/api/errorFeed/error-feed";
import ErrorFeedFilters from "./components/ErrorFeedFilters";
import ErrorFeedTable from "./components/ErrorFeedTable";
import { deriveFeedPageState } from "./feedPageState";
import { useErrorFeedApiParams } from "./store";

// Re-read on every visit/focus: a first Observe project is created by SDK
// instrumentation, not by a mutation this page could invalidate, so a user
// coming back within the catalog's 5-minute freshness window must still see
// it. Bounded to this page; no polling.
const REVALIDATE_ON_ENTRY = {
  refetchOnMount: "always",
  refetchOnWindowFocus: "always",
};

export default function ErrorFeedView() {
  const [selected, setSelected] = useState([]);

  const apiParams = useErrorFeedApiParams();
  const feed = useErrorFeedList(apiParams, REVALIDATE_ON_ENTRY);
  const catalog = useObserveProjectList(REVALIDATE_ON_ENTRY);

  const pageState = deriveFeedPageState({ catalog, feed });
  const rows = useMemo(() => feed.data?.data ?? [], [feed.data]);
  const totalCount = feed.data?.total ?? 0;

  const projectOptions = useMemo(
    () => [{ value: "", label: "All Projects" }, ...(catalog.data ?? [])],
    [catalog.data],
  );

  const handleRetry = useCallback(() => {
    if (feed.isError) feed.refetch();
    if (catalog.isError) catalog.refetch();
  }, [feed, catalog]);

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
          projectOptions={projectOptions}
        />

        {/* Table */}
        <ErrorFeedTable
          rows={rows}
          totalCount={totalCount}
          isLoading={feed.isLoading}
          pageState={pageState}
          onRetry={handleRetry}
          selected={selected}
          onSelect={handleSelect}
          onSelectAll={handleSelectAll}
        />
      </Box>
    </Box>
  );
}
