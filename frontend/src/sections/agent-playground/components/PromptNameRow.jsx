import React, { useCallback, useEffect, useMemo, useRef } from "react";
import PropTypes from "prop-types";
import { Stack } from "@mui/material";
import { useWatch, useFormContext } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { EnhancedFormSelectField } from "src/components/FormSelectField/FormSelectField";
import { DraftBadge } from "src/sections/workbench/createPrompt/SharedStyledComponents";
import {
  useGetPromptVersionsInfinite,
  useGetPromptVersionDetail,
} from "src/api/agent-playground/agent-playground";
import { mapVersionToFormConfig } from "../utils/promptVersionUtils";

export default function PromptNameRow({ control }) {
  const { setValue } = useFormContext();

  const handleNameTransformation = (event) => {
    const transformedValue = event.target.value
      .toLowerCase()
      .replace(/[^a-z0-9_]/g, "_")
      .replace(/^_+/, "");
    setValue("name", transformedValue);
  };
  const promptTemplateId = useWatch({ control, name: "prompt_template_id" });
  const selectedVersion = useWatch({ control, name: "prompt_version_id" });
  const prevVersionRef = useRef(selectedVersion);

  const {
    data: versionsData,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useGetPromptVersionsInfinite(promptTemplateId);

  // Flatten all loaded pages into a single versions array
  const versions = useMemo(
    () => (versionsData?.pages || []).flatMap((p) => p.data?.results ?? []),
    [versionsData],
  );

  // Check if the selected version is in the loaded pages
  const selectedInList = useMemo(
    () => versions.some((v) => v.id === selectedVersion),
    [versions, selectedVersion],
  );

  // Fetch selected version individually when it's not in the loaded pages
  const { data: selectedVersionDetail } = useGetPromptVersionDetail(
    selectedVersion,
    { enabled: !!selectedVersion && !selectedInList },
  );

  // Build options — prepend selected version from detail fetch if not in list
  const versionOptions = useMemo(() => {
    const seen = new Set();
    const options = [];

    // Prepend selected version from detail fetch (if not in paginated list)
    if (selectedVersionDetail && !selectedInList) {
      seen.add(selectedVersionDetail.id);
      options.push({
        value: selectedVersionDetail.id,
        label: selectedVersionDetail.template_version?.toUpperCase(),
        isDraft: selectedVersionDetail.is_draft,
      });
    }

    // Add paginated versions (deduplicates if selected version's page loads)
    for (const v of versions) {
      if (!seen.has(v.id)) {
        seen.add(v.id);
        options.push({
          value: v.id,
          label: v.template_version?.toUpperCase(),
          isDraft: v.is_draft,
        });
      }
    }

    return options;
  }, [versions, selectedInList, selectedVersionDetail]);

  // When user changes version from the dropdown, repopulate form fields
  useEffect(() => {
    if (
      !selectedVersion ||
      selectedVersion === prevVersionRef.current ||
      versions.length === 0
    ) {
      prevVersionRef.current = selectedVersion;
      return;
    }
    prevVersionRef.current = selectedVersion;

    // Look in both paginated list and detail fetch
    const version =
      versions.find((v) => v.id === selectedVersion) ||
      (selectedVersionDetail?.id === selectedVersion
        ? selectedVersionDetail
        : null);
    if (!version) return;

    const formConfig = mapVersionToFormConfig(version);
    setValue("modelConfig", formConfig.modelConfig, { shouldDirty: true });
    // Explicitly set responseFormat so useController picks up the nested change
    setValue(
      "modelConfig.responseFormat",
      formConfig.modelConfig.responseFormat,
      {
        shouldDirty: true,
      },
    );
    setValue("messages", formConfig.messages, { shouldDirty: true });
    setValue("outputFormat", formConfig.outputFormat || "string", {
      shouldDirty: false,
    });
    if (formConfig.payload) {
      setValue("payload", formConfig.payload, { shouldDirty: true });
    }
  }, [selectedVersion, versions, selectedVersionDetail, setValue]);

  const handleScrollEnd = useCallback(() => {
    if (hasNextPage && !isFetchingNextPage) fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const isDraft = useMemo(
    () =>
      versionOptions.find((v) => v.value === selectedVersion)?.isDraft ?? false,
    [selectedVersion, versionOptions],
  );

  return (
    <Stack direction="row" gap={1} alignItems="center">
      <FormTextFieldV2
        fullWidth
        size="small"
        control={control}
        fieldName="name"
        label="Prompt Name"
        required
        rules={{ required: "Prompt name is required" }}
        onChange={handleNameTransformation}
        sx={{ "& .MuiInputBase-root": { height: "32px" } }}
      />
      {versionOptions.length > 0 && (
        <EnhancedFormSelectField
          control={control}
          fieldName="prompt_version_id"
          size="small"
          options={versionOptions}
          onScrollEnd={handleScrollEnd}
          loadingMoreOptions={isFetchingNextPage}
          sx={{
            minWidth: 80,
            maxWidth: 80,
            "& .MuiInputBase-root": { height: "32px" },
          }}
        />
      )}
      {isDraft && <DraftBadge>Draft</DraftBadge>}
    </Stack>
  );
}

PromptNameRow.propTypes = {
  control: PropTypes.any.isRequired,
};
