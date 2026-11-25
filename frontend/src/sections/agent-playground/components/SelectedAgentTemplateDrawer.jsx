import {
  Box,
  Button,
  IconButton,
  Stack,
  Typography,
  CircularProgress,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { useQuery } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import MarkdownWithVariableHighlight from "src/components/ReactMarkdownWithHighlight";
import {
  PreviewContainer,
  PreviewGraphInner,
  PreviewLoading,
  PreviewError,
} from "./PreviewGraphInner";
import { fetchTemplateGraph } from "../data/mockAgentTemplates";
import BackButton from "../../develop-detail/Common/BackButton";

// Template Graph Preview Component
const TemplateGraphPreview = ({ templateId }) => {
  const {
    data: graphData,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["template-graph-preview", templateId],
    queryFn: () => fetchTemplateGraph(templateId),
    enabled: Boolean(templateId),
    select: (response) => response.data,
  });

  if (isLoading) {
    return <PreviewLoading />;
  }

  if (isError || !graphData) {
    return <PreviewError />;
  }

  return (
    <PreviewContainer>
      <ReactFlowProvider>
        <PreviewGraphInner
          nodes={graphData.nodes}
          edges={graphData.edges}
          showControls
        />
      </ReactFlowProvider>
    </PreviewContainer>
  );
};

TemplateGraphPreview.propTypes = {
  templateId: PropTypes.string,
};

export const SelectedAgentTemplateDrawer = ({
  onClose,
  template,
  onUseTemplate,
  isLoading: isUsingTemplate,
}) => {
  const handleUseTemplate = () => {
    if (onUseTemplate && template) {
      onUseTemplate(template);
    }
  };

  if (!template) return null;

  return (
    <Box
      sx={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        width: "60vw",
        maxWidth: 800,
        bgcolor: "background.paper",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 2,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <BackButton onBack={onClose} />
        <IconButton onClick={onClose}>
          <Iconify color="text.primary" icon="solar:close-linear" />
        </IconButton>
      </Box>

      {/* Content */}
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          px: 3,
          py: 2,
        }}
      >
        {/* Template Header */}
        <Stack
          direction="row"
          alignItems="flex-start"
          justifyContent="space-between"
          sx={{ mb: 3 }}
        >
          <Stack direction="row" gap={2} alignItems="flex-start">
            <Box
              sx={{
                border: "2px solid",
                borderColor: "status.discovery.border",
                backgroundColor: "status.discovery.bg",
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: 44,
                width: 44,
                borderRadius: "8px",
                flexShrink: 0,
              }}
            >
              <SvgColor
                src="/assets/icons/navbar/ic_agents.svg"
                sx={{
                  bgcolor: "status.discovery.solid",
                  height: 20,
                  width: 20,
                }}
              />
            </Box>
            <Box
              sx={{
                mr: 2,
              }}
            >
              <Typography
                typography="m3"
                fontWeight="fontWeightMedium"
                color="text.primary"
              >
                {template.name}
              </Typography>
              <Typography typography="s2" color="text.secondary">
                {template.description}
              </Typography>
              <Typography
                typography="s3"
                color="text.disabled"
                sx={{ mt: 0.5 }}
              >
                By {template.createdBy}
              </Typography>
            </Box>
          </Stack>
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={handleUseTemplate}
            disabled={isUsingTemplate}
            sx={{
              flexShrink: 0,
            }}
            startIcon={
              isUsingTemplate ? (
                <CircularProgress size={16} color="inherit" />
              ) : null
            }
          >
            {isUsingTemplate ? "Loading..." : "Use this template"}
          </Button>
        </Stack>

        {/* Graph Preview */}
        <TemplateGraphPreview templateId={template.id} />

        {/* Documentation - Rendered as Markdown */}
        {template.documentation && (
          <Box
            sx={{
              "& h2": {
                typography: "s1",
                fontWeight: "fontWeightMedium",
                color: "text.primary",
                mt: 3,
                mb: 1,
              },
              "& p": {
                typography: "s2",
                color: "text.secondary",
                mb: 1.5,
              },
              "& ul, & ol": {
                pl: 2,
                m: 0,
                mb: 1.5,
              },
              "& li": {
                typography: "s2",
                color: "text.secondary",
                mb: 0.5,
              },
            }}
          >
            <MarkdownWithVariableHighlight content={template.documentation} />
          </Box>
        )}
      </Box>
    </Box>
  );
};

SelectedAgentTemplateDrawer.propTypes = {
  onClose: PropTypes.func,
  template: PropTypes.object,
  onUseTemplate: PropTypes.func,
  isLoading: PropTypes.bool,
};

export default SelectedAgentTemplateDrawer;
