import { useState, useMemo } from "react";
import { Alert, Box, Typography, CircularProgress } from "@mui/material";
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
  const [selectedType, setSelectedType] = useState(null);

  const { data: attributeDiscovery, isLoading } = useQuery({
    queryKey: ["span-attribute-keys", projectId],
    queryFn: () =>
      axios.get(endpoints.project.spanAttributeKeys(), {
        params: { project_id: projectId },
      }),
    select: (data) => ({
      keys: data.data?.result || [],
      queryComplete: data.data?.query_complete !== false,
    }),
    enabled: Boolean(projectId),
  });
  const attributeKeys = useMemo(
    () => attributeDiscovery?.keys || [],
    [attributeDiscovery],
  );
  const attributeKeysIncomplete = attributeDiscovery?.queryComplete === false;

  // Group attributes by dot-delimited prefix
  const groups = useMemo(() => {
    const grouped = {};
    attributeKeys.forEach(({ key, type, count }) => {
      const parts = key.split(".");
      const prefix = parts.length > 1 ? parts.slice(0, -1).join(".") : key;
      if (!grouped[prefix]) {
        grouped[prefix] = {
          keys: [],
          knownTotalCount: 0,
          hasUnknownCount: false,
        };
      }
      grouped[prefix].keys.push({ key, type, count });
      if (typeof count === "number") {
        grouped[prefix].knownTotalCount += count;
      } else {
        grouped[prefix].hasUnknownCount = true;
      }
    });
    return Object.entries(grouped)
      .map(([prefix, data]) => ({ prefix, ...data }))
      .sort(
        (a, b) =>
          Number(b.hasUnknownCount) - Number(a.hasUnknownCount) ||
          b.knownTotalCount - a.knownTotalCount,
      );
  }, [attributeKeys]);

  const filteredKeys = useMemo(() => {
    if (!selectedGroup) return attributeKeys;
    return groups.find((g) => g.prefix === selectedGroup)?.keys || [];
  }, [selectedGroup, groups, attributeKeys]);

  const handleSelectKey = (key, type) => {
    setSelectedKey(key);
    setSelectedType(type || null);
  };

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "calc(100vh - 180px)",
        }}
      >
        <CircularProgress />
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
      {attributeKeysIncomplete && (
        <Alert severity="warning" sx={{ m: 1.5, mb: 0 }}>
          Attribute discovery is incomplete. Type an attribute key to continue.
        </Alert>
      )}
      <Box sx={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden" }}>
        <AttributeGroupList
          groups={groups}
          selectedGroup={selectedGroup}
          onSelectGroup={setSelectedGroup}
        />
        <AttributeKeyList
          keys={filteredKeys}
          selectedKey={selectedKey}
          onSelectKey={handleSelectKey}
          allowManualEntry
        />
        <AttributeDetail
          projectId={projectId}
          attributeKey={selectedKey}
          attributeType={selectedType}
        />
      </Box>
    </Box>
  );
};

export default AttributesView;
