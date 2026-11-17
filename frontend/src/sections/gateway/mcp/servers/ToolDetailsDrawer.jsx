import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import {
  DetailSection,
  GatewayDetailDrawer,
  GatewayStatusChip,
} from "../../components/frame";
import JsonBlock from "../components/JsonBlock";
import { annotationMarks, toolFacts, toolInputSchema } from "./toolDetails";

// One tool of the inventory while MCP is on: what its server and the
// gateway's tool versions say about it. Nothing is fetched or filled in; a
// fact the inventory does not carry is not shown.
export default function ToolDetailsDrawer({ tool, onClose }) {
  const facts = toolFacts(tool);
  const marks = annotationMarks(tool?.annotations);
  const schema = toolInputSchema(tool);
  const nothing =
    !facts.description &&
    !facts.version &&
    !facts.deprecated &&
    marks.length === 0 &&
    !schema;
  return (
    <GatewayDetailDrawer
      open={Boolean(tool)}
      onClose={onClose}
      title={tool?.name || ""}
      subtitle={
        tool
          ? `Server ${tool.server_id}${tool.upstream_name ? `, upstream name ${tool.upstream_name}` : ""}`
          : undefined
      }
    >
      {tool && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {facts.description && (
            <Typography typography="s2" color="text.primary">
              {facts.description}
            </Typography>
          )}
          {facts.version && (
            <Box sx={{ display: "flex" }}>
              <GatewayStatusChip
                tone="neutral"
                label={facts.version}
                showIcon={false}
              />
            </Box>
          )}
          {facts.deprecated && (
            <Box
              sx={{
                p: 1.5,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "warning.light",
                bgcolor: "warning.lighter",
                display: "flex",
                flexDirection: "column",
                gap: 0.5,
              }}
            >
              <Typography
                component="h3"
                typography="s1"
                fontWeight="fontWeightMedium"
                color="warning.dark"
              >
                Deprecated
              </Typography>
              {facts.deprecationMessage && (
                <Typography typography="s2" color="warning.dark">
                  {facts.deprecationMessage}
                </Typography>
              )}
              {facts.replacedBy && (
                <Typography typography="s2" color="warning.dark">
                  {`Replacement: ${facts.replacedBy}`}
                </Typography>
              )}
            </Box>
          )}
          {marks.length > 0 && (
            <DetailSection title="Annotations">
              <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
                {marks.map((mark) => (
                  <GatewayStatusChip
                    key={mark.key}
                    tone={mark.tone}
                    label={mark.label}
                    showIcon={false}
                  />
                ))}
              </Box>
            </DetailSection>
          )}
          {schema && (
            <DetailSection title="Input schema">
              <JsonBlock value={schema} label="Input schema" maxHeight={300} />
            </DetailSection>
          )}
          {nothing && (
            <Typography typography="s2" color="text.secondary">
              Its server sent no description, annotations or input schema for
              this tool.
            </Typography>
          )}
        </Box>
      )}
    </GatewayDetailDrawer>
  );
}

ToolDetailsDrawer.propTypes = {
  tool: PropTypes.object,
  onClose: PropTypes.func.isRequired,
};
