import React, { useState } from "react";
import { Box, LinearProgress, Typography } from "@mui/material";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import Iconify from "../iconify";
import StatusBadge from "../status-badge/StatusBadge";
import PropTypes from "prop-types";

import "./jsonStyle.css";
import { green, pink } from "@mui/material/colors";
import Markdown from "react-markdown";
import { extractStringValues } from "./common";
import { getSpanAttributes } from "./DrawerRightRenderer/getSpanData";

const BottomGuardrailTab = ({ observationSpan, isLoading }) => {
  const [expandedItems, setExpandedItems] = useState({});

  if (isLoading) return <LinearProgress />;

  const handleAccordionChange = (panel) => (event, isExpanded) => {
    setExpandedItems((prev) => ({
      ...prev,
      [panel]: isExpanded,
    }));
  };

  // Get guardrail data from spanAttributes (with fallback to evalAttributes for backward compatibility)
  const innerSpan =
    observationSpan?.observation_span || observationSpan?.observationSpan;
  const spanAttrs = getSpanAttributes(innerSpan);
  const guardrailRules =
    spanAttrs["guardrail.rules"] || spanAttrs["raw.input"]?.protectRules || [];
  const failedRule = spanAttrs["guardrail.failedRule"];
  const completedRules = spanAttrs["guardrail.completedRules"] || [];
  const uncompletedRules = spanAttrs["guardrail.uncompletedRules"] || [];
  const reasons = spanAttrs["guardrail.reasons"];
  const inputText = spanAttrs["input.value"] || innerSpan?.input || "";

  // If no guardrail rules, show empty state
  if (guardrailRules.length === 0) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography variant="body1" color="textSecondary">
          No guardrail rules configured
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ width: "100%", padding: 2.5 }}>
      {guardrailRules.map((rule, index) => {
        const panelId = `panel-${index}`;
        const isExpanded = expandedItems[panelId] !== false; // Default to expanded
        const ruleName = rule.metric || rule.name || `Rule ${index + 1}`;
        const isTriggered = extractStringValues(failedRule)?.includes(ruleName);
        const isCompleted = completedRules.includes(ruleName);
        const isUncompleted = uncompletedRules.includes(ruleName);

        // Determine rule status
        let ruleStatus = "Not Evaluated";
        let statusRole = "neutral";
        let statusIcon = null;

        if (isTriggered) {
          ruleStatus = "Triggered";
          statusRole = "danger";
          statusIcon = "solar:danger-triangle-linear";
        } else if (isCompleted) {
          ruleStatus = "Passed";
          statusRole = "success";
          statusIcon = "solar:check-circle-linear";
        } else if (isUncompleted) {
          ruleStatus = "Not Evaluated";
          statusRole = "neutral";
        }

        return (
          <Accordion
            key={`${ruleName}-${index}`}
            defaultExpanded
            expanded={isExpanded}
            onChange={handleAccordionChange(panelId)}
            sx={{ mb: 1 }}
          >
            <AccordionSummary>
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  width: "100%",
                }}
              >
                <Box
                  sx={{ display: "flex", gap: 2, ml: 1, alignItems: "center" }}
                >
                  <Typography
                    variant="body2"
                    sx={{ color: "primary.main", fontWeight: 400 }}
                  >
                    Ruleset {index + 1}
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{ fontWeight: 400, color: "text.primary" }}
                  >
                    Metric
                    <span style={{ color: pink[400] }}> = </span>
                    <span style={{ color: green[500] }}>
                      &apos;{ruleName}&apos;
                    </span>
                  </Typography>
                  {rule.target_value && (
                    <Typography
                      variant="body2"
                      sx={{ fontWeight: 400, color: "text.primary" }}
                    >
                      Target_value
                      <span style={{ color: pink[400] }}> = </span>
                      <span style={{ color: green[500] }}>
                        &apos;{rule.target_value}&apos;
                      </span>
                    </Typography>
                  )}
                  {!isExpanded && isTriggered && (
                    <Typography
                      variant="body2"
                      sx={{ fontWeight: 400, color: "text.primary" }}
                    >
                      Text
                      <span style={{ color: pink[400] }}> = </span>
                      <span style={{ color: green[500] }}>
                        {inputText.length > 50
                          ? inputText.substring(0, 50) + "..."
                          : inputText}
                      </span>
                    </Typography>
                  )}
                </Box>
                <StatusBadge
                  status={ruleStatus}
                  label={ruleStatus}
                  role={statusRole}
                  icon={
                    statusIcon ? (
                      <Iconify icon={statusIcon} width={14} />
                    ) : undefined
                  }
                />
              </Box>
            </AccordionSummary>
            <AccordionDetails>
              <Box
                sx={{
                  backgroundColor: "background.paper",
                  borderRadius: "8px",
                  fontSize: "13px",
                  whiteSpace: "pre-wrap",
                  maxHeight: "72vh",
                  overflow: "auto",
                  position: "relative",
                  ml: 13.5,
                  mt: -1.3,
                }}
              >
                {isTriggered ? (
                  <Box>
                    <Typography
                      variant="body2"
                      sx={{ fontWeight: 400, color: "text.primary" }}
                    >
                      Text
                      <span style={{ color: pink[400] }}> = </span>
                      <span style={{ color: green[500] }}>{inputText}</span>
                    </Typography>
                    {reasons && typeof reasons !== "string" && (
                      <Box sx={{ mt: 2 }}>
                        <Typography
                          variant="subtitle2"
                          sx={{ mb: 1, fontWeight: 600 }}
                        >
                          Reason:
                        </Typography>
                        {Array.isArray(reasons) && (
                          <Markdown>{reasons?.[index]}</Markdown>
                        )}
                      </Box>
                    )}
                  </Box>
                ) : (
                  <Box>
                    {isCompleted && (
                      <Typography
                        variant="body2"
                        sx={{ color: "text.secondary" }}
                      >
                        This rule passed successfully.
                      </Typography>
                    )}
                    {isUncompleted && (
                      <Typography
                        variant="body2"
                        sx={{ color: "text.secondary" }}
                      >
                        This rule was not evaluated.
                      </Typography>
                    )}
                    {rule.contains && (
                      <Box sx={{ mt: 1 }}>
                        <Typography
                          variant="subtitle2"
                          sx={{ mb: 1, fontWeight: 600 }}
                        >
                          Contains:
                        </Typography>
                        <Typography
                          variant="body2"
                          sx={{ color: "text.secondary" }}
                        >
                          {rule.contains.join(", ")}
                        </Typography>
                      </Box>
                    )}
                  </Box>
                )}
              </Box>
            </AccordionDetails>
          </Accordion>
        );
      })}
    </Box>
  );
};

BottomGuardrailTab.propTypes = {
  observationSpan: PropTypes.object,
  isLoading: PropTypes.bool,
};

export default BottomGuardrailTab;
