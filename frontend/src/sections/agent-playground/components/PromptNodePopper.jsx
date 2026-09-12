import {
  Paper,
  Popper,
  ClickAwayListener,
  Button,
  Box,
  List,
  ListItem,
  ListItemButton,
  ListSubheader,
  ListItemText,
  Stack,
  CircularProgress,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import SvgColor from "src/components/svg-color";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import {
  useGetLibraryTemplatesInfinite,
  useGetNodeTemplates,
  useGetPromptTemplatesInfinite,
} from "src/api/agent-playground/agent-playground";
import axios, { endpoints } from "src/utils/axios";
import { NODE_TYPES } from "../utils/constants";
import { mapVersionToFormConfig } from "../utils/promptVersionUtils";
import { getLibraryTemplateItems } from "src/api/agent-playground/libraryTemplateResponseUtils";
import {
  INCOMPATIBLE_LIBRARY_TEMPLATE_MESSAGE,
  toDetachedLibraryTemplateConfig,
} from "../utils/libraryTemplateUtils";
import useAddNodeOptimistic from "../AgentBuilder/hooks/useAddNodeOptimistic";
import { useDebounce } from "src/hooks/use-debounce";
import { enqueueSnackbar } from "notistack";

function LoadingListItem({ size = 20 }) {
  return (
    <ListItem sx={{ display: "flex", justifyContent: "center", py: 2 }}>
      <CircularProgress size={size} />
    </ListItem>
  );
}

LoadingListItem.propTypes = {
  size: PropTypes.number,
};

export default function PromptNodePopper({
  open,
  anchorEl,
  onClose,
  onNodeSelect,
}) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 300);
  const { addNode } = useAddNodeOptimistic();

  // Get the node_template_id for llm_prompt from templates
  const { data: templateNodes = [] } = useGetNodeTemplates();
  const llmPromptTemplateId = useMemo(() => {
    const t = templateNodes.find((n) => n.id === NODE_TYPES.LLM_PROMPT);
    return t?.node_template_id || undefined;
  }, [templateNodes]);

  // Fetch prompts with infinite scroll
  const {
    data: promptsData,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isError: isPromptsError,
  } = useGetPromptTemplatesInfinite(debouncedSearch, { enabled: open });

  const {
    data: libraryTemplatesData,
    isLoading: isLibraryLoading,
    fetchNextPage: fetchNextLibraryPage,
    hasNextPage: hasNextLibraryPage,
    isFetchingNextPage: isFetchingNextLibraryPage,
    isError: isLibraryError,
  } = useGetLibraryTemplatesInfinite(debouncedSearch, { enabled: open });

  const prompts = useMemo(
    () => promptsData?.pages?.flatMap((p) => p.data?.results ?? []) ?? [],
    [promptsData],
  );

  const libraryTemplates = useMemo(
    () =>
      libraryTemplatesData?.pages?.flatMap((p) =>
        getLibraryTemplateItems(p.data),
      ) ?? [],
    [libraryTemplatesData],
  );

  const handleListScroll = useCallback(
    (e) => {
      const { scrollTop, scrollHeight, clientHeight } = e.target;
      const isAtBottom = scrollTop + clientHeight >= scrollHeight - 5;

      if (isAtBottom && hasNextPage && !isFetchingNextPage) {
        fetchNextPage();
      }

      if (isAtBottom && hasNextLibraryPage && !isFetchingNextLibraryPage) {
        fetchNextLibraryPage();
      }
    },
    [
      fetchNextLibraryPage,
      fetchNextPage,
      hasNextLibraryPage,
      hasNextPage,
      isFetchingNextLibraryPage,
      isFetchingNextPage,
    ],
  );

  const handleAddBlankPrompt = useCallback(() => {
    if (onNodeSelect) {
      onNodeSelect(NODE_TYPES.LLM_PROMPT, llmPromptTemplateId);
    } else {
      addNode({
        type: NODE_TYPES.LLM_PROMPT,
        position: undefined,
        node_template_id: llmPromptTemplateId,
      });
    }
    onClose();
  }, [addNode, onClose, onNodeSelect, llmPromptTemplateId]);

  const handlePromptClick = useCallback(
    async (prompt) => {
      // Fetch versions and pick the latest/default one
      let version = null;
      try {
        const res = await axios.get(
          endpoints.develop.runPrompt.getPromptVersions(),
          {
            params: { template_id: prompt.id, modality: "chat" },
          },
        );
        const versions = res.data?.results ?? [];
        version = versions.find((v) => v.is_default) || versions[0] || null;
      } catch {
        enqueueSnackbar("Failed to fetch prompt versions", {
          variant: "error",
        });
        return;
      }

      // Build form-compatible config from version's promptConfigSnapshot
      const config = {
        prompt_template_id: prompt.id,
        prompt_version_id: version?.id ?? null,
        ...mapVersionToFormConfig(version),
      };
      if (onNodeSelect) {
        onNodeSelect(NODE_TYPES.LLM_PROMPT, llmPromptTemplateId, {
          ...config,
          name: prompt.name,
        });
      } else {
        addNode({
          type: NODE_TYPES.LLM_PROMPT,
          position: undefined,
          node_template_id: llmPromptTemplateId,
          name: prompt.name,
          config,
        });
      }
      onClose();
    },
    [addNode, onClose, onNodeSelect, llmPromptTemplateId],
  );

  const handleLibraryTemplateClick = useCallback(
    (template) => {
      const config = toDetachedLibraryTemplateConfig(template);
      if (!config) {
        enqueueSnackbar(INCOMPATIBLE_LIBRARY_TEMPLATE_MESSAGE, {
          variant: "error",
        });
        return;
      }

      if (onNodeSelect) {
        onNodeSelect(NODE_TYPES.LLM_PROMPT, llmPromptTemplateId, {
          ...config,
          name: template.name,
        });
      } else {
        addNode({
          type: NODE_TYPES.LLM_PROMPT,
          position: undefined,
          node_template_id: llmPromptTemplateId,
          name: template.name,
          config,
        });
      }
      onClose();
    },
    [addNode, onClose, onNodeSelect, llmPromptTemplateId],
  );

  const hasPrompts = prompts.length > 0;
  const hasLibraryTemplates = libraryTemplates.length > 0;
  const hasQueryError = isPromptsError || isLibraryError;
  const queryErrorMessage = isPromptsError
    ? isLibraryError
      ? "Unable to load prompt templates. Check your connection and try again."
      : "Unable to load your prompts. Library templates may still be available."
    : "Unable to load library templates. Your prompts may still be available.";
  const shouldShowEmptyState =
    !isLoading &&
    !isLibraryLoading &&
    !hasPrompts &&
    !hasLibraryTemplates &&
    !hasQueryError;
  const isFetchingMore = isFetchingNextPage || isFetchingNextLibraryPage;

  const renderPromptItems = (items, keyPrefix, onClick) =>
    items.map((prompt) => (
      <ListItem key={`${keyPrefix}-${prompt.id}`} disablePadding>
        <ListItemButton
          onClick={() => onClick(prompt)}
          sx={{
            py: 1,
            px: 2,
            "&:hover": {
              bgcolor: (theme) =>
                theme.palette.mode === "dark"
                  ? "action.hover"
                  : "whiteScale.200",
            },
          }}
        >
          <Stack spacing={0.5} sx={{ width: "100%" }}>
            <ListItemText
              primary={prompt.name}
              primaryTypographyProps={{
                typography: "s1",
                color: "text.primary",
                noWrap: true,
              }}
            />
          </Stack>
        </ListItemButton>
      </ListItem>
    ));

  return (
    <Popper
      open={open}
      anchorEl={anchorEl}
      placement="right-start"
      modifiers={[
        {
          name: "offset",
          options: {
            offset: [40, 0],
          },
        },
      ]}
      sx={{ zIndex: 1301 }}
      data-prompt-popper
    >
      <ClickAwayListener onClickAway={onClose}>
        <Paper
          elevation={3}
          data-prompt-popper
          sx={{
            ml: 2,
            minWidth: 320,
            maxWidth: 320,
            backgroundColor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
          }}
        >
          <Box sx={{ p: 1.5, pb: 0.5 }}>
            <FormSearchField
              placeholder="Search prompts..."
              size="small"
              searchQuery={search}
              onChange={(e) => setSearch(e.target.value)}
              fullWidth
              autoFocus
              InputProps={{}}
              sx={{
                "& .MuiOutlinedInput-root": {
                  height: 30,
                  minHeight: 30,
                  "& fieldset": {
                    borderColor: "divider",
                  },
                  "&:hover fieldset": {
                    borderColor: "divider",
                  },
                  "&.Mui-focused fieldset": {
                    borderColor: "divider",
                  },
                },
              }}
            />
          </Box>

          <Button
            onClick={handleAddBlankPrompt}
            fullWidth
            sx={{
              justifyContent: "flex-start",
              textAlign: "left",
              px: 1.5,
              py: 1,
            }}
            size="small"
            variant="text"
            color="primary"
            startIcon={
              <SvgColor
                src="/assets/icons/ic_add.svg"
                sx={{ width: 16, height: 16 }}
              />
            }
          >
            Add Blank Prompt
          </Button>

          <List
            onScroll={handleListScroll}
            sx={{
              width: "100%",
              bgcolor: "background.paper",
              maxHeight: 200,
              overflowY: "auto",
              py: 0.25,
            }}
            dense
          >
            {isLoading ? (
              <LoadingListItem />
            ) : (
              hasPrompts && (
                <>
                  <ListSubheader
                    disableSticky
                    sx={{
                      bgcolor: "background.paper",
                      color: "text.secondary",
                      lineHeight: "28px",
                      typography: "s2",
                      fontWeight: "fontWeightMedium",
                    }}
                  >
                    My Prompts
                  </ListSubheader>
                  {renderPromptItems(
                    prompts,
                    "saved-prompt",
                    handlePromptClick,
                  )}
                </>
              )
            )}

            {isLibraryLoading ? (
              <LoadingListItem />
            ) : (
              hasLibraryTemplates && (
                <>
                  <ListSubheader
                    disableSticky
                    sx={{
                      bgcolor: "background.paper",
                      color: "text.secondary",
                      lineHeight: "28px",
                      typography: "s2",
                      fontWeight: "fontWeightMedium",
                    }}
                  >
                    Library Templates
                  </ListSubheader>
                  {renderPromptItems(
                    libraryTemplates,
                    "library-template",
                    handleLibraryTemplateClick,
                  )}
                </>
              )
            )}

            {hasQueryError && (
              <ListItem>
                <Typography variant="body2" color="error.main" sx={{ py: 0.5 }}>
                  {queryErrorMessage}
                </Typography>
              </ListItem>
            )}

            {shouldShowEmptyState && (
              <ListItem>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ py: 1 }}
                >
                  No prompts found
                </Typography>
              </ListItem>
            )}

            {isFetchingMore && <LoadingListItem size={16} />}
          </List>
        </Paper>
      </ClickAwayListener>
    </Popper>
  );
}

PromptNodePopper.propTypes = {
  open: PropTypes.bool.isRequired,
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  onNodeSelect: PropTypes.func,
};
