import { Box, Button, Popper, Skeleton, Typography } from "@mui/material";
import React, { useEffect, useMemo, useRef } from "react";
import PropTypes from "prop-types";
import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import SVGColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { useCreateWorkspaceModal } from "../states";
import { useScrollEnd } from "../../../hooks/use-scroll-end";
import { ShowComponent } from "../../../components/show";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useOrganization } from "src/contexts/OrganizationContext";

const SelectWorkspaceChild = React.forwardRef(
  ({ setOpen }, popperTimeoutRef) => {
    const {
      currentWorkspaceId,
      currentWorkspaceDisplayName,
      switchWorkspace,
      updateWorkspaceName,
    } = useWorkspace();
    const { orgLevel } = useOrganization();
    const newWorkspaceId = useRef(null);
    const canCreateWorkspace = typeof orgLevel === "number" && orgLevel >= 8;

    const { data, fetchNextPage, isPending, isFetchingNextPage } =
      useInfiniteQuery({
        queryFn: ({ pageParam }) =>
          axios.get(endpoints.workspaces.list, {
            params: {
              page: pageParam,
              limit: 10,
            },
          }),
        queryKey: ["workspaces-list"],
        getNextPageParam: ({ data }) =>
          data?.next ? data?.current_page + 1 : null,
        initialPageParam: 1,
        staleTime: Infinity,
      });

    const { mutate: doSwitch, isPending: isSwitching } = useMutation({
      mutationFn: (newId) => switchWorkspace(newId, currentWorkspaceId),
      onSuccess: (_response, newId) => {
        trackEvent(Events.workspaceNewWorkspaceSelected, {
          workspaces: {
            oldWorkSpaceId: currentWorkspaceId,
            newWorkspaceId: newId,
          },
        });
      },
      onError: () => {
        // switchWorkspace already shows a snackbar on error
      },
    });

    const workspaces = useMemo(
      () => data?.pages.flatMap((page) => page.data.results),
      [data],
    );

    // Sync sidebar display name with the authoritative workspace list
    useEffect(() => {
      if (!workspaces || !currentWorkspaceId) return;
      const current = workspaces.find((ws) => ws.id === currentWorkspaceId);
      const latestName = current?.display_name || current?.name;
      if (latestName && latestName !== currentWorkspaceDisplayName) {
        updateWorkspaceName(latestName);
      }
    }, [
      workspaces,
      currentWorkspaceId,
      currentWorkspaceDisplayName,
      updateWorkspaceName,
    ]);

    const { setOpen: setCreateWorkspaceModalOpen } = useCreateWorkspaceModal();

    const scrollRef = useScrollEnd(() => {
      if (isPending || isFetchingNextPage) return;
      fetchNextPage();
    }, [fetchNextPage, isFetchingNextPage, isPending]);

    return (
      <Box
        onMouseEnter={() => {
          // Clear any existing timeout when entering popper
          if (popperTimeoutRef.current) {
            clearTimeout(popperTimeoutRef.current);
            popperTimeoutRef.current = null;
          }
          setOpen(true);
        }}
        onMouseLeave={() => {
          // Add a small delay before closing
          popperTimeoutRef.current = setTimeout(() => {
            setOpen(false);
          }, 100);
        }}
        sx={{
          backgroundColor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          boxShadow: "0px 4px 20px rgba(0, 0, 0, 0.1)",
          p: 1,
          minWidth: "200px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1,
            maxHeight: "200px",
            overflowY: "auto",
          }}
          ref={scrollRef}
        >
          <ShowComponent condition={isPending}>
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: 0.5 }}
              height={20}
            />
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: 0.5 }}
              height={20}
            />
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: 0.5 }}
              height={20}
            />
          </ShowComponent>
          <ShowComponent condition={!isPending}>
            {workspaces?.map((workspace) => (
              <Box
                key={workspace.id}
                onClick={() => {
                  if (isSwitching || workspace.id === currentWorkspaceId)
                    return;
                  setOpen(false); // Close popper after selection

                  newWorkspaceId.current = workspace.id;
                  doSwitch(workspace.id);
                }}
                sx={{
                  px: 1,
                  py: 0.5,
                  cursor: isSwitching ? "wait" : "pointer",
                  borderRadius: 0.5,
                  backgroundColor:
                    workspace.id === currentWorkspaceId
                      ? "background.neutral"
                      : "background.paper",
                  "&:hover": {
                    backgroundColor: "background.neutral",
                  },
                  opacity: isSwitching ? 0.6 : 1,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <Typography
                  typography="s2_1"
                  color="text.primary"
                  fontWeight={500}
                >
                  {workspace.display_name || workspace.name}
                </Typography>
                <ShowComponent condition={workspace.id === currentWorkspaceId}>
                  <Iconify
                    icon="eva:checkmark-fill"
                    sx={{ width: 16, height: 16, color: "primary.main" }}
                  />
                </ShowComponent>
              </Box>
            ))}
          </ShowComponent>
          <ShowComponent condition={isFetchingNextPage}>
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: 0.5, height: "20px", minHeight: "20px" }}
            />
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: 0.5, height: "20px", minHeight: "20px" }}
            />
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: 0.5, height: "20px", minHeight: "20px" }}
            />
          </ShowComponent>
        </Box>
        <Box>
          <ShowComponent condition={canCreateWorkspace}>
            <Button
              variant="text"
              color="primary"
              fullWidth
              size="small"
              startIcon={
                <SVGColor
                  src="/assets/icons/ic_add.svg"
                  sx={{ color: "inherit" }}
                />
              }
              sx={{ justifyContent: "flex-start" }}
              onClick={() => {
                trackEvent(Events.workspaceCreateClicked, {
                  [PropertyName.click]: "click",
                });
                setCreateWorkspaceModalOpen(true);
              }}
            >
              Create Workspace
            </Button>
          </ShowComponent>
        </Box>
      </Box>
    );
  },
);

SelectWorkspaceChild.displayName = "SelectWorkspaceChild";

SelectWorkspaceChild.propTypes = {
  setOpen: PropTypes.func,
};

const SelectWorkspace = React.forwardRef(
  ({ open, anchorEl, setOpen }, popperTimeoutRef) => {
    return (
      <Popper
        open={open}
        anchorEl={anchorEl}
        placement="right-start"
        modifiers={[
          {
            name: "offset",
            options: {
              offset: [0, 8],
            },
          },
        ]}
        style={{ zIndex: 1300 }}
      >
        <SelectWorkspaceChild setOpen={setOpen} ref={popperTimeoutRef} />
      </Popper>
    );
  },
);

SelectWorkspace.displayName = "SelectWorkspace";

SelectWorkspace.propTypes = {
  open: PropTypes.bool,
  anchorEl: PropTypes.object,
  setOpen: PropTypes.func,
};

export default SelectWorkspace;
