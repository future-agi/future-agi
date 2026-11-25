import React, { useCallback, useMemo, useState } from "react";
import { Box, Button, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import SvgColor from "src/components/svg-color";
import PageHeader from "src/components/page-header";
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
  const navigate = useNavigate();
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
    // Revalidate the composed page, including malformed 200 responses that
    // render as errors even though React Query marks the request successful.
    feed.refetch();
    catalog.refetch();
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
          description="Track, triage and resolve agent errors: hallucinations, eval failures and pipeline issues."
          actions={
            <>
              <Button
                variant="outlined"
                size="small"
                startIcon={
                  <SvgColor
                    src="/assets/icons/navbar/ic_alert.svg"
                    sx={{ width: 16 }}
                  />
                }
                onClick={() => navigate("/dashboard/error-feed/alerts")}
                sx={{ borderColor: "divider", color: "text.secondary" }}
              >
                Alert rules
              </Button>
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
            </>
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
