import { useState, useMemo } from "react";
import { Box, Typography } from "@mui/material";
import { LoadingScreen } from "src/components/loading-screen";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router-dom";
import AttributeGroupList from "./AttributeGroupList";
import AttributeKeyList from "./AttributeKeyList";
import AttributeDetail from "./AttributeDetail";

const AttributesView = () => {
  const { id: projectId } = useParams();
  const [selectedGroup, setSelectedGroup] = useState(null);
  const [selectedKey, setSelectedKey] = useState(null);

  const { data: attributeKeys = [], isLoading } = useQuery({
    queryKey: ["span-attribute-keys", projectId],
    queryFn: () =>
      axios.get(endpoints.project.spanAttributeKeys(), {
        params: { project_id: projectId },
      }),
    select: (data) => data.data?.result || [],
    enabled: Boolean(projectId),
  });

  // Group attributes by dot-delimited prefix
  const groups = useMemo(() => {
    const grouped = {};
    attributeKeys.forEach(({ key, type, count }) => {
      const parts = key.split(".");
      const prefix = parts.length > 1 ? parts.slice(0, -1).join(".") : key;
      if (!grouped[prefix]) grouped[prefix] = { keys: [], totalCount: 0 };
      grouped[prefix].keys.push({ key, type, count });
      grouped[prefix].totalCount += count;
    });
    return Object.entries(grouped)
      .map(([prefix, data]) => ({ prefix, ...data }))
      .sort((a, b) => b.totalCount - a.totalCount);
  }, [attributeKeys]);

  const filteredKeys = useMemo(() => {
    if (!selectedGroup) return attributeKeys;
    return groups.find((g) => g.prefix === selectedGroup)?.keys || [];
  }, [selectedGroup, groups, attributeKeys]);

  if (isLoading) {
    return (
      <LoadingScreen sx={{ height: "calc(100vh - 180px)" }} />
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
        height: "calc(100vh - 180px)",
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
      />
      <AttributeDetail projectId={projectId} attributeKey={selectedKey} />
    </Box>
  );
};

export default AttributesView;
