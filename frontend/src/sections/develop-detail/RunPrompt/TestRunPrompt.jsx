import { Box, Tab, Tabs, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import CellMarkdown from "src/sections/common/CellMarkdown";
import TestAudioPlayer from "../../../components/custom-audio/TestAudioPlayer";
import ImageOutputRenderer from "src/sections/prompt/NewPrompt/PromptGenerate/Renderers/ImageOutputRenderer";
import { MODEL_TYPES } from "./common";
import { parseThinkingContent } from "src/sections/workbench/createPrompt/Playground/OutputSection/thinkingUtils";
import ThinkingBlock from "src/sections/workbench/createPrompt/Playground/OutputSection/ThinkingBlock";
import { PROMPT_RESULT_TYPES } from "src/sections/workbench/createPrompt/Playground/common";

const TestRunPrompt = ({ previewData, modelType }) => {
  const [selectedTab, setSelectedTab] = useState("markdown");

  const handleChange = (event, newValue) => {
    setSelectedTab(newValue);
  };

  const memoisedPreviewData = useMemo(() => {
    switch (selectedTab) {
      case "raw":
        return <RawPreview previewData={previewData} />;
      case "markdown":
        return (
          <MarkdownPreview previewData={previewData} modelType={modelType} />
        );
      default:
        return <></>;
    }
  }, [selectedTab, previewData, modelType]);

  return (
    <Box
      sx={{
        paddingY: "20px",
        gap: "20px",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        width: "550px",
        borderRight: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        variant="m3"
        fontWeight={"fontWeightMedium"}
        color="text.primary"
        sx={{ paddingX: "20px" }}
      >
        Prompt Test
      </Typography>
      <Box
        sx={{
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        <Box
          sx={{
            borderBottom: 1,
            borderColor: "divider",
            paddingX: "20px",
            position: "sticky",
            top: 0,
            backgroundColor: "background.paper",
          }}
        >
          <Tabs
            value={selectedTab}
            onChange={handleChange}
            aria-label="basic tabs example"
            textColor="primary"
          >
            <Tab label="Markdown" value="markdown" />
            <Tab label="Raw" value="raw" />
          </Tabs>
        </Box>
        <Box sx={{ overflowY: "auto" }}>{memoisedPreviewData}</Box>
      </Box>
    </Box>
  );
};

const RawPreview = ({ previewData }) => {
  return (
    <Box
      sx={{
        paddingX: "20px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      {previewData?.data?.result?.responses
        ?.slice()
        .reverse()
        .map((response, index) => {
          const { thinking, content: displayText } =
            parseThinkingContent(response);
          const text = thinking !== null ? displayText : response;

          return (
            <Box
              key={index}
              sx={{
                border: "2px solid",
                borderColor: "background.neutral",
                borderRadius: "8px",
                padding: "12px 16px",
                wordWrap: "break-word",
                overflow: "hidden",
              }}
            >
              {thinking !== null && (
                <ThinkingBlock
                  content={thinking}
                  isThinking={false}
                  outputType={PROMPT_RESULT_TYPES.RAW}
                />
              )}
              <Typography
                sx={{
                  overflowWrap: "break-word",
                  wordBreak: "break-word",
                  whiteSpace: "pre-wrap",
                }}
                variant="body2"
              >
                {text}
              </Typography>
            </Box>
          );
        })}
    </Box>
  );
};

const MarkdownPreview = ({ previewData, modelType }) => {
  const isImage = modelType === MODEL_TYPES.IMAGE;
  const isAudio = modelType === MODEL_TYPES.TTS;

  return (
    <Box
      sx={{
        paddingX: "20px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      {previewData?.data?.result?.responses
        ?.slice()
        .reverse()
        ?.map((response, index) => {
          const markdownText = Array.isArray(response)
            ? response.join("")
            : response ?? "";

          const { thinking, content: displayText } =
            parseThinkingContent(markdownText);
          const text = thinking !== null ? displayText : markdownText;

          return (
            <Box
              key={index}
              sx={{
                border: "2px solid",
                borderColor: "background.neutral",
                borderRadius: "8px",
                padding: "12px 16px",
                wordWrap: "break-word",
                overflow: "hidden",
              }}
            >
              {thinking !== null && (
                <ThinkingBlock
                  content={thinking}
                  isThinking={false}
                  outputType={PROMPT_RESULT_TYPES.MARKDOWN}
                />
              )}
              {isImage ? (
                <ImageOutputRenderer src={text} alt="Generated image" />
              ) : isAudio ? (
                <TestAudioPlayer
                  audioData={{
                    url: text,
                  }}
                />
              ) : (
                <Typography
                  variant="body2"
                  sx={{
                    overflowWrap: "break-word",
                    wordBreak: "break-word",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  <CellMarkdown spacing={0} text={text} />
                </Typography>
              )}
            </Box>
          );
        })}
    </Box>
  );
};

RawPreview.propTypes = {
  previewData: PropTypes.object,
};

MarkdownPreview.propTypes = {
  previewData: PropTypes.object,
  modelType: PropTypes.string,
};

TestRunPrompt.propTypes = {
  previewData: PropTypes.object,
  modelType: PropTypes.string,
};

export default TestRunPrompt;
