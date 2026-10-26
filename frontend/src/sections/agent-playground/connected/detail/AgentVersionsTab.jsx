import PropTypes from "prop-types";
import React, { useEffect } from "react";
import { format } from "date-fns";
import { Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { alpha } from "@mui/material/styles";
import EmptyState from "src/sections/simulate/environments/components/EmptyState";
import ComingSoonCard from "./ComingSoonCard";
import { Card, LineSkeleton } from "./bits";
import { MONO } from "./format";
import useAgentRoute from "./useAgentRoute";
import usePinnedVersions from "./usePinnedVersions";

function Chip({ label, tone }) {
  return (
    <Box
      sx={{
        px: 0.75,
        height: 18,
        display: "inline-flex",
        alignItems: "center",
        borderRadius: 0.75,
        bgcolor: (t) =>
          tone === "primary"
            ? alpha(
                t.palette.primary.main,
                t.palette.mode === "dark" ? 0.16 : 0.1,
              )
            : alpha(t.palette.text.primary, 0.07),
        color: tone === "primary" ? "primary.main" : "text.secondary",
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 700 }}>
        {label}
      </Typography>
    </Box>
  );
}

Chip.propTypes = { label: PropTypes.string, tone: PropTypes.string };

// A timeline row while it loads, laid out like a real one: the dot and its
// line, then label, message and date at the same sizes and spacing.
function VersionSkeleton() {
  return (
    <Stack
      data-testid="version-skeleton"
      direction="row"
      spacing={1.5}
      sx={{ mx: -1, px: 1, pt: 0.75, pb: 1.5, mb: 0.75 }}
    >
      <Stack alignItems="center" sx={{ flexShrink: 0, pt: 0.25 }}>
        <Skeleton variant="circular" width={10} height={10} />
        <Box sx={{ width: 1.5, flex: 1, mt: 0.5, bgcolor: "divider" }} />
      </Stack>
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2" }}>
          <LineSkeleton width={36} />
        </Typography>
        <Typography sx={{ typography: "s3", mt: 0.375 }}>
          <LineSkeleton width="80%" />
        </Typography>
        <Typography sx={{ typography: "s3", mt: 0.25 }}>
          <LineSkeleton width={110} />
        </Typography>
      </Box>
    </Stack>
  );
}

const skeletons = (n) =>
  // eslint-disable-next-line react/no-array-index-key
  Array.from({ length: n }, (_, i) => (
    <VersionSkeleton key={`skeleton-${i}`} />
  ));

/**
 * Versions: a read-only timeline from the paginated versions list (the one
 * the Agent Definition page uses), newest first, and Compare (coming soon).
 */
export default function AgentVersionsTab() {
  const { agentId, agent, versionId, selectVersion } = useAgentRoute();
  const {
    versions,
    loaded,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
    error,
    refetch,
  } = usePinnedVersions(agentId, versionId);
  // Like the Agent Definition page: the next page loads at the list's end.
  // The listener attaches once the list exists, so it waits for the agent.
  const scrollRef = useScrollEnd(() => {
    if (hasNextPage && !isFetchingNextPage) fetchNextPage();
  }, [
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    !!agent,
    versions.length,
  ]);
  // A page that doesn't fill the list leaves nothing to scroll, so keep
  // loading until the list overflows or every version is in.
  useEffect(() => {
    const list = scrollRef.current;
    if (!list || !hasNextPage || isFetchingNextPage) return;
    if (list.scrollHeight <= list.clientHeight + 5) fetchNextPage();
  }, [
    scrollRef,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    versions.length,
  ]);
  const total = agent?.versionCount;

  return (
    <Box
      sx={{
        display: "grid",
        gap: 2,
        gridTemplateColumns: "300px 1fr",
        alignItems: "start",
        // The timeline fills the tab's height and scrolls inside, at any width.
        height: "100%",
      }}
    >
      <Card
        title={
          agent ? (
            `${total} version${total === 1 ? "" : "s"}`
          ) : (
            <LineSkeleton width={72} />
          )
        }
        sx={{ height: "100%" }}
      >
        <Box
          ref={scrollRef}
          data-testid="versions-scroll"
          sx={{
            px: 2,
            py: 1.75,
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
          }}
        >
          {isError && (
            <EmptyState
              icon="solar:danger-triangle-linear"
              title="Couldn't load versions"
              body={error?.message}
              action={
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() => refetch()}
                >
                  Retry
                </Button>
              }
            />
          )}
          {isLoading && skeletons(3)}
          {loaded && versions.length === 0 && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              No versions yet
            </Typography>
          )}
          {versions.map((v, i) => {
            return (
              <Stack
                key={v.id}
                data-testid="version-entry"
                direction="row"
                spacing={1.5}
                role="button"
                tabIndex={0}
                aria-current={v.id === versionId ? "true" : undefined}
                onClick={() => selectVersion(v.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    selectVersion(v.id);
                  }
                }}
                sx={{
                  mx: -1,
                  px: 1,
                  pt: 0.75,
                  pb: i === versions.length - 1 ? 0.75 : 1.5,
                  mb: i === versions.length - 1 ? 0 : 0.75,
                  borderRadius: 1,
                  cursor: "pointer",
                  bgcolor:
                    v.id === versionId ? "action.selected" : "transparent",
                  "&:hover": {
                    bgcolor:
                      v.id === versionId ? "action.selected" : "action.hover",
                  },
                  "&:focus-visible": {
                    outline: "2px solid",
                    outlineColor: "primary.main",
                  },
                }}
              >
                <Stack alignItems="center" sx={{ flexShrink: 0, pt: 0.25 }}>
                  <Box
                    data-testid="version-dot"
                    data-selected={v.id === versionId ? "true" : undefined}
                    sx={{
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      bgcolor:
                        v.id === versionId ? "primary.main" : "transparent",
                      border: "2px solid",
                      borderColor:
                        v.id === versionId ? "primary.main" : "text.disabled",
                    }}
                  />
                  {i < versions.length - 1 && (
                    <Box
                      sx={{ width: 1.5, flex: 1, mt: 0.5, bgcolor: "divider" }}
                    />
                  )}
                </Stack>
                <Box flex={1} minWidth={0}>
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={0.75}
                    flexWrap="wrap"
                    rowGap={0.5}
                  >
                    <Typography
                      data-testid="version-label"
                      sx={{
                        typography: "s2",
                        fontWeight: 700,
                        fontFamily: MONO,
                      }}
                    >
                      {v.label}
                    </Typography>
                    {v.isLatest && <Chip label="Latest" tone="primary" />}
                    {v.isActive && <Chip label="Active" />}
                  </Stack>
                  {v.commitMessage && (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        mt: 0.375,
                      }}
                    >
                      {v.commitMessage}
                    </Typography>
                  )}
                  {v.createdAt && (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.subtitle",
                        mt: 0.25,
                      }}
                    >
                      {format(new Date(v.createdAt), "dd-MM-yyyy, h:mm a")}
                    </Typography>
                  )}
                </Box>
              </Stack>
            );
          })}
          {isFetchingNextPage && skeletons(2)}
        </Box>
      </Card>
      <ComingSoonCard
        title="Compare"
        description="Compare any two versions to see exactly what changed."
      />
    </Box>
  );
}
