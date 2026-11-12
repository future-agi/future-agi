import React, { useState } from "react";
import { Box, Typography } from "@mui/material";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../DrawerRightRenderer/SpanAccordianElements";
import PropTypes from "prop-types";
import { JsonView } from "react-json-view-lite";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";

const FunctionOutput = ({ toolCalls }) => {
  const [outputFormat, setOutputFormat] = useState("Markdown");

  const handleFormatChange = (event) => {
    setOutputFormat(event.target.value);
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
          {toolCalls.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No tool calls found.
            </Typography>
          ) : (
            toolCalls.map((toolCall, index) => {
              const argumentList = Object.keys(toolCall.argumentsObj).map(
                (argName) => ({
                  name: argName,
                  value: toolCall.argumentsObj[argName],
                }),
              );

              return (
                <Accordion
                  key={index}
                  defaultExpanded
                  disableGutters
                  sx={{
                    marginBottom: 2,
                  }}
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
                        {toolCall.functionName}(
                        {argumentList.map((arg) => ` ${arg.name} `).join(", ")})
                      </Typography>
                      {toolCall.id && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{
                            ml: 1,
                            fontFamily: "monospace",
                            fontSize: "12px",
                          }}
                        >
                          {toolCall.id}
                        </Typography>
                      )}
                    </Box>
                  </AccordionSummary>

                  <AccordionDetails sx={{ padding: 2 }}>
                    {argumentList.map((arg, idx) => (
                      <Box
                        key={idx}
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
                            {arg.name}
                          </Typography>
                        </Box>
                        <Typography
                          sx={{
                            padding: 1.5,
                            fontSize: "13px",
                            backgroundColor: "background.neutral",
                            whiteSpace: "pre-wrap",
                            fontFamily:
                              typeof arg.value === "object"
                                ? "monospace"
                                : "inherit",
                          }}
                        >
                          {typeof arg.value === "object"
                            ? JSON.stringify(arg.value, null, 2)
                            : String(arg.value ?? "-")}
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
            data={toolCalls}
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

FunctionOutput.propTypes = {
  toolCalls: PropTypes.array,
};

export default FunctionOutput;
