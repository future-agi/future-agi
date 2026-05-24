import {
  Box,
  Button,
  Grid,
  Skeleton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { QueryClient, useInfiniteQuery } from "@tanstack/react-query";
import React, { useState, useEffect } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "../../../utils/axios";
import GroupCard from "./GroupCard";
import SvgColor from "../../../components/svg-color";
import { useEvalStore } from "../store/useEvalStore";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { useNavigate } from "react-router";
import DeleteGroupModal from "./DeleteGroup";
import EvalPlayground from "../EvalPlayground/EvalPlayground";
import PropTypes from "prop-types";
import { useEvaluationContext } from "../../common/EvaluationDrawer/context/EvaluationContext";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

export default function GroupsGrid({ isEvalsView, onGroupSelect }) {
  const { role } = useAuthContext();
  const theme = useTheme();
  const [searchQuery, setSearchQuery] = useState("");
  const navigate = useNavigate();
  const { setVisibleSection, setCurrentTab } = useEvaluationContext();

  const [openDeleteModal, setOpenDeleteModal] = useState(null);
  const [playgroundEvaluation, setPlaygroundEvaluation] = useState(null);
  const debouncedSearch = useDebounce(searchQuery.trim(), 300);
  const { setCreateGroupMode } = useEvalStore();
  const queryClient = new QueryClient();

  const { data, isLoading, isFetchingNextPage, hasNextPage, fetchNextPage } =
    useInfiniteQuery({
      queryKey: ["eval-groups", debouncedSearch],
      queryFn: async ({ pageParam = 0 }) => {
        const response = await axios.get(endpoints.develop.eval.groupEvals, {
          params: {
            page_number: pageParam,
            page_size: 12,
            ...(debouncedSearch && { name: debouncedSearch }),
          },
        });
        return response.data;
      },
      getNextPageParam: (lastPage, allPages) => {
        const currentPage = allPages.length - 1; // adjust since we start at 0
        const totalPages = lastPage?.result?.total_pages;

        if (currentPage + 1 < totalPages) {
          return currentPage + 1; // next page
        }
        return undefined; // no more pages
      },
      initialPageParam: 0,
    });

  // Flatten all pages data into a single array
  const allGroups =
    data?.pages?.flatMap((page) => evalGroupsPagePayload(page)?.data || []) ||
    [];
  const totalCount = evalGroupsPagePayload(data?.pages?.[0])?.total_count || 0;

  const scrollContainerRef = useScrollEnd(() => {
    if (!isLoading && !isFetchingNextPage && hasNextPage) {
      fetchNextPage();
    }
  }, [isLoading, isFetchingNextPage, hasNextPage, fetchNextPage]);

  // Check if we need to load more data when content doesn't fill the container
  useEffect(() => {
    if (
      !isLoading &&
      !isFetchingNextPage &&
      hasNextPage &&
      allGroups.length > 0
    ) {
      const container = scrollContainerRef.current;
      if (container) {
        const { scrollHeight, clientHeight } = container;
        // If content doesn't fill the container, fetch more
        if (scrollHeight <= clientHeight) {
          fetchNextPage();
        }
      }
    }
  }, [
    allGroups.length,
    isLoading,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
    scrollContainerRef,
  ]);

  const handleCreate = () => {
    setCreateGroupMode(true);
    if (isEvalsView) {
      navigate("/dashboard/evaluations", {
        state: { createMode: true },
      });
    } else {
      setVisibleSection("config");
      setCurrentTab("evals");
    }
  };

  const handleOpenPlayGround = (groupId) => {
    const selectedgroup = allGroups.find((g) => g?.id === groupId);
    setPlaygroundEvaluation({
      name: selectedgroup?.name,
      evalTemplateName: selectedgroup?.name,
      evalRequiredKeys: selectedgroup?.required_keys,
      evalTemplateTags: ["FUTUREAGI_BUILT", "FUNCTION"],
      description: selectedgroup?.description,
      isModelRequired: true,
      type: "futureagi_built",
      evalsActionType: "playground",
      isGroupEvals: true,
    });
  };

  const onClosePlayground = () => {
    queryClient.invalidateQueries({
      queryKey: ["user-eval-list"],
    });
    setPlaygroundEvaluation(null);
  };

  return (
    <>
      <Stack
        direction={"column"}
        gap={2}
        sx={{
          paddingBottom: theme.spacing(4),
          height: "calc(100vh - 130px)",
          overflow: "hidden",
        }}
      >
        <Stack
          justifyContent={"space-between"}
          direction={"row"}
          alignItems={"center"}
        >
          <FormSearchField
            size="small"
            placeholder="Search"
            sx={{ minWidth: "360px" }}
            searchQuery={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            disabled={isLoading && !searchQuery}
          />
          <Button
            onClick={handleCreate}
            color="primary"
            variant="outlined"
            disabled={
              !RolePermission.EVALS[PERMISSIONS.EDIT_CREATE_DELETE_EVALS][role]
            }
            startIcon={
              <SvgColor
                src="/assets/icons/ic_add.svg"
                sx={{ color: "inherit" }}
              />
            }
          >
            Create Groups
          </Button>
        </Stack>

        <Stack gap={1} sx={{ flex: 1, overflow: "hidden" }}>
          <Typography
            fontWeight={"fontWeightMedium"}
            typography="s2"
            color={"text.primary"}
          >
            Groups ({totalCount})
          </Typography>

          <Box
            ref={scrollContainerRef}
            sx={{
              flex: 1,
              overflow: "auto",
              paddingRight: theme.spacing(0.5),
            }}
          >
            {isLoading && allGroups.length === 0 ? (
              <Grid container spacing={2}>
                {Array.from({ length: 12 }).map((_, index) => (
                  <Grid item key={index} xs={12} sm={6} md={4}>
                    <Box
                      key={index}
                      display="flex"
                      justifyContent="center"
                      alignItems="center"
                      minHeight="200px"
                    >
                      <Skeleton
                        variant="rectangular"
                        width="100%"
                        height={250}
                      />
                    </Box>
                  </Grid>
                ))}
              </Grid>
            ) : (
              <Grid container spacing={2}>
                {allGroups?.map((group) => {
                  return (
                    <Grid item key={group?.id} xs={12} sm={6} md={4}>
                      <GroupCard
                        description={group?.description}
                        name={group?.name}
                        requiredInputs={group?.required_keys}
                        evaluations={group?.evals_count ?? 0}
                        onDelete={() => setOpenDeleteModal(group?.id)}
                        onPlaygroundClick={(e) => {
                          e.stopPropagation();
                          handleOpenPlayGround(group?.id);
                        }}
                        isSample={group?.is_sample}
                        id={group?.id}
                        isEvalsView={isEvalsView}
                        onClick={
                          typeof onGroupSelect === "function"
                            ? (id) => onGroupSelect(id)
                            : undefined
                        }
                      />
                    </Grid>
                  );
                })}
              </Grid>
            )}

            {/* Show loading indicator when fetching next page */}
            {isFetchingNextPage && (
              <Grid container spacing={2} sx={{ mt: 2 }}>
                {Array.from({ length: 3 }).map((_, index) => (
                  <Grid item key={`loading-${index}`} xs={12} sm={6} md={4}>
                    <Box
                      display="flex"
                      justifyContent="center"
                      alignItems="center"
                      minHeight="200px"
                    >
                      <Skeleton
                        variant="rectangular"
                        width="100%"
                        height={250}
                      />
                    </Box>
                  </Grid>
                ))}
              </Grid>
            )}

            {!isLoading && allGroups.length === 0 && debouncedSearch && (
              <Box
                display="flex"
                justifyContent="center"
                alignItems="center"
                minHeight="200px"
              >
                <Typography variant="body2" color="text.secondary">
                  No groups found
                </Typography>
              </Box>
            )}

            {!isLoading && allGroups.length === 0 && !debouncedSearch && (
              <EmptyLayout
                title={"No groups has been added"}
                description={
                  "Create a new group to organize related evaluations with shared variable mappings."
                }
                hideIcon
              />
            )}
          </Box>
        </Stack>
      </Stack>
      <DeleteGroupModal
        open={openDeleteModal !== null}
        id={openDeleteModal}
        onClose={() => setOpenDeleteModal(null)}
      />

      <EvalPlayground
        open={playgroundEvaluation?.evalsActionType === "playground"}
        onClose={onClosePlayground}
        evaluation={playgroundEvaluation}
      />
    </>
  );
}

GroupsGrid.propTypes = {
  isEvalsView: PropTypes.bool,
  onGroupSelect: PropTypes.func,
};

function evalGroupsPagePayload(page) {
  return page?.result || page;
}
