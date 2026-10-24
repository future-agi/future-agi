import {
  Paper,
  Popper,
  ClickAwayListener,
  Button,
  Box,
  List,
  ListItem,
  ListItemButton,
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
  useGetNodeTemplates,
  useGetPromptTemplatesInfinite,
} from "src/api/agent-playground/agent-playground";
import axios, { endpoints } from "src/utils/axios";
import { NODE_TYPES } from "../utils/constants";
import { mapVersionToFormConfig } from "../utils/promptVersionUtils";
import useAddNodeOptimistic from "../AgentBuilder/hooks/useAddNodeOptimistic";
import { useDebounce } from "src/hooks/use-debounce";
import { enqueueSnackbar } from "notistack";

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
  } = useGetPromptTemplatesInfinite(debouncedSearch, { enabled: open });

  const prompts = useMemo(
    () => promptsData?.pages?.flatMap((p) => p.data?.results ?? []) ?? [],
    [promptsData],
  );

  const handleListScroll = useCallback(
    (e) => {
      const { scrollTop, scrollHeight, clientHeight } = e.target;
      if (
        scrollTop + clientHeight >= scrollHeight - 5 &&
        hasNextPage &&
        !isFetchingNextPage
      ) {
        fetchNextPage();
      }
    },
    [fetchNextPage, hasNextPage, isFetchingNextPage],
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
          sx={{ ml: 2, minWidth: 320, maxWidth: 320, overflow: "hidden" }}
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
            }}
            size="small"
            variant="text"
            color="primary"
            startIcon={<SvgColor src="/assets/icons/ic_add.svg" width={16} />}
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
              <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
                <CircularProgress size={20} />
              </Box>
            ) : prompts.length === 0 ? (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ px: 2, py: 1.5 }}
              >
                No prompts found
              </Typography>
            ) : (
              <>
                {prompts.map((prompt) => (
                  <ListItem key={prompt.id} disablePadding>
                    <ListItemButton
                      onClick={() => handlePromptClick(prompt)}
                      sx={{
                        py: 1,
                        px: 2,
                        "&:hover": {
                          bgcolor: "action.hover",
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
                ))}
                {isFetchingNextPage && (
                  <Box
                    sx={{ display: "flex", justifyContent: "center", py: 1 }}
                  >
                    <CircularProgress size={16} />
                  </Box>
                )}
              </>
            )}
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
