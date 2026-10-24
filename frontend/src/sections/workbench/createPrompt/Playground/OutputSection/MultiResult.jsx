import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography, useTheme } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import { useAgThemePrompt } from "src/hooks/use-ag-theme";
import "./MultiResult.css";
import OutputMeta from "./OutputMeta";
import "./SingleResult.css";
import CopyButton from "./CopyButton";
import { useParams } from "react-router";
import { usePromptStore } from "src/sections/workbench-v2/store/usePromptStore";
import { usePromptWorkbenchContext } from "../../WorkbenchContext";
import { usePromptVersions } from "../../hooks/use-prompt-versions";
import CustomTooltip from "src/components/tooltip";
import SvgColor from "src/components/svg-color";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import LabelSelectPopover from "../../VersionHistory/LabelDropdown/LabelSelectPopover";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import MarkdownWithVariableHighlight from "src/components/ReactMarkdownWithHighlight";
import JsonOutputRenderer from "src/sections/prompt/NewPrompt/PromptGenerate/Renderers/JsonOutputRenderer";
import GridIcon from "src/components/gridIcon/GridIcon";
import { useSingleImageViewContext } from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageContext";
import {
  shouldRenderAsJson,
  shouldRenderAsImage,
  isValidUrl,
} from "src/utils/utils";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { PROMPT_RESULT_TYPE_OPTIONS, PROMPT_RESULT_TYPES } from "../common";
import { parseThinkingContent } from "./thinkingUtils";
import ThinkingBlock from "./ThinkingBlock";
import TestAudioPlayer from "src/components/custom-audio/TestAudioPlayer";
import LoadingSkeleton from "./LoadingSkeleton";
import { ShowComponent } from "src/components/show";

const MultiResult = ({
  results,
  selectedVersion: mainSelectedVersion,
  responseFormat,
  getWaveSurferInstance,
  storeWaveSurferInstance,
  updateWaveSurferInstance,
}) => {
  const outputFormat = results?.[0]?.outputFormat;
  const [outputType, setOutputType] = useState(PROMPT_RESULT_TYPES.RAW);
  const theme = useTheme();
  const { setImageUrl } = useSingleImageViewContext();

  const colDefs = useMemo(
    () => [
      {
        headerName: "",
        field: "srno",
        width: 46,
        valueGetter: (params) => {
          return params?.node?.rowIndex + 1;
        },
      },
      {
        headerName: "Result",
        field: "text",
        flex: 1,
        wrapText: true,
        autoHeight: true,
        cellRenderer: (params) => {
          const rawText = params?.data?.text || "";
          const {
            thinking,
            content: displayText,
            isThinking,
          } = parseThinkingContent(rawText);
          const hasThinking = thinking !== null;
          const text = hasThinking ? displayText : rawText;

          const status = params?.data?.status;
          const isImageOutput = shouldRenderAsImage(outputFormat, text);
          const isAudioOutput = outputFormat === "audio";
          const isJsonOutput =
            !isImageOutput && shouldRenderAsJson(responseFormat, text);

          const renderContent = () => {
            if (isAudioOutput) {
              if (!text && (status === "running" || status === "started")) {
                return (
                  <LoadingSkeleton
                    sx={{
                      height: "60px",
                    }}
                  />
                );
              }

              return (
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "border.default",
                    borderRadius: theme.spacing(0.5),
                    px: 1,
                    py: 0.75,
                    my: 1,
                    backgroundColor: "background.paper",
                  }}
                >
                  <TestAudioPlayer
                    audioData={{ url: text }}
                    cacheKey={
                      text ? `workbench-output-audio-${text}` : undefined
                    }
                    getWaveSurferInstance={getWaveSurferInstance}
                    storeWaveSurferInstance={storeWaveSurferInstance}
                    updateWaveSurferInstance={updateWaveSurferInstance}
                  />
                </Box>
              );
            }

            // Image output takes priority
            if (isImageOutput) {
              if (!text && (status === "running" || status === "started")) {
                return (
                  <LoadingSkeleton
                    sx={{
                      width: "300px",
                    }}
                  />
                );
              }
              if (text && !isValidUrl(text)) {
                return (
                  <Typography
                    component={"pre"}
                    sx={{
                      overflow: "hidden",
                      wordWrap: "break-word",
                      whiteSpace: "pre-wrap",
                    }}
                    typography={"m3"}
                    fontWeight={"fontWeightRegular"}
                  >
                    {text}
                  </Typography>
                );
              }
              return (
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
                    maxWidth: "180px",
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                  }}
                />
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
                sx={{
                  overflow: "hidden",
                  wordWrap: "break-word",
                  whiteSpace: "pre-wrap",
                }}
                component={"pre"}
                typography={"m3"}
                color="text.primary"
                fontWeight="fontWeightRegular"
              >
                {text}
              </Typography>
            );
          };

          return (
            <Box className="prompt-output-container">
              <Box sx={{ display: "flex", justifyContent: "space-between" }}>
                <Box sx={{ flex: 1 }}>
                  <OutputMeta metadata={params?.data?.metadata} />
                </Box>
                {!isJsonOutput &&
                  !isImageOutput &&
                  !isAudioOutput &&
                  status !== "running" &&
                  status !== "started" && <CopyButton value={text} />}
              </Box>
              <Box
                className="streaming-text"
                sx={{
                  overflowX: "auto",
                }}
              >
                {hasThinking && (
                  <ThinkingBlock
                    sx={{ mt: 0.5 }}
                    content={thinking}
                    isThinking={isThinking}
                    outputType={outputType}
                  />
                )}
                {renderContent()}
              </Box>
            </Box>
          );
        },
      },
    ],
    [
      outputType,
      outputFormat,
      responseFormat,
      theme,
      setImageUrl,
      getWaveSurferInstance,
      storeWaveSurferInstance,
      updateWaveSurferInstance,
    ],
  );

  const { role } = useAuthContext();
  const canWrite = RolePermission.PROMPTS[PERMISSIONS.UPDATE][role];
  const agThemePrompt = useAgThemePrompt();
  const { id } = useParams();
  const { setOpenSaveTemplate } = usePromptStore();
  const { promptName, selectedVersions } = usePromptWorkbenchContext();
  const baseVersion = mainSelectedVersion ?? selectedVersions[0];
  const { versions, refetch } = usePromptVersions(id);
  const [openTagsModal, setOpenTagsModal] = useState(false);

  const gridRef = useRef();

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      sortable: false,
      filter: false,
      resizable: false,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
      cellRendererSelector: false,
      cellEditorSelector: false,
      cellStyle: {
        lineHeight: "18px",
        padding: "8px",
        fontSize: "12px",
      },
    }),
    [],
  );

  const selectedVersion = useMemo(() => {
    return versions?.find(
      (ver) => ver?.template_version === baseVersion?.version,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseVersion, JSON.stringify(versions)]);

  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", gap: 2, height: "100%" }}
    >
      <Stack
        direction={"row"}
        justifyContent="space-between"
        alignItems="center"
      >
        <Typography
          typography="m2"
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
      <Box style={{ height: "100%", flex: 1, overflow: "auto" }}>
        <AgGridReact
          columnDefs={colDefs}
          theme={agThemePrompt}
          rowData={results}
          defaultColDef={defaultColDef}
          className=" prompt-multi-result"
          domLayout="autoHeight"
          getRowId={(params) => {
            return params?.data?.id;
          }}
          ref={gridRef}
        />
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

MultiResult.propTypes = {
  results: PropTypes.array,
  selectedVersion: PropTypes.object,
  responseFormat: PropTypes.object,
  getWaveSurferInstance: PropTypes.func,
  storeWaveSurferInstance: PropTypes.func,
  updateWaveSurferInstance: PropTypes.func,
};

export default MultiResult;
