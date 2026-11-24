import PropTypes from "prop-types";
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { fetchAllObserveProjects } from "src/api/project/observe-project-list";
import TagPicker from "src/components/tag-picker";

const fetchProjectTags = async (projectId) => {
  const { data } = await axios.get(
    apiPath("/tracer/project/{id}/", { id: projectId }),
  );
  const result = data?.result || data;
  return result?.tags || [];
};

const updateProjectTags = async (projectId, tags) => {
  const { data } = await axios.patch(
    apiPath("/tracer/project/{id}/tags/", { id: projectId }),
    { tags },
  );
  return data?.result?.tags || tags;
};

const fetchAllKnownTags = async ({ signal } = {}) => {
  const projects = await fetchAllObserveProjects({ signal });
  const tagSet = new Set();
  projects.forEach((p) => (p.tags || []).forEach((t) => tagSet.add(t)));
  return Array.from(tagSet).sort();
};

const TagEditor = ({ projectId }) => {
  const [opened, setOpened] = useState(false);
  const queryClient = useQueryClient();

  const { data: tags = [] } = useQuery({
    queryKey: ["project-tags", projectId],
    queryFn: () => fetchProjectTags(projectId),
    enabled: !!projectId,
    staleTime: 30_000,
  });

  const {
    data: allKnownTags = [],
    isError: isKnownTagsError,
    isFetching: isKnownTagsFetching,
    refetch: refetchKnownTags,
  } = useQuery({
    queryKey: ["all-known-tags"],
    queryFn: ({ signal }) => fetchAllKnownTags({ signal }),
    enabled: opened,
    staleTime: 60_000,
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: (newTags) => updateProjectTags(projectId, newTags),
    onMutate: async (newTags) => {
      await queryClient.cancelQueries({
        queryKey: ["project-tags", projectId],
      });
      const prev = queryClient.getQueryData(["project-tags", projectId]);
      queryClient.setQueryData(["project-tags", projectId], newTags);
      return { prev };
    },
    onError: (_err, _newTags, context) => {
      queryClient.setQueryData(["project-tags", projectId], context?.prev);
      enqueueSnackbar("Failed to update tags", { variant: "error" });
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["project-tags", projectId] });
      queryClient.invalidateQueries({ queryKey: ["observe-projects"] });
      queryClient.invalidateQueries({ queryKey: ["all-known-tags"] });
    },
  });

  return (
    <TagPicker
      tags={tags}
      knownTags={allKnownTags}
      onChange={(next) => mutation.mutate(next)}
      onOpen={() => setOpened(true)}
      knownError={isKnownTagsError}
      knownFetching={isKnownTagsFetching}
      onRetryKnown={() => refetchKnownTags()}
    />
  );
};

TagEditor.propTypes = {
  projectId: PropTypes.string.isRequired,
};

export default TagEditor;
