import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography, useTheme } from "@mui/material";
import "./SingleResult.css";

import OutputMeta from "./OutputMeta";
import CopyButton from "./CopyButton";
import SvgColor from "src/components/svg-color";
import { usePromptStoreShallow } from "src/sections/workbench-v2/store/usePromptStore";
import { usePromptWorkbenchContext } from "../../WorkbenchContext";
import { useParams } from "react-router";
import { usePromptVersions } from "../../hooks/use-prompt-versions";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import CustomTooltip from "src/components/tooltip";
import LabelSelectPopover from "../../VersionHistory/LabelDropdown/LabelSelectPopover";
import MarkdownWithVariableHighlight from "src/components/ReactMarkdownWithHighlight";
import JsonOutputRenderer from "src/sections/prompt/NewPrompt/PromptGenerate/Renderers/JsonOutputRenderer";
import GridIcon from "src/components/gridIcon/GridIcon";
import { useSingleImageViewContext } from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageContext";
import {
  shouldRenderAsJson,
  shouldRenderAsImage,
  isValidUrl,
} from "src/utils/utils";
import { PROMPT_RESULT_TYPE_OPTIONS, PROMPT_RESULT_TYPES } from "../common";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { parseThinkingContent } from "./thinkingUtils";
import ThinkingBlock from "./ThinkingBlock";
import TestAudioPlayer from "src/components/custom-audio/TestAudioPlayer";
import { ShowComponent } from "src/components/show";

const SingleResult = ({
  result,
  selectedVersion: mainSelectedVersion,
  responseFormat,
  getWaveSurferInstance,
  storeWaveSurferInstance,
  updateWaveSurferInstance,
}) => {
  const outputFormat = result?.outputFormat;
  const [outputType, setOutputType] = useState(PROMPT_RESULT_TYPES.RAW);
  const scrollableDiv = useRef();
  const { role } = useAuthContext();
  const canWrite = RolePermission.PROMPTS[PERMISSIONS.UPDATE][role];
  const theme = useTheme();
  const { setImageUrl } = useSingleImageViewContext();
  const { id } = useParams();
  const { setOpenSaveTemplate } = usePromptStoreShallow((state) => ({
    setOpenSaveTemplate: state.setOpenSaveTemplate,
  }));
  const { promptName, selectedVersions } = usePromptWorkbenchContext();
  const baseVersion = mainSelectedVersion ?? selectedVersions[0];
  const { versions, refetch } = usePromptVersions(id);
  const [openTagsModal, setOpenTagsModal] = useState(false);

  const {
    thinking,
    content: displayText,
    isThinking,
  } = useMemo(() => parseThinkingContent(result?.text), [result?.text]);

  const hasThinking = thinking !== null;

  // Determine if we should render as image
  const isImageOutput = useMemo(
    () =>
      shouldRenderAsImage(
        outputFormat,
        hasThinking ? displayText : result?.text,
      ),
    [outputFormat, hasThinking, displayText, result?.text],
  );

  const isAudioOutput = useMemo(() => outputFormat === "audio", [outputFormat]);

  // Determine if we should render as JSON (only if not image)
  const isJsonOutput = useMemo(
    () =>
      !isImageOutput &&
      shouldRenderAsJson(
        responseFormat,
        hasThinking ? displayText : result?.text,
      ),
    [isImageOutput, responseFormat, hasThinking, displayText, result?.text],
  );

  const selectedVersion = useMemo(() => {
    return versions?.find(
      (ver) => ver?.template_version === baseVersion?.version,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseVersion, JSON.stringify(versions)]);

  // Auto scroll
  // useEffect(() => {
  //   // Scroll to the bottom when content changes
  //   if (scrollableDiv.current) {
  //     scrollableDiv.current.scrollTop = scrollableDiv.current.scrollHeight;
  //   }
  // }, [result?.text]);

  const renderContent = () => {
    if (isAudioOutput) {
      return (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "border.default",
            borderRadius: theme.spacing(0.5),
            px: 1,
            py: 0.75,
            my: 2,
            backgroundColor: "background.paper",
          }}
        >
          <TestAudioPlayer
            audioData={{
              url: result?.text,
            }}
            cacheKey={
              result?.text ? `workbench-output-audio-${result.text}` : undefined
            }
            getWaveSurferInstance={getWaveSurferInstance}
            storeWaveSurferInstance={storeWaveSurferInstance}
            updateWaveSurferInstance={updateWaveSurferInstance}
          />
        </Box>
      );
    }

    const text = hasThinking ? displayText : result?.text;

    const renderOutput = () => {
      // Image output takes priority
      if (isImageOutput) {
        if (text && !isValidUrl(text)) {
          return (
            <Typography
              component={"pre"}
              sx={{
                wordWrap: "break-word",
                whiteSpace: "pre-wrap",
                overflow: "hidden",
              }}
              typography={"m3"}
              fontWeight={"fontWeightRegular"}
            >
              {text}
            </Typography>
          );
        }

        return (
          <Box
            sx={{
              my: 2,
              overflow: "visible",
              pl: 0.5,
            }}
          >
            <GridIcon
              src={text}
              alt="Generated image"
              onClick={(e) => {
                e.stopPropagation();
                setImageUrl?.(text);
              }}
              sx={{
                cursor: "pointer",
                borderRadius: "8px",
                maxWidth: "300px",
                width: "100%",
                objectFit: "cover",
                height: "200px",
              }}
            />
          </Box>
        );
      }

      // JSON output
      if (isJsonOutput) {
        return (
          <JsonOutputRenderer
            data={text}
            columnName="output"
            showPaths={true}
            initialExpanded={true}
            showRawOnInvalid={true}
          />
        );
      }

      // Text output based on output type selection
      if (outputType === PROMPT_RESULT_TYPES.MARKDOWN) {
        return <MarkdownWithVariableHighlight content={text} />;
      }

      // Raw text output
      return (
        <Typography
          component={"pre"}
          sx={{
            overflow: "hidden",
            wordWrap: "break-word",
            whiteSpace: "pre-wrap",
          }}
          typography={"m3"}
          color="text.primary"
          fontWeight="fontWeightRegular"
        >
          {text}
        </Typography>
      );
    };

    return (
      <>
        {hasThinking && (
          <ThinkingBlock
            content={thinking}
            isThinking={isThinking}
            outputType={outputType}
          />
        )}
        {renderOutput()}
      </>
    );
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        gap: 1,
      }}
    >
      <Stack
        direction={"row"}
        justifyContent="space-between"
        alignItems="center"
      >
        <Typography
          variant="m2"
          fontWeight={"fontWeightSemiBold"}
          color="text.primary"
        >
          Output
        </Typography>
        <Stack direction={"row"} gap={1.5}>
          <ShowComponent condition={outputFormat === "string"}>
            <FormSearchSelectFieldState
              onChange={(e) => {
                if (e?.target?.value) {
                  setOutputType(e.target.value);
                }
              }}
              value={outputType}
              size="small"
              placeholder="Output Type"
              options={PROMPT_RESULT_TYPE_OPTIONS}
              inputProps={{
                size: Math.max(
                  PROMPT_RESULT_TYPE_OPTIONS.find((o) => o.value === outputType)
                    ?.label?.length ?? 0,
                  3,
                ),
              }}
              sx={{
                width: "fit-content",
                minWidth: 120,
                maxWidth: 220,
                "& .MuiOutlinedInput-root": {
                  width: "fit-content",
                  minWidth: 120,
                  maxWidth: 220,
                },
                backgroundColor: "background.paper",
                "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline":
                  {
                    borderColor: "divider",
                  },
              }}
              showClear={false}
            />
          </ShowComponent>
          <CustomTooltip show arrow size="small" title="Add Tags/Labels">
            <IconButton
              disabled={selectedVersion?.is_draft || !canWrite}
              onClick={() => {
                setOpenTagsModal(true);
              }}
              sx={{
                bgcolor: "background.paper",
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <SvgColor
                sx={{
                  height: 20,
                  width: 20,
                  color: selectedVersion?.is_draft ? "divider" : "text.primary",
                }}
                src="/assets/icons/ic_tag.svg"
              />
            </IconButton>
          </CustomTooltip>
          <CustomTooltip show arrow size="small" title="Save as template">
            <IconButton
              disabled={
                selectedVersion?.is_draft || !selectedVersion || !canWrite
              }
              onClick={() => {
                trackEvent(Events.promptSaveAsTemplateClicked, {
                  [PropertyName.id]: id,
                });
                setOpenSaveTemplate({
                  ...baseVersion,
                  promptName,
                  versionId: selectedVersion?.id,
                  createdBy: selectedVersion?.created_by,
                });
              }}
              sx={{
                bgcolor: "background.paper",
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <SvgColor
                sx={{
                  height: 20,
                  width: 20,
                  color: selectedVersion?.is_draft ? "divider" : "text.primary",
                }}
                src="/assets/icons/ic_template.svg"
              />
            </IconButton>
          </CustomTooltip>
        </Stack>
      </Stack>
      <Box
        sx={{ overflowY: "auto", position: "relative" }}
        ref={scrollableDiv}
        className="prompt-output-container"
      >
        <Box sx={{ display: "flex", justifyContent: "space-between" }}>
          <Box sx={{ flex: 1 }}>
            <OutputMeta metadata={result?.metadata} />
          </Box>
          {!isJsonOutput && !isImageOutput && !isAudioOutput && (
            <CopyButton value={result?.text || ""} />
          )}
        </Box>
        <Box className="streaming-text">{renderContent()}</Box>
      </Box>
      <LabelSelectPopover
        promptId={selectedVersion?.original_template}
        open={openTagsModal}
        handleClose={() => setOpenTagsModal(false)}
        version={selectedVersion?.template_version}
        versionId={selectedVersion?.id}
        selectedLabels={selectedVersion?.labels}
        onSuccess={refetch}
      />
    </Box>
  );
};

SingleResult.propTypes = {
  result: PropTypes.object,
  selectedVersion: PropTypes.object,
  responseFormat: PropTypes.object,
  getWaveSurferInstance: PropTypes.func,
  storeWaveSurferInstance: PropTypes.func,
  updateWaveSurferInstance: PropTypes.func,
};

export default SingleResult;
