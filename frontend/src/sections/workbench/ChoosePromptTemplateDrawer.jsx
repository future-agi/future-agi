import {
  Box,
  Divider,
  Drawer,
  Grid,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Typography,
  Skeleton,
  Button,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useRef, useCallback, useEffect } from "react";
import { useSnackbar } from "notistack";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { SelectedPromptTemplateDrawer } from "./SelectedPromptTemplateDrawer";
import SvgColor from "src/components/svg-color";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import _ from "lodash";
import axios, { endpoints } from "src/utils/axios";
import { useDeletePromptTemplate } from "src/api/develop/prompt";
import { useDebounce } from "../../hooks/use-debounce";
import { extractTextFromPrompt } from "../../components/ImprovePromptDrawer/common";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { usePromptStore } from "../workbench-v2/store/usePromptStore";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { PropertyName } from "../../utils/Mixpanel";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const TemplateCard = ({ name, description, createdBy, onClick, onDelete }) => {
  const [menuAnchor, setMenuAnchor] = useState(null);
  return (
    <Box
      sx={{
        position: "relative",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        minHeight: "120px",
        padding: "12px",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        cursor: "pointer",
        height: "100%",
        transition: "border-color 0.2s ease",
        "&:hover": {
          borderColor: "text.disabled",
        },
      }}
      component={"div"}
      onClick={onClick}
    >
      {onDelete && (
        <>
          <IconButton
            size="small"
            aria-label="Template actions"
            onClick={(e) => {
              e.stopPropagation();
              setMenuAnchor(e.currentTarget);
            }}
            sx={{
              position: "absolute",
              top: 6,
              right: 6,
              color: "text.secondary",
            }}
          >
            <Iconify icon="solar:menu-dots-vertical-linear" width={20} />
          </IconButton>
          <Menu
            anchorEl={menuAnchor}
            open={Boolean(menuAnchor)}
            onClose={(e) => {
              e?.stopPropagation?.();
              setMenuAnchor(null);
            }}
            onClick={(e) => e.stopPropagation()}
            anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
            transformOrigin={{ vertical: "top", horizontal: "right" }}
          >
            <MenuItem
              onClick={(e) => {
                e.stopPropagation();
                setMenuAnchor(null);
                onDelete();
              }}
              // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
              sx={{ color: "status.danger.fg", gap: 1 }}
            >
              <Iconify icon="solar:trash-bin-trash-bold" width={16} />
              Delete
            </MenuItem>
          </Menu>
        </>
      )}
      <Box
        sx={{
          border: "1px solid",
          borderColor: "border.subtle",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 28,
          width: 28,
          flexShrink: 0,
          borderRadius: "6px",
        }}
      >
        <SvgColor
          src="/assets/icons/ic_prompt_template.svg"
          sx={{
            border: "2.22px solid",
            bgcolor: "status.success.solid",
            height: 16,
            width: 16,
          }}
        />
      </Box>
      <Stack>
        <Typography
          fontSize={13}
          lineHeight="20px"
          fontWeight={"fontWeightSemiBold"}
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
          color={"text.tertiary"}
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
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        minHeight: "120px",
        padding: "12px",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        height: "100%",
      }}
    >
      <Skeleton
        variant="rectangular"
        width={28}
        height={28}
        sx={{ borderRadius: "6px" }}
      />
      <Stack gap={1}>
        <Skeleton variant="text" width="80%" height={20} />
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
            borderRadius: "4px",
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
  onDelete: PropTypes.func,
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
  const { enqueueSnackbar } = useSnackbar();
  const queryClient = useQueryClient();

  // Template to delete (drives the confirm dialog). null = closed.
  const [templateToDelete, setTemplateToDelete] = useState(null);
  // Drives the dialog's open state on its own so the payload (name) can stay
  // mounted through the fade-out and only clear on `onExited`.
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const { mutate: deleteTemplate, isPending: isDeleting } =
    useDeletePromptTemplate({
      onSuccess: () => {
        enqueueSnackbar("Template deleted", { variant: "success" });
        setDeleteDialogOpen(false);
        queryClient.invalidateQueries({ queryKey: ["prompt-templates"] });
      },
      onError: (err) => {
        enqueueSnackbar(
          err?.response?.data?.message || "Failed to delete template",
          { variant: "error" },
        );
      },
    });

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

  const covered = useDrawerCovered(open);
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
      sx={coveredDrawerSx(covered)}
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
            <Iconify color="text.primary" icon="solar:close-linear" />
          </IconButton>
        </Box>
      </Box>
      <Divider sx={{ borderColor: "divider" }} orientation="horizontal" />
      <Stack direction="row" sx={{ height: "100%", paddingLeft: "6px" }}>
        <Box
          sx={{
            width: "224px",
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
              mb: "8px",
              gap: "8px",
              alignItems: "center",
              px: 1,
              minHeight: 28,
              borderRadius: "6px",
              backgroundColor:
                category === "templates" ? "action.selected" : undefined,
              "&:hover": {
                cursor: "pointer",
              },
            }}
          >
            <SvgColor
              sx={{ height: 16, width: 16, color: "primary.main" }}
              src={
                category === "templates"
                  ? "/assets/icons/ic_open_folder.svg"
                  : "/assets/icons/ic_folder.svg"
              }
            />
            <Typography
              fontSize={13}
              lineHeight="20px"
              color={"text.primary"}
              fontWeight={category === "templates" ? 500 : 400}
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
                sx={{ display: "flex", flexDirection: "column", gap: "4px" }}
              >
                {categories?.map((cat) => (
                  <Box
                    onClick={() => handleCategoryChange(cat.name)}
                    sx={{
                      px: 1,
                      minHeight: 28,
                      display: "flex",
                      alignItems: "center",
                      bgcolor:
                        cat?.name === category ? "action.selected" : undefined,
                      borderRadius: "6px",
                      "&:hover": {
                        cursor: "pointer",
                      },
                    }}
                    key={cat?.name}
                  >
                    <Typography
                      fontSize={13}
                      lineHeight="20px"
                      fontWeight={cat?.name === category ? 500 : 400}
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
            <Grid container spacing={1.5} sx={{ width: "100%" }}>
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
                    onDelete={
                      category === "templates"
                        ? () => {
                            setTemplateToDelete({
                              id: template.id,
                              name: template?.name,
                            });
                            setDeleteDialogOpen(true);
                          }
                        : undefined
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

      <ConfirmDialog
        open={deleteDialogOpen}
        onClose={() => !isDeleting && setDeleteDialogOpen(false)}
        TransitionProps={{ onExited: () => setTemplateToDelete(null) }}
        title="Delete template"
        content={
          <Typography typography="s1" color="text.secondary">
            Are you sure you want to delete{" "}
            <Box
              component="span"
              sx={{ fontWeight: "fontWeightSemiBold", color: "text.primary" }}
            >
              {templateToDelete?.name || "this template"}
            </Box>
            ? This can&apos;t be undone.
          </Typography>
        }
        action={
          <Button
            variant="contained"
            color="error"
            disabled={isDeleting}
            onClick={() => deleteTemplate(templateToDelete?.id)}
          >
            {isDeleting ? "Deleting…" : "Delete"}
          </Button>
        }
      />
    </Drawer>
  );
};

ChoosePromptTemplateDrawer.propTypes = {
  onClose: PropTypes.func,
  open: PropTypes.bool,
  importMode: PropTypes.bool,
};
