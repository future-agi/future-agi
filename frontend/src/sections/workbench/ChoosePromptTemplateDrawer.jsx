import {
  Box,
  Divider,
  Drawer,
  Grid,
  IconButton,
  Stack,
  Typography,
  Skeleton,
  Button,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useRef, useCallback, useEffect } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import { SelectedPromptTemplateDrawer } from "./SelectedPromptTemplateDrawer";
import SvgColor from "src/components/svg-color";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import _ from "lodash";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "../../hooks/use-debounce";
import { extractTextFromPrompt } from "../../components/ImprovePromptDrawer/common";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { usePromptStore } from "../workbench-v2/store/usePromptStore";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { PropertyName } from "../../utils/Mixpanel";

const TemplateCard = ({ name, description, createdBy, onClick }) => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor:
          theme.palette.mode === "dark"
            ? theme.palette.action.disabled
            : "divider",
        boxShadow:
          theme.palette.mode === "dark"
            ? `0 4px 12px ${theme.palette.action.disabled}`
            : `0 2px 8px ${theme.palette.action.hover}`,
        borderRadius: "4px",
        minHeight: "156px",
        padding: "12px",
        display: "flex",
        flexDirection: "column",
        gap: "6px",
        cursor: "pointer",
        height: "100%",
        transition: "box-shadow 0.3s ease, border-color 0.3s ease",
        "&:hover": {
          boxShadow:
            theme.palette.mode === "dark"
              ? `0 6px 16px ${theme.palette.action.disabledBackground}`
              : `0 4px 12px ${theme.palette.action.focus}`,
        },
      }}
      component={"div"}
      onClick={onClick}
    >
      <Box
        sx={{
          border: "2px solid",
          borderColor: "divider",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 44,
          width: 44,
          borderRadius: "8px",
        }}
      >
        <SvgColor
          src="/assets/icons/ic_prompt_template.svg"
          sx={{
            border: "2.22px solid",
            bgcolor: "green.500",
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
            whiteSpace: "nowrap", //  prevent wrapping
            overflow: "hidden", // hide overflowed text
            textOverflow: "ellipsis", // add ellipsis        // required to limit container width
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

const SkeletonCard = () => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        boxShadow: `4px -4px 16px 0px ${theme.palette.action.hover}`,
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
};

const CategoriesSkeleton = () => (
  <Box>
    <Skeleton variant="text" width="60%" height={16} sx={{ mb: 1 }} />
    <Box sx={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      {Array.from({ length: 5 }).map((_, index) => (
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

export const ChoosePromptTemplateDrawer = ({ open, onClose, importMode }) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [category, setCategory] = useState("templates");
  const [selectedPromptTemplate, setSelectedPromptTemplate] = useState({
    id: "",
    name: "",
    desc: "",
    promptConfig: null,
  });

  const loadMoreRef = useRef(null);
  const debouncedSearchQuery = useDebounce(searchQuery, 300);
  const { setNewPromptModal } = usePromptStore();

  const { data, isLoading, isFetchingNextPage, fetchNextPage, hasNextPage } =
    // @ts-ignore
    useInfiniteQuery({
      queryKey: ["prompt-templates", category, debouncedSearchQuery],
      queryFn: async ({ pageParam = 0 }) => {
        const response = await axios.get(
          endpoints.develop.runPrompt.promptTemplate,
          {
            params: {
              page_size: 30,
              page_number: pageParam,
              ...(category !== "templates" && { category }),
              ...(debouncedSearchQuery && { name: debouncedSearchQuery }),
            },
          },
        );
        return response.data;
      },
      enabled: open,
      getNextPageParam: (lastPage, allPages) => {
        const currentPage = allPages.length - 1;
        const totalItems = lastPage?.result?.total_count || 0;
        const pageSize = 10;
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
    queryKey: ["template-categories"],
    queryFn: async () => {
      return axios.get(endpoints.develop.runPrompt.categories);
    },
    enabled: !!open,
    select: (d) =>
      (d.data?.result || [])
        .filter((c) => c !== null)
        .map((c) => ({
          name: c,
          displayName: _.capitalize(c),
        })),
  });

  useEffect(() => {
    const element = loadMoreRef.current;
    const option = {
      threshold: 0,
      rootMargin: "0px 0px 100px 0px", // Trigger 100px before reaching the element
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
  };

  const handleCategoryChange = (newCategory) => {
    setCategory(newCategory);
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
          width: "90%",
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
      {/* select template header */}
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
            {importMode ? "Import Templates" : "Prompt Templates"}
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            fontWeight={"fontWeightRegular"}
          >
            Browse and discover curated prompt templates for writing,
            coding,research and more.
          </Typography>
        </Box>

        <Box>
          <IconButton onClick={handleClose}>
            <Iconify color="text.primary" icon="mingcute:close-line" />
          </IconButton>
        </Box>
      </Box>
      <Divider sx={{ borderColor: "divider" }} orientation="horizontal" />
      <Stack direction="row" sx={{ height: "100%", paddingLeft: "6px" }}>
        <Box
          sx={{
            width: "250px",
            padding: "12px",
            paddingLeft: "6px",
            flexShrink: 0,
          }}
        >
          <Box
            onClick={() => {
              trackEvent(Events.promptMyTemplatesClicked, {
                [PropertyName.source]: "use-template-drawer",
              });
              handleCategoryChange("templates");
            }}
            sx={{
              display: "flex",
              flexDirection: "row",
              mb: "10px",
              gap: "10px",
              alignItems: "center",
              px: 0.5,
              py: 0.5,
              borderRadius: "4px",
              backgroundColor:
                category === "templates" ? "action.hover" : undefined,
              "&:hover": {
                cursor: "pointer",
              },
            }}
          >
            <SvgColor
              sx={{ height: 20, width: 20, color: "primary.main" }}
              src={
                category === "templates"
                  ? "/assets/icons/ic_open_folder.svg"
                  : "/assets/icons/ic_folder.svg"
              }
            />
            <Typography
              variant="s1"
              color={"text.primary"}
              fontWeight={"fontWeightMedium"}
            >
              My templates
            </Typography>
          </Box>

          <Box>
            <Typography
              color={"text.disabled"}
              typography={"s2"}
              fontWeight={"fontWeightMedium"}
              marginBottom={1}
            >
              Categories
            </Typography>
            {categoriesLoading ? (
              <CategoriesSkeleton />
            ) : (
              <Box
                sx={{ display: "flex", flexDirection: "column", gap: "8px" }}
              >
                {categories?.map((cat) => (
                  <Box
                    onClick={() => handleCategoryChange(cat.name)}
                    sx={{
                      px: 0.5,
                      py: 0.5,
                      bgcolor:
                        cat?.name === category ? "action.hover" : undefined,
                      borderRadius: 0.5,
                      "&:hover": {
                        cursor: "pointer",
                      },
                    }}
                    key={cat?.name}
                  >
                    <Typography
                      variant="s1"
                      fontWeight={"fontWeightMedium"}
                      color={"text.primary"}
                    >
                      {cat?.displayName}
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
              overflowX: "hidden",
              pb: "2rem",
              height: "calc(100vh - 200px)", // Adjust based on your header height
              padding: 2,
              boxSizing: "border-box",
            }}
          >
            <Grid container spacing={2} sx={{ width: "100%" }}>
              {/* Render actual templates */}
              {data?.allTemplates?.map((template, index) => (
                <Grid item key={`${template.id}-${index}`} xs={12} sm={6}>
                  <TemplateCard
                    createdBy={template.created_by}
                    description={(() => {
                      const text =
                        extractTextFromPrompt(
                          template?.prompt_config_snapshot?.messages?.[0]
                            ?.content,
                        ).trim().length === 0
                          ? extractTextFromPrompt(
                              template?.prompt_config_snapshot?.messages?.[1]
                                ?.content,
                            )
                          : extractTextFromPrompt(
                              template?.prompt_config_snapshot?.messages?.[0]
                                ?.content,
                            );
                      return text
                        .replace(/[\r\n]+/g, " ")
                        .replace(/\s+/g, " ")
                        .trim().length > 200
                        ? text.slice(0, 200) + "..."
                        : text;
                    })()}
                    name={template.name}
                    onClick={() =>
                      setSelectedPromptTemplate({
                        id: template.id,
                        name: template?.name,
                        promptConfig: template,
                        desc: "",
                      })
                    }
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
              {!isLoading &&
                data?.allTemplates?.length === 0 &&
                !debouncedSearchQuery && (
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
                        height: "600px",
                      }}
                    >
                      <EmptyLayout
                        title={
                          importMode
                            ? "No templates available"
                            : "Create new template"
                        }
                        description={
                          importMode
                            ? "You don't have any templates yet. Create one from the prompts page to import it here."
                            : "Craft a prompt, customize it to your needs, and save it as a reusable template."
                        }
                        icon={"/assets/icons/ic_scratch.svg"}
                        linkText={"Check docs"}
                        link="https://docs.futureagi.com/docs/prompt"
                        sx={{
                          height: "80%",
                        }}
                        action={
                          importMode ? null : (
                            <Button
                              onClick={() => setNewPromptModal(true)}
                              variant="contained"
                              color="primary"
                            >
                              Create prompt
                            </Button>
                          )
                        }
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

          <SelectedPromptTemplateDrawer
            open={Boolean(selectedPromptTemplate?.id)}
            onClose={() =>
              setSelectedPromptTemplate({
                id: "",
                name: "",
                desc: "",
                promptConfig: null,
              })
            }
            data={selectedPromptTemplate}
            importMode={importMode}
          />
        </Stack>
      </Stack>
    </Drawer>
  );
};

ChoosePromptTemplateDrawer.propTypes = {
  onClose: PropTypes.func,
  open: PropTypes.bool,
  importMode: PropTypes.bool,
};
