import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { format } from "date-fns";
import { formatRole } from "src/sections/test/common";
import SvgColor from "src/components/svg-color";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { ShowComponent } from "src/components/show";
import CellMarkdown from "src/sections/common/CellMarkdown";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import { allExpanded, defaultStyles } from "react-json-view-lite";
import { isJsonValue } from "src/utils/utils";
import {
  ToolCallCard,
  ToolResultCard,
} from "src/components/tool-call/ToolCallCard";

const ColorMap = (role) => {
  switch (role) {
    case "system": {
      return {
        borderColor: "orange.100",
        backgroundColor: "orange.o10",
        headerColor: "orange.500",
        textColor: "orange.600",
      };
    }
    case "user": {
      return {
        borderColor: "blue.100",
        backgroundColor: "blue.o10",
      };
    }
    case "assistant": {
      return {
        borderColor: "divider",
        backgroundColor: "background.neutral",
      };
    }
    default: {
      return {
        borderColor: "orange.100",
        backgroundColor: "orange.o10",
      };
    }
  }
};

const ConversationCard = ({
  role,
  content,
  align,
  timeStamp,
  agentName,
  simulatorName,
  callType,
  simulationCallType,
  highlightedContent,
  rawContent,
}) => {
  // Check if content is JSON and parse it
  const isJsonContent = useMemo(() => isJsonValue(content), [content]);

  const parsedJsonContent = useMemo(() => {
    if (!isJsonContent) return null;
    try {
      return typeof content === "string" ? JSON.parse(content) : content;
    } catch {
      return null;
    }
  }, [content, isJsonContent]);

  // Render content based on type
  const renderContent = () => {
    if (highlightedContent) {
      return (
        <Typography
          typography="s2_1"
          fontWeight="fontWeightRegular"
          component="div"
        >
          {highlightedContent}
        </Typography>
      );
    }

    if (isJsonContent && parsedJsonContent) {
      return (
        <CustomJsonViewer
          object={parsedJsonContent}
          shouldExpandNode={allExpanded}
          clickToExpandNode={true}
          style={defaultStyles}
        />
      );
    }

    return <CellMarkdown spacing={0} text={content} />;
  };

  return (
    <Box
      sx={{
        position: "relative",
        width: "100%",
        display: "flex",
        gap: 2,
        justifyContent: align === "flex-end" ? "flex-end" : "flex-start",
      }}
    >
      <ShowComponent
        condition={
          role === "assistant" && simulationCallType === AGENT_TYPES.CHAT
        }
      >
        <Box
          sx={{
            height: 35,
            width: 35,
            borderRadius: "50%",
            bgcolor: "action.hover",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 2,
          }}
        >
          <SvgColor
            src="/assets/icons/ic_bot.svg"
            sx={{
              height: 16,
              width: 16,
              color: "pink.500",
            }}
          />
        </Box>
      </ShowComponent>

      <Box
        sx={{
          display: "flex",
          width: "90%",
          border: "1px solid",
          borderColor: ColorMap(role)?.borderColor,
          backgroundColor: ColorMap(role)?.backgroundColor,
          borderRadius: 0.5,
          padding: 1,
          flexDirection: "column",
          gap: 1,
          alignSelf: align,
          color: "text.primary",
          position: "relative",
          zIndex: 1,
        }}
      >
        <Box sx={{ display: "flex", justifyContent: "space-between" }}>
          <Typography typography="s1_2" fontWeight="fontWeightMedium">
            {formatRole(
              role,
              agentName,
              simulatorName,
              callType,
              simulationCallType,
            )}
          </Typography>

          <Typography typography="s3">
            {typeof timeStamp === "number"
              ? `${Math.floor(timeStamp / 60)}:${String(Math.floor(timeStamp % 60)).padStart(2, "0")}`
              : timeStamp
                ? format(new Date(timeStamp), "h:mm:ss a 'on' MM/dd/yyyy")
                : ""}
          </Typography>
        </Box>
        {renderContent()}
        <ShowComponent
          condition={Array.isArray(rawContent) && rawContent.length > 0}
        >
          <Stack spacing={0.5} sx={{ mt: 0.5 }}>
            {rawContent.flatMap((message, index) => {
              if (message.role === "tool") {
                return [
                  <ToolResultCard
                    key={`result-${index}`}
                    content={message.content}
                  />,
                ];
              }
              if (message.tool_calls?.length > 0) {
                return message.tool_calls.map((toolCall) => (
                  <ToolCallCard
                    key={toolCall.id}
                    toolCall={{
                      name: toolCall.function.name,
                      arguments: toolCall.function.arguments,
                    }}
                  />
                ));
              }
              return [];
            })}
          </Stack>
        </ShowComponent>
      </Box>

      <ShowComponent
        condition={role === "user" && simulationCallType === AGENT_TYPES.CHAT}
      >
        <Box
          sx={{
            height: 35,
            width: 35,
            borderRadius: "50%",
            bgcolor: "action.hover",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 2,
          }}
        >
          <SvgColor
            src="/icons/runTest/ic_user.svg"
            sx={{
              height: 16,
              width: 16,
              color: "blue.500",
            }}
          />
        </Box>
      </ShowComponent>
    </Box>
  );
};

ConversationCard.propTypes = {
  role: PropTypes.string,
  content: PropTypes.string,
  align: PropTypes.string,
  timeStamp: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  agentName: PropTypes.string,
  simulatorName: PropTypes.string,
  callType: PropTypes.string,
  simulationCallType: PropTypes.string,
  highlightedContent: PropTypes.node,
  rawContent: PropTypes.arrayOf(PropTypes.object),
};

export default ConversationCard;
