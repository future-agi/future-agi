import React, { useMemo, useState } from "react";
import { Box, Button, Divider, Stack, useTheme } from "@mui/material";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useParams } from "react-router";
import { usePromptStore } from "./store/usePromptStore";
import ActionBar from "./components/ActionBar";
import FolderListView from "./components/FolderListView";
import { ROOT_ROUTES } from "./common";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "../../utils/axios";
import { useDebounce } from "../../hooks/use-debounce";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import SvgColor from "src/components/svg-color";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

export default function FolderView() {
  const { folder } = useParams();
  const isMain = !ROOT_ROUTES.includes(folder);
  const theme = useTheme();
  const { role } = useAuthContext();
  const {
    searchQuery,
    onSearchQueryChange,
    setNewPromptModal,
    setSelectTemplateDrawerOpen,
  } = usePromptStore();
  const debouncedSearchQuery = useDebounce(searchQuery, 300);

  const [sortConfig, setSortConfig] = useState({
    field: "updated_at",
    direction: "desc",
  });

  const { data: folders } = useQuery({
    queryKey: ["prompt-folders"],
    queryFn: () => axios.get(endpoints.develop.runPrompt.promptFolder),
    select: (d) => d.data?.result,
    enabled: false,
  });

  const selectedFolder = useMemo(() => {
    return folders?.find((f) => f?.id === folder);
  }, [folders, folder]);

  const placeholder = useMemo(() => {
    if (folder === "my-templates") {
      return "Search in templates";
    } else {
      return "Search in prompts";
    }
  }, [folder]);

  const handleUseTemplate = () => {
    trackEvent(Events.promptUseTemplateClicked, {
      [PropertyName.click]: true,
    });
    setSelectTemplateDrawerOpen(true);
  };

  const handleCreateTemplate = () => {
    trackEvent(Events.promptCreateClicked, {
      [PropertyName.click]: true,
    });
    setNewPromptModal(true);
  };

  return (
    <Box
      sx={{
        flex: 1,
        paddingY: theme.spacing(2),
        height: "100%",
        backgroundColor: "background.paper",
        position: "relative",
      }}
    >
      <Stack
        sx={{
          p: theme.spacing(2),
          pt: 0,
        }}
        display={"flex"}
        direction={"row"}
        alignItems={"center"}
        justifyContent={"space-between"}
        gap={2}
      >
        <FormSearchField
          placeholder={placeholder}
          sx={{ minWidth: "360px", flexGrow: 1 }}
          searchQuery={searchQuery}
          onChange={(e) => onSearchQueryChange(e.target.value)}
        />

        <Stack
          sx={{
            ml: "auto",
          }}
          direction={"row"}
          alignItems={"center"}
          gap={theme.spacing(2)}
        >
          <Button
            variant="outlined"
            size="medium"
            sx={{
              color: "text.primary",
              borderColor: "divider",
            }}
            startIcon={<SvgColor src="/assets/icons/ic_docs_single.svg" />}
            component="a"
            href="https://docs.futureagi.com/docs/prompt/"
            target="_blank"
          >
            View Docs
          </Button>
          <Button
            onClick={handleUseTemplate}
            color="primary"
            variant="outlined"
            disabled={!RolePermission.PROMPTS[PERMISSIONS.CREATE][role]}
          >
            Use template
          </Button>
          <Button
            onClick={handleCreateTemplate}
            variant="contained"
            color="primary"
            disabled={!RolePermission.PROMPTS[PERMISSIONS.CREATE][role]}
          >
            Create prompt
          </Button>
        </Stack>
      </Stack>
      {!debouncedSearchQuery && (
        <>
          <Divider
            sx={{
              borderColor: "divider",
            }}
          />
          <ActionBar
            items={
              isMain
                ? [
                    {
                      id: folder,
                      name: selectedFolder?.name,
                    },
                  ]
                : []
            }
            sortConfig={sortConfig}
            setSortConfig={setSortConfig}
          />
        </>
      )}
      <Divider
        sx={{
          borderColor: "divider",
        }}
      />

      <FolderListView key={folder} sortConfig={sortConfig} />
    </Box>
  );
}
