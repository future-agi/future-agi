import React, { useEffect, useState } from "react";
import PromptModalWrapper from "./PromptModalWrapper";
import { Box, DialogContent, Typography } from "@mui/material";
import { useForm } from "react-hook-form";
import PropTypes from "prop-types";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { usePromptExecutions, usePromptVersions } from "src/api/develop/prompt";
import _ from "lodash";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import ImageEmbed from "src/components/PromptCards/EmbedComponents/ImageEmbed";
import PdfEmbed from "src/components/PromptCards/EmbedComponents/PdfEmbed";
import AudioEmbed from "src/components/PromptCards/EmbedComponents/AudioEmbed";
import { AudioPlaybackProvider } from "../../../../components/custom-audio/context-provider/AudioPlaybackContext";
import { normalizeForComparison } from "src/sections/workbench/createPrompt/Playground/common";

const schema = z.object({
  prompt: z.string().min(1, "Prompt is required"),
  promptVersion: z.string(),
});

const findMatchingVariable = (targetVariable, variables) => {
  const normalizedTarget = normalizeForComparison(targetVariable);
  return variables.find(
    (key) => normalizeForComparison(key) === normalizedTarget,
  );
};

const getPartsWithHighlighting = (text, validVariables) => {
  const regex = /({{.*?}})/g;
  const parts = text?.split(regex);

  return parts?.map((part, index) => {
    const match = part.match(/{{(.*?)}}/);
    if (match) {
      const variable = match[1].trim();
      const isValid = findMatchingVariable(variable, validVariables);

      return (
        <Typography
          key={index}
          typography="s1"
          fontWeight={"fontWeightRegular"}
          color={"text.primary"}
          sx={{
            color: isValid ? "green.600" : "red.600",
          }}
        >
          {part}
        </Typography>
      );
    }

    return <span key={index}>{part}</span>;
  });
};

export const ReadOnlyPrompt = ({ title, prompt, allColumns = [] }) => {
  const validVariables = allColumns.map((col) => col?.headerName);

  function renderPromptContent(prompt) {
    if (Array.isArray(prompt)) {
      const content = []; // Collect content here

      for (let i = 0; i < prompt.length; i++) {
        if (prompt?.[i]?.type === "text") {
          const parts = getPartsWithHighlighting(
            prompt?.[i]?.text,
            validVariables,
          );
          content.push(...parts);
        } else if (prompt[i]?.type === "image_url") {
          content.push(
            <Box
              sx={{ my: (theme) => theme.spacing(0.5) }}
              key={`image-box-${i}`}
            >
              <ImageEmbed
                key={`image-${i}`}
                name={prompt[i]?.image_url?.img_name}
                url={prompt[i]?.image_url?.url}
                size={prompt[i]?.image_url?.img_size}
              />
            </Box>,
          );
        } else if (prompt[i]?.type === "pdf_url") {
          content.push(
            <Box
              sx={{ my: (theme) => theme.spacing(0.5) }}
              key={`pdf-box-${i}`}
            >
              <PdfEmbed
                key={`pdf-${i}`}
                name={prompt[i]?.pdf_url?.file_name}
                url={prompt[i]?.pdf_url?.url}
                size={prompt[i]?.pdf_url?.pdf_size}
              />
            </Box>,
          );
        } else if (prompt[i]?.type === "audio_url") {
          content.push(
            <Box
              sx={{ my: (theme) => theme.spacing(0.5) }}
              key={`audio-box-${i}`}
            >
              <AudioEmbed
                key={`audio-${i}`}
                name={prompt[i]?.audio_url?.audio_name}
                url={prompt[i]?.audio_url?.url}
                size={prompt[i]?.audio_url?.audio_size}
                mimeType={prompt[i]?.audio_url?.audio_type}
              />
            </Box>,
          );
        }
      }
      return content;
    }
    return "";
  }

  return (
    <Box
      sx={{
        backgroundColor: "background.neutral",
        borderRadius: "4px",
        padding: "12px 16px",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        variant="s1"
        fontWeight={"fontWeightMedium"}
        color={"text.primary"}
      >
        {_.capitalize(title)}
      </Typography>
      <AudioPlaybackProvider>
        <Box
          sx={{
            wordWrap: "break-word",
            typography: "s1",
            color: "text.primary",
            fontWeight: "fontWeightRegular",
          }}
        >
          {renderPromptContent(prompt)}
        </Box>
      </AudioPlaybackProvider>
    </Box>
  );
};

ReadOnlyPrompt.propTypes = {
  title: PropTypes.string,
  prompt: PropTypes.string,
  allColumns: PropTypes.array,
};

function getDefaultValues(data) {
  return {
    prompt: data?.prompt?.id ?? "",
    promptVersion: data?.promptVersion?.templateName ?? "",
  };
}

export default function ImportPrompt({
  open,
  onClose,
  handleApplyImportedPrompt,
  data,
  allColumns,
}) {
  const {
    control,
    reset,
    handleSubmit,
    watch,
    formState: { isValid },
    setValue,
  } = useForm({
    defaultValues: getDefaultValues(data),
    resolver: zodResolver(schema),
    mode: "onChange",
  });

  const selectedPromptId = watch("prompt");
  const selectedVersionId = watch("promptVersion");
  const [promptMessages, setPromptMessages] = useState([]);
  const [, setHasUserChangedVersion] = useState(false);

  useEffect(() => {
    if (open && data?.prompt?.id && data?.promptVersion?.template_version) {
      setValue("prompt", data?.prompt?.id, {
        shouldValidate: true,
      });
      setValue("promptVersion", data?.promptVersion?.template_version, {
        shouldValidate: true,
      });
    }
  }, [data?.prompt?.id, data?.promptVersion?.template_version, open, setValue]);

  const {
    data: promptsData,
    fetchNextPage: fetchNextPromptListPage,
    hasNextPage: promptListHasNextPage,
    isFetchingNextPage: isFetchingPromptNextPage,
    isPending: promptsIsPending,
  } = usePromptExecutions(open);

  const onPromptScrollEnd = () => {
    if (promptListHasNextPage) {
      fetchNextPromptListPage({ cancelRefetch: false });
    }
  };

  const {
    data: versionsData,
    isPending: vesiosnIsPending,
    fetchNextPage: fetchNextVersionListPage,
    hasNextPage: versionsHasNextPage,
    isFetchingNextPage: isFetchingVersionsNextPage,
  } = usePromptVersions(selectedPromptId);

  const onVersionsScrollEnd = () => {
    if (!versionsHasNextPage) return;
    fetchNextVersionListPage({ cancelRefetch: false });
  };

  const promptsRes = promptsData?.pages.flatMap((page) => page.results) ?? [];
  const prompts =
    promptsRes?.map((prompt) => ({
      label: prompt?.name,
      value: prompt?.id,
    })) ?? [];

  const versionRes = versionsData?.pages.flatMap((page) => page.results) ?? [];

  const versions =
    versionRes?.map((version) => ({
      label: version?.template_version,
      value: version?.template_version,
    })) ?? [];

  useEffect(() => {
    if (versionRes?.length === 0) return;
    const latestVersionId = versionRes?.[0]?.template_version;
    if (selectedVersionId !== latestVersionId) {
      setValue("promptVersion", latestVersionId, {
        shouldValidate: true,
      });
    } else if (!selectedPromptId && !data?.promptVersion?.template_version) {
      setValue("promptVersion", "", {
        shouldValidate: true,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionRes?.length, selectedPromptId, data]);

  useEffect(() => {
    setPromptMessages([]);
    if (!selectedPromptId || !selectedVersionId || versionRes?.length < 0)
      return;
    const selectedVersion = versionRes.find(
      (v) => v?.template_version === selectedVersionId,
    );
    const messages = selectedVersion?.prompt_config_snapshot?.messages || [];
    setPromptMessages(messages);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVersionId, selectedPromptId, versionRes?.length]);

  const handleClose = () => {
    onClose();
    setTimeout(() => {
      reset({
        prompt: "",
        promptVersion: "",
      });
      setPromptMessages([]);
      setHasUserChangedVersion(false);
    }, 200);
  };

  const handleImportPrompt = (data) => {
    const prompt = promptsRes.find((prompt) => {
      return data?.prompt === prompt?.id;
    });
    const promptVersion = versionRes.find(
      (version) => version?.template_version === data?.promptVersion,
    );
    handleApplyImportedPrompt({
      prompt,
      promptVersion,
    });
    handleClose();
  };

  return (
    <PromptModalWrapper
      open={open}
      onClose={handleClose}
      title="Import Prompt"
      subTitle="You can edit it once applied"
      onSubmit={handleSubmit(handleImportPrompt)}
      isValid={Boolean(isValid && selectedVersionId)}
      actionBtnTitle="Apply"
    >
      <DialogContent
        sx={{
          padding: 0,
        }}
      >
        <form
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            paddingTop: "8px",
          }}
        >
          <FormSearchSelectFieldControl
            disabled={promptsIsPending}
            control={control}
            fieldName="prompt"
            label="Select prompt"
            placeholder="Select a prompt"
            fullWidth
            size="small"
            options={prompts || []}
            sx={{
              "& .MuiSelect-icon": {
                color: "text.primary",
              },
            }}
            onScrollEnd={onPromptScrollEnd}
            isFetchingNextPage={isFetchingPromptNextPage}
            onChange={() => {
              setValue("promptVersion", "", {
                shouldValidate: true,
              });
            }}
          />
          <FormSearchSelectFieldControl
            control={control}
            disabled={
              !selectedPromptId ||
              (selectedPromptId && !vesiosnIsPending && versions?.length === 0)
            }
            fieldName="promptVersion"
            label="Versions"
            placeholder="Select version"
            fullWidth
            size="small"
            options={versions || []}
            sx={{
              "& .MuiSelect-icon": {
                color: "text.primary",
              },
            }}
            helperText={
              selectedPromptId && !vesiosnIsPending && versions?.length === 0
                ? "No versions found for the selected prompt"
                : ""
            }
            onScrollEnd={onVersionsScrollEnd}
            isFetchingNextPage={isFetchingVersionsNextPage}
          />
          {promptMessages &&
            promptMessages?.length > 0 &&
            promptMessages?.map((message, index) => {
              let title = message?.role;
              if (message?.role === "system") {
                title = message.role + " (Optional)";
              }

              return (
                <ReadOnlyPrompt
                  title={title}
                  prompt={message?.content}
                  key={index}
                  allColumns={allColumns}
                />
              );
            })}
        </form>
      </DialogContent>
    </PromptModalWrapper>
  );
}

ImportPrompt.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  handleApplyImportedPrompt: PropTypes.func,
  data: PropTypes.object,
  allColumns: PropTypes.array,
};
