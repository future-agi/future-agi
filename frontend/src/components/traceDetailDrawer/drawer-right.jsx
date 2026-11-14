import React from "react";
import { useSelectedNode } from "./useSelectedNode";
import { useNavigate } from "react-router";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "../iconify";
import { Typography, Box, Button, Skeleton, useTheme } from "@mui/material";
import _ from "lodash";
import SvgColor from "../svg-color";
import DrawerRightRenderer from "./DrawerRightRenderer/DrawerRightRenderer";
import {
  getObservationType,
  getPromptTemplateId,
  getSpanAttributes,
} from "./DrawerRightRenderer/getSpanData";
import PropTypes from "prop-types";
import StatusChip from "../custom-status-chip/CustomStatusChip";

const DrawerRightSkeleton = () => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        paddingX: theme.spacing(2),
        paddingTop: theme.spacing(1.5),
        display: "flex",
        flexDirection: "column",
        gap: theme.spacing(2),
      }}
    >
      <Skeleton variant="rectangular" width="20%" height={22} />
      <Skeleton variant="rectangular" width="70%" height={22} />
      <Skeleton variant="rectangular" width="12%" height={22} />

      <Box
        sx={{
          padding: theme.spacing(2.5),
          border: "1px solid",
          borderColor: "divider",
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(1.5),
          borderRadius: theme.spacing(1),
        }}
      >
        <Skeleton variant="rectangular" width="6%" height={22} />
        <Skeleton variant="rectangular" width="60%" height={22} />
      </Box>
      <Box
        sx={{
          padding: theme.spacing(2.5),
          border: "1px solid",
          borderColor: "divider",
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(1.5),
          borderRadius: theme.spacing(1),
        }}
      >
        <Skeleton variant="rectangular" width="6%" height={22} />
        <Skeleton variant="rectangular" width="60%" height={22} />
      </Box>
    </Box>
  );
};

const DrawerRight = ({
  setActionToDataset,
  onAnnotate,
  observationSpan,
  observationSpanLoading,
}) => {
  const { selectedNode } = useSelectedNode();
  const navigate = useNavigate();

  const span = observationSpan?.observation_span;
  const observationType = getObservationType(span);
  const promptTemplateId = getPromptTemplateId(span);
  const isLlmSpan = observationType?.toLowerCase() === "llm";

  const { mutate: createPromptDraft, isPending: isCreatingDraft } = useMutation(
    {
      mutationFn: (body) =>
        axios.post(endpoints.develop.runPrompt.createPromptDraft, body),
      onSuccess: (data) => {
        const newId =
          data?.data?.result?.rootTemplate ||
          data?.data?.result?.root_template ||
          data?.data?.result?.id;
        if (newId) {
          navigate(`/dashboard/workbench/create/${newId}?tab=Playground`);
        }
      },
    },
  );

  const handleIterateInWorkbench = () => {
    if (promptTemplateId) {
      navigate(
        `/dashboard/workbench/create/${promptTemplateId}?tab=Playground`,
      );
      return;
    }

    const attrs = getSpanAttributes(span);

    // Parse template variables from span attributes
    let templateVars = null;
    const rawVars =
      attrs?.["gen_ai.prompt.template.variables"] ||
      attrs?.["llm.prompt_template.variables"];
    if (rawVars) {
      try {
        const parsed =
          typeof rawVars === "string" ? JSON.parse(rawVars) : rawVars;
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
          templateVars = parsed;
      } catch {
        /* ignore */
      }
    }

    // Replace resolved values with {{var}} placeholders
    const templatize = (text) => {
      if (!text || !templateVars) return text;
      let result = text;
      const entries = Object.entries(templateVars)
        .filter(([, val]) => val != null && String(val).length > 0)
        .sort((a, b) => String(b[1]).length - String(a[1]).length);
      for (const [name, value] of entries) {
        const strVal = String(value);
        let idx = result.indexOf(strVal);
        while (idx !== -1) {
          result =
            result.slice(0, idx) +
            `{{${name}}}` +
            result.slice(idx + strVal.length);
          idx = result.indexOf(strVal, idx + `{{${name}}}`.length);
        }
      }
      return result;
    };

    // Build messages from span attributes
    const tempMessages = {};
    const messagePrefixes = [
      "llm.inputMessages",
      "llm.input_messages",
      "gen_ai.input.messages",
    ];

    Object.keys(attrs).forEach((key) => {
      const matchingPrefix = messagePrefixes.find((prefix) =>
        key.startsWith(prefix),
      );
      if (!matchingPrefix) return;
      const parts = key.replace(`${matchingPrefix}.`, "").split(".");
      const index = parts[0];
      const property = parts.slice(1).join(".");
      if (!tempMessages[index]) tempMessages[index] = {};
      if (property === "message.role" || property === "role") {
        tempMessages[index].role = attrs[key];
      }
      if (
        property.startsWith("message.content") ||
        property.startsWith("content")
      ) {
        let content = attrs[key];
        if (typeof content === "object" && content !== null)
          content = JSON.stringify(content, null, 2);
        if (!tempMessages[index].content) tempMessages[index].content = content;
      }
    });

    const parsedMessages = Object.keys(tempMessages)
      .sort((a, b) => parseInt(a) - parseInt(b))
      .filter((key) => tempMessages[key].role)
      .map((key) => {
        let text =
          typeof tempMessages[key].content === "string"
            ? tempMessages[key].content
            : JSON.stringify(tempMessages[key].content ?? "", null, 2);
        text = templatize(text);
        return {
          role: tempMessages[key].role,
          content: [{ type: "text", text }],
        };
      });

    if (parsedMessages.length === 0 && span?.input) {
      let inputContent =
        typeof span.input === "string"
          ? span.input
          : JSON.stringify(span.input, null, 2);
      inputContent = templatize(inputContent);
      parsedMessages.push(
        { role: "system", content: [{ type: "text", text: "" }] },
        { role: "user", content: [{ type: "text", text: inputContent }] },
      );
    }

    if (parsedMessages.length > 0 && parsedMessages[0].role !== "system") {
      parsedMessages.unshift({
        role: "system",
        content: [{ type: "text", text: "" }],
      });
    }

    // Build variable_names: { varName: [sampleValue] }
    const variableNames = {};
    if (templateVars) {
      for (const [name, value] of Object.entries(templateVars)) {
        variableNames[name] = value != null ? [String(value)] : [];
      }
    }

    createPromptDraft({
      name: "",
      prompt_config: [{ messages: parsedMessages }],
      ...(Object.keys(variableNames).length > 0 && {
        variable_names: variableNames,
      }),
    });
  };

  const isDataReady = !observationSpanLoading && selectedNode;
  if (!isDataReady) {
    return <DrawerRightSkeleton />;
  }

  return (
    <Box
      sx={{
        height: "100%",
        paddingX: (theme) => theme.spacing(2),
        paddingTop: (theme) => theme.spacing(1.2),
        overflowY: "auto",
        borderRadius: 0,
        "&::-webkit-scrollbar": {
          width: (theme) => theme.spacing(0.75),
        },
        "&::-webkit-scrollbar-thumb": {
          backgroundColor: "border.strong",
          borderRadius: (theme) => theme.spacing(0.25),
        },
        "&::-webkit-scrollbar-track": {
          backgroundColor: "transparent",
        },
      }}
    >
      <Typography
        typography="m3"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
      >
        {_.capitalize(selectedNode?.observation_type) || "No node selected"} -{" "}
        {selectedNode?.name || "No node selected"}
      </Typography>
      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          gap: (theme) => theme.spacing(1),
          marginY: (theme) => theme.spacing(2),
        }}
      >
        <StatusChip status={observationSpan?.observation_span?.status} />
        {/* Total Cost */}

        {(selectedNode?.trace !== null || selectedNode?.trace_id !== null) && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              paddingX: (theme) => theme.spacing(1),
              paddingY: (theme) => theme.spacing(0.5),
              borderRadius: (theme) => theme.spacing(1),
              backgroundColor: "background.paper",
              color: "text.secondary",
              border: "1px solid",
              borderColor: "divider",
              whiteSpace: "nowrap",
              height: "22px",
              gap: (theme) => theme.spacing(1),
            }}
          >
            <Typography
              typography="s3"
              fontWeight="fontWeightRegular"
              color="text.primary"
            >
              Trace ID: {selectedNode?.trace || selectedNode?.trace_id}
            </Typography>
          </Box>
        )}
        {selectedNode?.id !== null && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              paddingX: (theme) => theme.spacing(1),
              paddingY: (theme) => theme.spacing(0.5),
              borderRadius: (theme) => theme.spacing(1),
              backgroundColor: "background.paper",
              color: "text.secondary",
              border: "1px solid",
              borderColor: "divider",
              whiteSpace: "nowrap",
              height: "22px",
              gap: (theme) => theme.spacing(1),
            }}
          >
            <Typography
              typography="s3"
              fontWeight="fontWeightRegular"
              color="text.primary"
            >
              Span ID: {selectedNode?.id}
            </Typography>
          </Box>
        )}

        {selectedNode?.session_id && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              paddingX: (theme) => theme.spacing(1),
              paddingY: (theme) => theme.spacing(0.5),
              borderRadius: (theme) => theme.spacing(1),
              backgroundColor: "background.paper",
              color: "primary.main",
              border: "1px solid",
              borderColor: "divider",
              whiteSpace: "nowrap",
              height: "22px",
              gap: (theme) => theme.spacing(1),
            }}
          >
            <Typography
              typography="s3"
              fontWeight="fontWeightRegular"
              color="primary.main"
            >
              Session ID: {selectedNode?.session_id}
            </Typography>
          </Box>
        )}
        {selectedNode?.cost > 0 && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              paddingX: (theme) => theme.spacing(1),
              paddingY: (theme) => theme.spacing(0.5),
              borderRadius: (theme) => theme.spacing(1),
              backgroundColor: "background.paper",
              color: "text.secondary",
              border: "1px solid",
              borderColor: "divider",
              whiteSpace: "nowrap",
              height: "22px",
              gap: (theme) => theme.spacing(1),
            }}
          >
            <SvgColor
              src={`/assets/icons/components/ic_newcoin.svg`}
              sx={{ width: "15px", height: "15px", color: "text.secondary" }}
            />
            <Typography
              typography="s3"
              fontWeight="fontWeightRegular"
              color="text.primary"
            >
              Total Cost: ${selectedNode?.cost}
            </Typography>
          </Box>
        )}
        {selectedNode?.total_tokens !== null && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              paddingX: (theme) => theme.spacing(1),
              paddingY: (theme) => theme.spacing(0.5),
              borderRadius: (theme) => theme.spacing(1),
              backgroundColor: "background.paper",
              color: "text.secondary",
              border: "1px solid",
              borderColor: "divider",
              whiteSpace: "nowrap",
              height: "22px",
              gap: (theme) => theme.spacing(1),
            }}
          >
            <SvgColor
              src={`/assets/icons/components/ic_newcoin.svg`}
              sx={{ width: "15px", height: "15px", color: "text.secondary" }}
            />
            <Typography
              typography="s3"
              fontWeight="fontWeightRegular"
              color="text.primary"
            >
              Total Tokens: {selectedNode?.total_tokens}
            </Typography>
          </Box>
        )}

        {/* Latency */}
        {selectedNode?.latency_ms !== null && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              paddingX: (theme) => theme.spacing(1),
              paddingY: (theme) => theme.spacing(0.5),
              borderRadius: (theme) => theme.spacing(1),
              backgroundColor: "background.paper",
              color: "text.secondary",
              border: "1px solid",
              borderColor: "divider",
              whiteSpace: "nowrap",
              height: "22px",
              gap: (theme) => theme.spacing(1),
            }}
          >
            <Iconify
              icon="solar:clock-circle-linear"
              color="text.secondary"
              width={16}
            />
            <Typography
              typography="s3"
              fontWeight="fontWeightRegular"
              color="text.primary"
            >
              Latency: {selectedNode?.latency_ms}ms
            </Typography>
          </Box>
        )}
      </Box>
      <Box
        sx={{
          display: "flex",
          gap: (theme) => theme.spacing(1),
          flexWrap: "wrap",
        }}
      >
        <Button
          size="small"
          onClick={() => setActionToDataset(true)}
          color="primary"
          variant="outlined"
        >
          <Iconify
            icon="solar:add-linear"
            sx={{
              mr: (theme) => theme.spacing(0.5),
              width: "16px",
              height: "16px",
            }}
          />
          Add to Dataset
        </Button>
        <Button
          variant="outlined"
          color="primary"
          onClick={() => onAnnotate?.()}
          size="small"
          startIcon={<Iconify icon="solar:pen-linear" />}
        >
          Annotate
        </Button>
        {isLlmSpan && (
          <Button
            variant="outlined"
            color="primary"
            onClick={handleIterateInWorkbench}
            size="small"
            disabled={isCreatingDraft}
            startIcon={<Iconify icon="solar:pen-new-square-linear" />}
          >
            {isCreatingDraft ? "Creating..." : "Iterate in Workbench"}
          </Button>
        )}
      </Box>
      <DrawerRightRenderer
        observationSpan={observationSpan?.observation_span}
      />
    </Box>
  );
};

DrawerRight.propTypes = {
  onAnnotate: PropTypes.func,
  setActionToDataset: PropTypes.func,
  observationSpan: PropTypes.object,
  observationSpanLoading: PropTypes.bool,
};

export default DrawerRight;
