import { Box, Button, Stack } from "@mui/material";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import React, { useEffect, useMemo, useState } from "react";
import PromptItem from "./PromptItem";
import {
  resetPromptActionState,
  usePromptStore,
} from "../store/usePromptStore";
import PropTypes from "prop-types";
import { SelectedPromptTemplateDrawer } from "../../workbench/SelectedPromptTemplateDrawer";
import { useParams } from "react-router";
import { useDebounce } from "../../../hooks/use-debounce";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "../../../utils/axios";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { EMPTY_MESSAGE } from "../common";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

export default function FolderListView({ sortConfig }) {
  const { folder } = useParams();
  const [page, setPage] = useState(1);
  const [pageLimit, setPageLimit] = useState(10);
  const { role } = useAuthContext();
  const { searchQuery, setNewPromptModal } = usePromptStore();
  const debouncedSearchQuery = useDebounce(searchQuery, 300);

  //search is always in prompt folders
  const { data, isLoading } = useQuery({
    queryKey: [
      "folder-items",
      folder,
      page,
      pageLimit,
      debouncedSearchQuery,
      sortConfig,
    ],
    queryFn: () => {
      const params = {
        page,
        page_size: pageLimit,
        name: debouncedSearchQuery,
      };

      if (folder === "all") {
        params.send_all = true;
        params.sort_order = sortConfig?.direction;
        params.sort_by = sortConfig?.field;
      } else {
        params.prompt_folder = folder;
        if (sortConfig?.field) {
          params.ordering = `${sortConfig?.direction === "desc" ? "-" : ""}${
            sortConfig.field
          }`;
        }
      }

      return axios.get(endpoints.develop.runPrompt.promptExecutions(), {
        params,
      });
    },
    select: (d) => ({
      prompts: d.data?.results,
      totalCount: d.data?.count,
      totalPages: d.data?.total_pages,
    }),
    enabled: folder !== "my-templates",
  });

  const { data: promptTemplates, isLoading: loadingTemplates } = useQuery({
    queryKey: [
      "prompt-templates",
      debouncedSearchQuery,
      page,
      pageLimit,
      sortConfig,
    ],
    queryFn: async () => {
      return axios.get(endpoints.develop.runPrompt.promptTemplate, {
        params: {
          name: debouncedSearchQuery,
          page_number: page - 1,
          page_size: pageLimit,
          sort_order: sortConfig?.direction,
          sort_by: sortConfig?.field,
        },
      });
    },
    select: (d) => ({
      result: d?.data?.result?.data?.map((item) => ({
        type: "TEMPLATE",
        ...item,
      })),
      totalCount: d?.data?.result?.total_count,
      totalPages: d?.data?.result?.total_pages,
    }),
    enabled: folder === "my-templates",
  });

  const combinedData = useMemo(() => {
    if (folder === "my-templates") {
      return {
        data: promptTemplates?.result ?? [],
        totalCount: promptTemplates?.totalCount ?? 0,
        totalPages: promptTemplates?.totalPages ?? 1,
        isLoading: loadingTemplates,
      };
    }

    return {
      data: data?.prompts ?? [],
      totalCount: data?.totalCount ?? 0,
      totalPages: data?.totalPages ?? 1,
      isLoading,
    };
  }, [folder, data, isLoading, promptTemplates, loadingTemplates]);

  // The count renders beside the list heading (ActionBar), not on its own row.
  const setTotalCount = usePromptStore((s) => s.setTotalCount);
  const listTotalCount = combinedData.totalCount;
  const listIsEmpty = !combinedData.isLoading && !combinedData.data?.length;
  useEffect(() => {
    setTotalCount(
      combinedData.isLoading || listIsEmpty ? null : listTotalCount,
    );
  }, [setTotalCount, combinedData.isLoading, listIsEmpty, listTotalCount]);

  const [selectedPromptTemplate, setSelectedPromptTemplate] = useState({
    id: "",
    name: "",
    desc: "",
    promptConfig: null,
  });

  const isEmpty =
    !combinedData.isLoading &&
    !debouncedSearchQuery &&
    page === 1 &&
    (!combinedData?.data || combinedData?.data?.length === 0);

  useEffect(() => {
    return () => {
      resetPromptActionState();
    };
  }, []);

  if (isEmpty) {
    return (
      <EmptyLayout
        {...(folder === "my-templates"
          ? EMPTY_MESSAGE.template
          : EMPTY_MESSAGE.prompt)}
        linkText={"Check docs"}
        link="https://docs.futureagi.com/docs/prompt"
        sx={{
          height: "80%",
        }}
        action={
          <Button
            onClick={() => setNewPromptModal(true)}
            variant="contained"
            color="primary"
            disabled={!RolePermission.PROMPTS[PERMISSIONS.CREATE][role]}
          >
            Create prompt
          </Button>
        }
      />
    );
  }

  return (
    <>
      <Stack
        direction="column"
        sx={{
          height: debouncedSearchQuery ? "92vh" : "80vh",
        }}
      >
        {/* Scrollable content */}
        <Stack
          direction="column"
          sx={{
            flex: 1,
            overflowY: "auto",
            paddingBottom: "120px",
          }}
        >
          {combinedData?.isLoading
            ? Array.from({ length: 4 }).map((_, i) => (
                <PromptItem
                  key={i}
                  isLoading={true}
                  sx={{
                    borderBottom: "1px solid",
                    borderColor: "border.subtle",
                  }}
                />
              ))
            : combinedData?.data?.map((prompt) => (
                <PromptItem
                  key={`${prompt?.id}${prompt?.name}`}
                  createdBy={prompt.created_by}
                  lastModified={prompt.updated_at}
                  lastModifiedBy={prompt.lastModifiedBy}
                  name={prompt?.name}
                  type={prompt?.type}
                  extraData={prompt}
                  id={prompt.id}
                  sx={{
                    borderBottom: "1px solid",
                    borderColor: "border.subtle",
                  }}
                  setSelectedPromptTemplate={setSelectedPromptTemplate}
                  isSearching={!!debouncedSearchQuery}
                />
              ))}
        </Stack>

        {/* Fixed Pagination Section */}
        <Box
          sx={{
            width: "100%",
            backgroundColor: "background.paper",
            position: "absolute",
            bottom: 0,
          }}
        >
          <DataTablePagination
            page={page - 1}
            pageSize={pageLimit}
            pageCount={combinedData.totalPages}
            pageSizeOptions={[10, 20, 30, 40, 50]}
            onPageChange={(p) => setPage(p + 1)}
            onPageSizeChange={(size) => {
              setPage(1);
              setPageLimit(size);
            }}
          />
        </Box>
      </Stack>
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
      />
    </>
  );
}

FolderListView.propTypes = {
  isLoading: PropTypes.bool,
  list: PropTypes.array,
  totalPages: PropTypes.number,
  sortConfig: PropTypes.shape({
    field: PropTypes.string,
    direction: PropTypes.oneOf(["desc", "asc"]),
  }),
};
