import { useState, useMemo } from "react";
import {
  Alert,
  Box,
  Typography,
  CircularProgress,
  Button,
} from "@mui/material";
import { useInfiniteQuery } from "@tanstack/react-query";
import { LoadingScreen } from "src/components/loading-screen";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router-dom";
import { useDebounce } from "src/hooks/use-debounce";
import AttributeGroupList from "./AttributeGroupList";
import AttributeKeyList from "./AttributeKeyList";
import AttributeDetail from "./AttributeDetail";

const AttributesView = () => {
  const { id: projectId } = useParams();
  const [selectedGroup, setSelectedGroup] = useState(null);
  const [selectedKey, setSelectedKey] = useState(null);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search.trim(), 350);

  const {
    data,
    isLoading,
    isError,
    isFetching,
    refetch,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["span-attribute-keys", projectId, debouncedSearch],
    queryFn: ({ signal, pageParam }) =>
      axios.get(endpoints.project.spanAttributeKeys(), {
        signal,
        params: {
          project_id: projectId,
          ...(debouncedSearch
            ? { q: debouncedSearch }
            : {
                page_size: 25,
                ...(pageParam ? { cursor: pageParam } : {}),
              }),
        },
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) =>
      !debouncedSearch &&
      lastPage?.data?.has_more &&
      lastPage?.data?.next_cursor
        ? lastPage.data.next_cursor
        : undefined,
    enabled: Boolean(projectId),
    retry: false,
    meta: { errorHandled: true },
  });
  const seenAttributeKeys = new Set();
  const attributeKeys = (data?.pages || []).flatMap((page) =>
    (page?.data?.result || []).filter(({ key }) => {
      if (!key || seenAttributeKeys.has(key)) return false;
      seenAttributeKeys.add(key);
      return true;
    }),
  );

  // Group attributes by dot-delimited prefix
  const groups = useMemo(() => {
    const grouped = {};
    attributeKeys.forEach(({ key, type, count, count_exact: countExact }) => {
      const parts = key.split(".");
      const prefix = parts.length > 1 ? parts.slice(0, -1).join(".") : key;
      if (!grouped[prefix]) grouped[prefix] = { keys: [], totalCount: 0 };
      grouped[prefix].keys.push({ key, type, count, count_exact: countExact });
      if (countExact && Number.isFinite(count))
        grouped[prefix].totalCount += count;
    });
    return Object.entries(grouped)
      .map(([prefix, data]) => ({ prefix, ...data }))
      .sort(
        (a, b) =>
          b.totalCount - a.totalCount || a.prefix.localeCompare(b.prefix),
      );
  }, [attributeKeys]);

  const filteredKeys = useMemo(() => {
    if (debouncedSearch || !selectedGroup) return attributeKeys;
    return groups.find((g) => g.prefix === selectedGroup)?.keys || [];
  }, [debouncedSearch, selectedGroup, groups, attributeKeys]);

  if (isLoading) {
    return <LoadingScreen sx={{ height: "calc(100vh - 180px)" }} />;
  }

  if (isError && attributeKeys.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          p: 3,
        }}
      >
        <Alert
          severity="warning"
          action={
            <Button
              size="small"
              disabled={isFetching}
              onClick={() => refetch()}
            >
              Retry
            </Button>
          }
        >
          Span attributes could not be loaded. Please retry.
        </Alert>
      </Box>
    );
  }

  if (attributeKeys.length === 0 && hasNextPage) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          flexDirection: "column",
          gap: 1,
        }}
      >
        {isFetchingNextPage ? (
          <CircularProgress size={24} />
        ) : (
          <Button variant="outlined" onClick={() => fetchNextPage()}>
            Continue loading attributes
          </Button>
        )}
        <Typography variant="body2" color="text.secondary">
          Searching older traces for attributes…
        </Typography>
      </Box>
    );
  }

  if (attributeKeys.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <Typography variant="h6" color="text.secondary">
          No Span Attributes Found
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Span attributes will appear here once trace data is ingested.
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 180px)",
        overflow: "hidden",
      }}
    >
      {isError && (
        <Alert
          severity="warning"
          action={
            <Button
              size="small"
              disabled={isFetching}
              onClick={() => refetch()}
            >
              Retry
            </Button>
          }
          sx={{ m: 1, mb: 0, flexShrink: 0 }}
        >
          Span attributes could not be refreshed. Existing attributes are still
          available.
        </Alert>
      )}
      <Box
        sx={{
          display: "flex",
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        <AttributeGroupList
          groups={groups}
          selectedGroup={selectedGroup}
          onSelectGroup={setSelectedGroup}
        />
        <AttributeKeyList
          keys={filteredKeys}
          selectedKey={selectedKey}
          onSelectKey={setSelectedKey}
          hasMore={hasNextPage}
          isLoadingMore={isFetchingNextPage}
          onLoadMore={fetchNextPage}
          search={search}
          onSearchChange={setSearch}
        />
        <AttributeDetail projectId={projectId} attributeKey={selectedKey} />
      </Box>
    </Box>
  );
};

export default AttributesView;
