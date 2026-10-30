import React, { useEffect, useState } from "react";
import { format } from "date-fns";
import { useLocation, useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import { Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import EmptyState from "src/sections/simulate/environments/components/EmptyState";
import {
  useDiscardVersion,
  useRetryVersion,
  useSetProduction,
} from "src/api/agents/agentDetail";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import ComingSoonCard from "./ComingSoonCard";
import { Card, LineSkeleton, Pill } from "./bits";
import { agentDetailPath } from "./agentDetailTabs";
import { MONO } from "./format";
import useAgentRoute from "./useAgentRoute";
import usePinnedVersions from "./usePinnedVersions";
import {
  CHECK_LABEL,
  ORIGIN_LABEL,
  canDiscard,
  canPromote,
  canRetry,
  environmentsLabel,
} from "./versionLabels";

const VERSION_ACTION = { typography: "s2", minWidth: 0, height: 24, px: 1 };

// The row is a button too; keys pressed on an action stay with the action.
const actionProps = (onClick) => ({
  size: "small",
  variant: "outlined",
  color: "inherit",
  sx: VERSION_ACTION,
  onClick: (e) => {
    e.stopPropagation();
    onClick();
  },
  onKeyDown: (e) => e.stopPropagation(),
});

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
        <Typography sx={{ typography: "s2", mt: 0.375 }}>
          <LineSkeleton width="80%" />
        </Typography>
        <Typography sx={{ typography: "s2", mt: 0.25 }}>
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
 * Versions: a timeline from the paginated versions list (the one the Agent
 * Definition page uses), newest first. Each version can be viewed on the
 * Definition tab or made the one in production; environments keep the
 * version each is pinned to. Compare is coming soon.
 */
export default function AgentVersionsTab() {
  const { agentId, agent, versionId, selectVersion } = useAgentRoute();
  const navigate = useNavigate();
  const { search } = useLocation();
  const { enqueueSnackbar } = useSnackbar();
  const activate = useSetProduction(agentId);
  const retry = useRetryVersion(agentId);
  const discard = useDiscardVersion(agentId);
  const { role } = useAuthContext();
  const canEdit = !!RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role];
  const [toActivate, setToActivate] = useState(null);
  const [toDiscard, setToDiscard] = useState(null);
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

  // The Definition tab for a version: `?v=` as the picker writes it, so the
  // version in production keeps a clean URL.
  const viewDefinition = (v) => {
    const params = new URLSearchParams(search);
    if (v.id === agent?.activeVersionId) params.delete("v");
    else params.set("v", v.id);
    const query = params.toString();
    navigate(
      `${agentDetailPath(agentId, "definition")}${query ? `?${query}` : ""}`,
    );
  };

  const retryCheck = (v) =>
    retry.mutate(
      { versionId: v.id },
      {
        onSuccess: () =>
          enqueueSnackbar(`${v.label} is being checked again`, {
            variant: "info",
          }),
        onError: (e) =>
          enqueueSnackbar(errorMessage(e), {
            variant: "error",
          }),
      },
    );

  const discardVersion = () => {
    const v = toDiscard;
    discard.mutate(
      { versionId: v.id },
      {
        onSuccess: () => {
          setToDiscard(null);
          enqueueSnackbar(`${v.label} discarded`, { variant: "success" });
        },
        onError: (e) =>
          enqueueSnackbar(errorMessage(e), {
            variant: "error",
          }),
      },
    );
  };

  const makeActive = () => {
    const v = toActivate;
    activate.mutate(
      { versionId: v.id },
      {
        onSuccess: (data) => {
          setToActivate(null);
          const moved = data?.moved_environment_ids?.length || 0;
          enqueueSnackbar(
            moved
              ? `${v.label} is now in production; ${moved} environment${moved === 1 ? "" : "s"} following production moved to it.`
              : `${v.label} is now in production. Pinned environments keep the version each is pinned to.`,
            { variant: "success" },
          );
        },
        onError: (e) =>
          enqueueSnackbar(errorMessage(e), {
            variant: "error",
          }),
      },
    );
  };

  return (
    <Box
      sx={{
        display: "grid",
        gap: 2,
        gridTemplateColumns: "minmax(300px, 1fr) 1fr",
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
                  color="inherit"
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
            const selected = v.id === versionId;
            return (
              <Stack
                key={v.id}
                data-testid="version-entry"
                direction="row"
                spacing={1.5}
                aria-current={selected ? "true" : undefined}
                sx={{
                  mx: -1,
                  px: 1,
                  pt: 0.75,
                  pb: i === versions.length - 1 ? 0.75 : 1.5,
                  mb: i === versions.length - 1 ? 0 : 0.75,
                  borderRadius: 1,
                  bgcolor: selected ? "action.selected" : "transparent",
                }}
              >
                <Stack alignItems="center" sx={{ flexShrink: 0, pt: 0.25 }}>
                  <Box
                    data-testid="version-dot"
                    data-selected={selected ? "true" : undefined}
                    sx={{
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      bgcolor: selected ? "primary.main" : "transparent",
                      border: "2px solid",
                      borderColor: selected ? "primary.main" : "text.disabled",
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
                      component="button"
                      type="button"
                      data-testid="version-label"
                      aria-pressed={selected}
                      onClick={() => selectVersion(v.id)}
                      sx={{
                        typography: "s2",
                        fontWeight: 500,
                        fontFamily: MONO,
                        background: "none",
                        border: 0,
                        p: 0,
                        color: "inherit",
                        cursor: "pointer",
                        "&:hover": { textDecoration: "underline" },
                      }}
                    >
                      {v.label}
                    </Typography>
                    {v.isLatest && <Pill tone="primary">Latest</Pill>}
                    {v.isActive && <Pill>In production</Pill>}
                    {v.isDraft && !v.isActive && !CHECK_LABEL[v.check] && (
                      <Pill>Draft</Pill>
                    )}
                    {CHECK_LABEL[v.check] && (
                      <Pill>{CHECK_LABEL[v.check]}</Pill>
                    )}
                    {ORIGIN_LABEL[v.origin] && (
                      <Pill>{ORIGIN_LABEL[v.origin]}</Pill>
                    )}
                    {environmentsLabel(v.environmentsCount) && (
                      <Pill>{environmentsLabel(v.environmentsCount)}</Pill>
                    )}
                  </Stack>
                  {v.commitMessage && (
                    <Typography
                      sx={{
                        typography: "s2",
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
                        typography: "s2",
                        color: "text.subtitle",
                        mt: 0.25,
                      }}
                    >
                      {format(new Date(v.createdAt), "dd-MM-yyyy, h:mm a")}
                    </Typography>
                  )}
                  <Stack
                    direction="row"
                    alignItems="center"
                    gap={0.75}
                    flexWrap="wrap"
                    sx={{ mt: 0.75 }}
                  >
                    <Button {...actionProps(() => viewDefinition(v))}>
                      View definition
                    </Button>
                    {canEdit && agent && !agent.managedBy && canPromote(v) && (
                      <Button {...actionProps(() => setToActivate(v))}>
                        Set as production
                      </Button>
                    )}
                    {canEdit && canRetry(v) && (
                      <Button {...actionProps(() => retryCheck(v))}>
                        Retry
                      </Button>
                    )}
                    {canEdit && canDiscard(v) && (
                      <Button {...actionProps(() => setToDiscard(v))}>
                        Discard
                      </Button>
                    )}
                  </Stack>
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
      <ConfirmDialog
        open={!!toActivate}
        onClose={() => (activate.isPending ? null : setToActivate(null))}
        closeDisabled={activate.isPending}
        title={toActivate ? `Set ${toActivate.label} as production?` : ""}
        content={
          <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
            It becomes the version in production. Environments that follow
            production move to it; pinned ones keep their version and are
            offered this one.
          </Typography>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={activate.isPending}
            onClick={makeActive}
          >
            Set as production
          </Button>
        }
      />
      <ConfirmDialog
        open={!!toDiscard}
        onClose={() => (discard.isPending ? null : setToDiscard(null))}
        closeDisabled={discard.isPending}
        title={toDiscard ? `Discard ${toDiscard.label}?` : ""}
        content={
          <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
            It stays in the list, set aside; nothing runs it and it is never the
            one in production.
          </Typography>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            disabled={discard.isPending}
            onClick={discardVersion}
          >
            Discard
          </Button>
        }
      />
    </Box>
  );
}
