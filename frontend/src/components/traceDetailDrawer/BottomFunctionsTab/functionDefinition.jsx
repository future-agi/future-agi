import React, { useState } from "react";
import { Box, Chip, Typography } from "@mui/material";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../DrawerRightRenderer/SpanAccordianElements";
import PropTypes from "prop-types";
import { JsonView } from "react-json-view-lite";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";

const FunctionDefinition = ({ toolDefinitions = [] }) => {
  const [outputFormat, setOutputFormat] = useState("Markdown");

  const handleFormatChange = (event) => {
    setOutputFormat(event.target.value);
  };

  const getParamEntries = (parameters) => {
    const props = parameters?.properties;
    if (!props || typeof props !== "object") return [];
    const required = parameters?.required || [];
    return Object.entries(props).map(([name, schema]) => ({
      name,
      type: schema?.type || "any",
      description: schema?.description || "",
      isRequired: required.includes(name),
    }));
  };

  return (
    <Box sx={{ mb: 2 }}>
      <Box sx={{ mb: 2 }}>
        <FormSearchSelectFieldState
          size="small"
          value={outputFormat}
          onChange={handleFormatChange}
          showClear={false}
          displayEmpty
          sx={{ maxWidth: 150 }}
          options={[
            { label: "Markdown", value: "Markdown" },
            { label: "JSON", value: "JSON" },
          ]}
        />
      </Box>

      {/* Markdown View */}
      {outputFormat === "Markdown" && (
        <Box sx={{ mt: 2 }}>
          {toolDefinitions.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No tool definitions found.
            </Typography>
          ) : (
            toolDefinitions.map((toolDef, index) => {
              const params = getParamEntries(toolDef.parameters);
              const signature =
                params.length > 0
                  ? params.map((p) => `${p.name}: ${p.type}`).join(", ")
                  : "";

              return (
                <Accordion
                  key={index}
                  defaultExpanded
                  disableGutters
                  sx={{ marginBottom: 2 }}
                >
                  <AccordionSummary>
                    <Box
                      sx={{
                        display: "flex",
                        justifyContent: "space-between",
                        paddingX: 1,
                        alignItems: "center",
                        width: "100%",
                      }}
                    >
                      <Typography variant="s1" color="text.secondary">
                        {toolDef.name}({signature})
                      </Typography>
                    </Box>
                  </AccordionSummary>

                  <AccordionDetails sx={{ padding: 2 }}>
                    {/* Description */}
                    {toolDef.description && (
                      <Box
                        sx={{
                          border: "1px solid",
                          borderColor: "action.selected",
                          borderRadius: "8px",
                          mb: 2,
                          overflow: "hidden",
                        }}
                      >
                        <Box
                          sx={{
                            borderBottom: "1px solid",
                            borderColor: "divider",
                          }}
                        >
                          <Typography
                            typography="s1"
                            sx={{
                              fontWeight: "500",
                              color: "primary.main",
                              padding: 1.5,
                            }}
                          >
                            Description
                          </Typography>
                        </Box>
                        <Typography
                          sx={{
                            padding: 1.5,
                            fontSize: "14px",
                            backgroundColor: "background.neutral",
                          }}
                        >
                          {toolDef.description}
                        </Typography>
                      </Box>
                    )}

                    {/* Parameters */}
                    {params.map((param, idx) => (
                      <Box
                        key={idx}
                        sx={{
                          border: "1px solid",
                          borderColor: "#938FA333",
                          borderRadius: "8px",
                          mb: 2,
                          overflow: "hidden",
                        }}
                      >
                        <Box
                          sx={{
                            borderBottom: "1px solid",
                            borderColor: "divider",
                            display: "flex",
                            alignItems: "center",
                            gap: 1,
                          }}
                        >
                          <Typography
                            typography="s1"
                            sx={{
                              fontWeight: "500",
                              color: "primary.main",
                              padding: 1.5,
                            }}
                          >
                            {param.name}
                          </Typography>
                          <Chip
                            label={param.type}
                            size="small"
                            variant="outlined"
                            sx={{ height: 20, fontSize: "11px" }}
                          />
                          {param.isRequired && (
                            <Chip
                              label="required"
                              size="small"
                              color="warning"
                              variant="outlined"
                              sx={{ height: 20, fontSize: "11px" }}
                            />
                          )}
                        </Box>
                        <Typography
                          sx={{
                            padding: 1.5,
                            fontSize: "14px",
                            backgroundColor: "background.neutral",
                          }}
                        >
                          {param.description || "No description"}
                        </Typography>
                      </Box>
                    ))}
                  </AccordionDetails>
                </Accordion>
              );
            })
          )}
        </Box>
      )}

      {/* JSON View */}
      {outputFormat === "JSON" && (
        <Box sx={{ mt: 2 }}>
          <JsonView
            data={toolDefinitions}
            shouldExpandNode={() => true}
            style={{
              container: "attributesJsonContainer",
              basicChildStyle: "attributesJsonChild",
              label: "attributesLabel",
              clickableLabel: "attributesClickableLabel",
              nullValue: "attributesNullValue",
              undefinedValue: "attributesUndefinedValue",
              numberValue: "attributesNumberValue",
              stringValue: "attributesStringValue",
              booleanValue: "attributesBooleanValue",
              otherValue: "attributesOtherValue",
              punctuation: "attributesPunctuation",
              expandIcon: "customExpandIcon",
              collapseIcon: "customCollapseIcon",
              collapsedContent: "customCollapsedContent",
            }}
          />
        </Box>
      )}
    </Box>
  );
};

FunctionDefinition.propTypes = {
  toolDefinitions: PropTypes.array,
};

export default FunctionDefinition;
