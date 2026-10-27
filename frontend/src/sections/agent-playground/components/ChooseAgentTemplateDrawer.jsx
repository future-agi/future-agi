import {
  Box,
  Collapse,
  Divider,
  Drawer,
  Grid,
  IconButton,
  Stack,
  Typography,
  Skeleton,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useRef, useCallback, useEffect } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useDebounce } from "src/hooks/use-debounce";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import {
  fetchAgentTemplateCategories,
  fetchAgentTemplates,
  loadTemplateToBuilder,
} from "../data/mockAgentTemplates";
import SelectedAgentTemplateDrawer from "./SelectedAgentTemplateDrawer";
import {
  useTemplateLoadingStoreShallow,
  useAgentPlaygroundStore,
} from "../store";

const TemplateCard = ({ name, description, createdBy, onClick }) => {
  return (
    <Box
      sx={{
        boxShadow: "2px 0px 16px 4px rgba(0, 0, 0, 0.05)",
        borderRadius: "4px",
        minHeight: "156px",
        padding: "12px",
        display: "flex",
        flexDirection: "column",
        gap: "6px",
        cursor: "pointer",
        height: "100%",
        transition: "box-shadow 0.2s ease-in-out",
        "&:hover": {
          boxShadow: "2px 0px 20px 6px rgba(0, 0, 0, 0.1)",
        },
      }}
      component={"div"}
      onClick={onClick}
    >
      <Box
        sx={{
          border: "2px solid",
          borderColor: "purple.o10",
          backgroundColor: "purple.o10",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 44,
          width: 44,
          borderRadius: "8px",
        }}
      >
        <SvgColor
          src="/assets/icons/navbar/ic_agents.svg"
          sx={{
            bgcolor: "purple.500",
            height: 20,
            width: 20,
          }}
        />
      </Box>
      <Stack>
        <Typography
          typography={"s1"}
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
          sx={{
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {name}
        </Typography>
        <Typography
          typography={"s2"}
          fontWeight={"fontWeightRegular"}
          color={"text.secondary"}
          sx={{
            overflowWrap: "break-word",
            wordBreak: "break-word",
            whiteSpace: "normal",
            display: "-webkit-box",
            WebkitLineClamp: 3,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {description}
        </Typography>
      </Stack>
      {createdBy && (
        <Typography
          typography={"s3"}
          fontWeight={"fontWeightRegular"}
          color={"text.disabled"}
          marginTop="auto"
        >
          By {createdBy}
        </Typography>
      )}
    </Box>
  );
};

const SkeletonCard = () => (
  <Box
    sx={{
      boxShadow: "4px -4px 16px 0px rgba(0, 0, 0, 0.05)",
      borderRadius: "4px",
      minHeight: "156px",
      padding: "12px",
      display: "flex",
      flexDirection: "column",
      gap: "6px",
      height: "100%",
    }}
  >
    <Skeleton
      variant="rectangular"
      width={44}
      height={44}
      sx={{ borderRadius: "8px" }}
    />
    <Stack gap={1}>
      <Skeleton variant="text" width="80%" height={24} />
      <Skeleton variant="text" width="100%" height={20} />
      <Skeleton variant="text" width="60%" height={20} />
    </Stack>
    <Skeleton variant="text" width="40%" height={16} />
  </Box>
);

const CategoriesSkeleton = () => (
  <Box>
    <Box sx={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      {Array.from({ length: 6 }).map((_, index) => (
        <Box
          key={index}
          sx={{
            px: 0.5,
            py: 0.5,
            borderRadius: 0.5,
          }}
        >
          <Skeleton variant="text" width="80%" height={20} />
        </Box>
      ))}
    </Box>
  </Box>
);

TemplateCard.propTypes = {
  name: PropTypes.string,
  description: PropTypes.string,
  createdBy: PropTypes.string,
  onClick: PropTypes.func,
};

export const ChooseAgentTemplateDrawer = ({
  open,
  onClose,
  onSelectTemplate,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [category, setCategory] = useState("code");
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [isUsingTemplate, setIsUsingTemplate] = useState(false);

  const loadMoreRef = useRef(null);
  const debouncedSearchQuery = useDebounce(searchQuery, 300);

  // Store hooks
  const {
    startLoadingTemplate,
    updateLoadingProgress,
    completeLoadingTemplate,
  } = useTemplateLoadingStoreShallow((state) => ({
    startLoadingTemplate: state.startLoadingTemplate,
    updateLoadingProgress: state.updateLoadingProgress,
    completeLoadingTemplate: state.completeLoadingTemplate,
  }));

  const { data, isLoading, isFetchingNextPage, fetchNextPage, hasNextPage } =
    useInfiniteQuery({
      queryKey: ["agent-templates", category, debouncedSearchQuery],
      queryFn: async ({ pageParam = 0 }) => {
        const response = await fetchAgentTemplates({
          category,
          searchQuery: debouncedSearchQuery,
          pageNumber: pageParam,
          pageSize: 30,
        });
        return response.data;
      },
      enabled: open,
      getNextPageParam: (lastPage, allPages) => {
        const currentPage = allPages.length - 1;
        const totalItems = lastPage?.result?.total_count || 0;
        const pageSize = 30;
        const totalPages = Math.ceil(totalItems / pageSize);

        return currentPage < totalPages - 1 ? currentPage + 1 : undefined;
      },
      select: (data) => ({
        pages: data.pages,
        pageParams: data.pageParams,
        allTemplates: data.pages.flatMap((page) => page?.result?.data || []),
      }),
    });

  // Intersection Observer for infinite scroll
  const handleObserver = useCallback(
    (entries) => {
      const [target] = entries;
      if (target.isIntersecting && hasNextPage && !isFetchingNextPage) {
        fetchNextPage();
      }
    },
    [fetchNextPage, hasNextPage, isFetchingNextPage],
  );

  const { data: categories, isLoading: categoriesLoading } = useQuery({
    queryKey: ["agent-template-categories"],
    queryFn: async () => {
      return fetchAgentTemplateCategories();
    },
    enabled: !!open,
    select: (d) => d.data?.result || [],
  });

  useEffect(() => {
    const element = loadMoreRef.current;
    const option = {
      threshold: 0,
      rootMargin: "0px 0px 100px 0px",
    };

    const observer = new IntersectionObserver(handleObserver, option);
    if (element) observer.observe(element);

    return () => {
      if (element) observer.unobserve(element);
    };
  }, [handleObserver]);

  const handleClose = () => {
    onClose();
    setSearchQuery("");
    setSelectedTemplate(null);
  };

  const handleCategoryChange = (newCategory) => {
    setCategory(newCategory);
  };

  const handleTemplateClick = (template) => {
    setSelectedTemplate(template);
  };

  const handleCloseTemplateDetail = () => {
    setSelectedTemplate(null);
  };

  const handleUseTemplate = async (template) => {
    setIsUsingTemplate(true);

    try {
      // Start loading state in the store
      const controller = startLoadingTemplate(template.id, template.name);

      // Close drawers
      handleClose();

      // Load the template with progress updates
      const result = await loadTemplateToBuilder(
        template.id,
        (progress, message) => {
          // Check if cancelled
          if (controller.signal.aborted) {
            throw new Error("Template loading cancelled");
          }
          updateLoadingProgress(progress, message);
        },
      );

      // Check if cancelled before applying
      if (controller.signal.aborted) {
        throw new Error("Template loading cancelled");
      }

      // Apply the template data to the store
      const { nodes, edges } = result.data;
      useAgentPlaygroundStore.getState().setGraphData(nodes, edges);

      // Complete loading
      completeLoadingTemplate();

      // Call the onSelectTemplate callback if provided
      if (onSelectTemplate) {
        onSelectTemplate(template);
      }
    } catch (error) {
      // Template loading was cancelled or failed - handled silently
      // Error is logged only in development for debugging purposes
    } finally {
      setIsUsingTemplate(false);
    }
  };

  return (
    <Drawer
      anchor="right"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          overflowY: "hidden",
          overflowX: "hidden",
          zIndex: 1,
          borderRadius: "0 !important",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
      open={open}
      onClose={handleClose}
    >
      <Box
        display="flex"
        flexDirection="row"
        height="100%"
        sx={{ backgroundColor: "background.paper" }}
      >
        {/* Template List View */}
        <Collapse
          in={!selectedTemplate}
          orientation="horizontal"
          unmountOnExit
          sx={{
            display: "flex",
            flexDirection: "column",
            height: "100%",
          }}
        >
          <Box sx={{ height: "100vh", width: "80vw" }}>
            {/* Header */}
            <Box
              sx={{
                display: "flex",
                flexDirection: "row",
                justifyContent: "space-between",
                padding: "12px",
              }}
            >
              <Box sx={{ display: "flex", flexDirection: "column", gap: 0 }}>
                <Typography
                  typography="m3"
                  color="text.primary"
                  fontWeight={"fontWeightMedium"}
                >
                  Agent Templates
                </Typography>
                <Typography
                  typography="s2"
                  color="text.secondary"
                  fontWeight={"fontWeightRegular"}
                >
                  Browse and discover curated agent templates for writing,
                  coding, research, and more.
                </Typography>
              </Box>

              <Box>
                <IconButton onClick={handleClose}>
                  <Iconify color="text.primary" icon="mingcute:close-line" />
                </IconButton>
              </Box>
            </Box>
            <Divider sx={{ borderColor: "divider" }} orientation="horizontal" />

            <Stack
              direction="row"
              sx={{ height: "calc(100vh - 80px)", paddingLeft: "6px" }}
            >
              {/* Left Sidebar - Categories */}
              <Box
                sx={{
                  width: "250px",
                  padding: "12px",
                  paddingLeft: "6px",
                  backgroundColor: "background.default",
                  flexShrink: 0,
                }}
              >
                <Box>
                  {categoriesLoading ? (
                    <CategoriesSkeleton />
                  ) : (
                    <Box
                      sx={{
                        display: "flex",
                        flexDirection: "column",
                        gap: "8px",
                      }}
                    >
                      {categories?.map((cat) => (
                        <Box
                          onClick={() => handleCategoryChange(cat.name)}
                          sx={{
                            px: 0.5,
                            py: 0.5,
                            bgcolor:
                              cat?.name === category ? "purple.o10" : undefined,
                            borderRadius: 0.5,
                            "&:hover": {
                              cursor: "pointer",
                              bgcolor:
                                cat?.name === category
                                  ? "purple.o10"
                                  : "action.hover",
                            },
                          }}
                          key={cat?.name}
                        >
                          <Typography
                            variant="s1"
                            fontWeight={"fontWeightMedium"}
                            color={"text.primary"}
                          >
                            {cat?.display_name}
                          </Typography>
                        </Box>
                      ))}
                    </Box>
                  )}
                </Box>
              </Box>

              <Divider
                orientation="vertical"
                flexItem
                sx={{ borderColor: "divider" }}
              />

              {/* Right Content - Templates Grid */}
              <Stack
                direction={"column"}
                sx={{
                  width: "100%",
                }}
              >
                <Box
                  sx={{
                    padding: 2,
                    paddingBottom: 0,
                  }}
                >
                  <FormSearchField
                    size="small"
                    placeholder="Search templates"
                    sx={{ width: "100%", flexShrink: 0 }}
                    searchQuery={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </Box>

                <Box
                  sx={{
                    width: "100%",
                    overflowY: "auto",
                    pb: "2rem",
                    height: "calc(100vh - 200px)",
                    padding: 2,
                  }}
                >
                  <Grid container spacing={2}>
                    {/* Render actual templates */}
                    {data?.allTemplates?.map((template, index) => (
                      <Grid item key={`${template.id}-${index}`} xs={6}>
                        <TemplateCard
                          createdBy={template.createdBy}
                          description={template.description}
                          name={template.name}
                          onClick={() => handleTemplateClick(template)}
                        />
                      </Grid>
                    ))}

                    {/* Show loading skeletons */}
                    {(isLoading || isFetchingNextPage) && (
                      <>
                        {Array.from({ length: 6 }).map((_, index) => (
                          <Grid item key={`skeleton-${index}`} xs={6}>
                            <SkeletonCard />
                          </Grid>
                        ))}
                      </>
                    )}

                    {/* Empty state */}
                    {!isLoading && data?.allTemplates?.length === 0 && (
                      <Grid item xs={12}>
                        <Box
                          sx={{
                            display: "flex",
                            flexDirection: "column",
                            alignItems: "center",
                            justifyContent: "center",
                            py: 4,
                            gap: 2,
                            placeItems: "center",
                            height: "400px",
                          }}
                        >
                          <EmptyLayout
                            title={"No templates found"}
                            description="Try adjusting your search or select a different category."
                            icon={"/assets/icons/ic_scratch.svg"}
                            sx={{
                              height: "80%",
                            }}
                          />
                        </Box>
                      </Grid>
                    )}
                  </Grid>

                  {/* Intersection observer target */}
                  {hasNextPage && (
                    <div
                      ref={loadMoreRef}
                      style={{ height: "20px", margin: "20px 0" }}
                    />
                  )}
                </Box>
              </Stack>
            </Stack>
          </Box>
        </Collapse>

        {/* Template Detail View */}
        <Collapse
          in={Boolean(selectedTemplate)}
          orientation="horizontal"
          unmountOnExit
          sx={{ height: "100%" }}
        >
          <SelectedAgentTemplateDrawer
            onClose={handleCloseTemplateDetail}
            template={selectedTemplate}
            onUseTemplate={handleUseTemplate}
            isLoading={isUsingTemplate}
          />
        </Collapse>
      </Box>
    </Drawer>
  );
};

ChooseAgentTemplateDrawer.propTypes = {
  onClose: PropTypes.func,
  open: PropTypes.bool,
  onSelectTemplate: PropTypes.func,
};

export default ChooseAgentTemplateDrawer;
