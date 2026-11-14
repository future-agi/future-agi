import {
  Box,
  Stack,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableRow,
  Paper,
  Switch,
  FormControlLabel,
  Divider,
} from "@mui/material";
import React, { useState, useMemo } from "react";
import { diffWordsWithSpace } from "diff";
import ConversationCard from "src/sections/test-detail/TestDetailDrawer/ConversationCard";
import SvgColor from "../../../../components/svg-color/svg-color";
import CustomTooltip from "../../../../components/tooltip/CustomTooltip";
import { ShowComponent } from "../../../../components/show/ShowComponent";
import { CompareConversationSkeleton } from "./Skeletons";
import PropTypes from "prop-types";
import { AGENT_TYPES } from "src/sections/agents/constants";

const CompareConversation = ({
  data: conversations,
  isLoading,
  simulationCallType,
}) => {
  const isVoice = simulationCallType === AGENT_TYPES.VOICE;
  const itemLabel = isVoice ? "Call" : "Session";
  const [showDiff, setShowDiff] = useState(false);
  // Function to compute diff between two messages
  // side: 'A' for baseline (removed), 'B' for replayed (added)
  const computeDiff = (textA, textB, side = null) => {
    if (!textA && !textB) return [];
    if (!textA) return [{ value: textB, added: true }];
    if (!textB) return [{ value: textA, removed: true }];

    const diff = diffWordsWithSpace(textA, textB);
    if (!side) return diff;

    // Filter and merge in one pass
    const targetType = side === "A" ? "removed" : "added";
    const filtered = diff.filter((part) =>
      side === "A" ? !part.added : !part.removed,
    );

    const merged = [];
    for (let i = 0; i < filtered.length; i++) {
      const current = filtered[i];
      const prev = merged[merged.length - 1];

      // Try to merge with previous if same type
      if (
        prev &&
        prev.added === current.added &&
        prev.removed === current.removed
      ) {
        prev.value += current.value;
        continue;
      }

      // Check if current is whitespace between same-type tokens
      if (
        /^\s+$/.test(current.value) &&
        !current.added &&
        !current.removed &&
        prev?.[targetType]
      ) {
        const nextNonWhitespace = filtered
          .slice(i + 1)
          .find((p) => !/^\s+$/.test(p.value));
        if (nextNonWhitespace?.[targetType]) {
          prev.value += current.value;
          continue;
        }
      }

      merged.push({ ...current });
    }

    return merged;
  };

  // Function to render highlighted content for A (red for differences)
  const renderDiffContentA = (textA, textB) => {
    if (!showDiff) return textA;
    const diff = computeDiff(textA, textB, "A");
    return diff.map((part, index) => {
      if (part.removed) {
        return (
          <Typography
            key={index}
            component="span"
            sx={{
              backgroundColor: "status.danger.bg",
              color: "status.danger.fg",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontWeight: "fontWeightMedium",
            }}
            typography="s2_1"
          >
            {part.value}
          </Typography>
        );
      } else {
        return (
          <Typography
            key={index}
            component="span"
            sx={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
            typography="s2_1"
          >
            {part.value}
          </Typography>
        );
      }
    });
  };

  // Function to render highlighted content for B (green for differences)
  const renderDiffContentB = (textA, textB) => {
    if (!showDiff) return textB;
    const diff = computeDiff(textA, textB, "B");

    return diff.map((part, index) => {
      if (part.added) {
        return (
          <Typography
            key={index}
            component="span"
            sx={{
              backgroundColor: "status.success.bg",
              color: "status.success.fg",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontWeight: "fontWeightMedium",
            }}
            typography="s2_1"
          >
            {part.value}
          </Typography>
        );
      } else {
        return (
          <Typography
            key={index}
            component="span"
            sx={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
            typography="s2_1"
          >
            {part.value}
          </Typography>
        );
      }
    });
  };

  // Match conversations by index for comparison
  const matchedConversations = useMemo(() => {
    if (!conversations) return [];
    const baseline = conversations.baselineSession.conversations;
    const replayed = conversations.replayedSession.conversations;
    const maxLength = Math.max(baseline.length, replayed.length);
    const matched = [];

    for (let i = 0; i < maxLength; i++) {
      matched.push({
        baseline: baseline[i] || null,
        replayed: replayed[i] || null,
      });
    }
    return matched;
  }, [conversations]);

  // Calculate total removals and additions (only when diff is enabled)
  const { removalsCount, additionsCount } = useMemo(() => {
    if (!showDiff) {
      return { removalsCount: 0, additionsCount: 0 };
    }

    let removals = 0;
    let additions = 0;

    matchedConversations.forEach((match) => {
      const baselineContent = match.baseline?.content || "";
      const replayedContent = match.replayed?.content || "";

      if (baselineContent || replayedContent) {
        const diffA = computeDiff(baselineContent, replayedContent, "A");
        const diffB = computeDiff(baselineContent, replayedContent, "B");
        diffA.forEach((part) => {
          if (part.removed) {
            removals++;
          }
        });
        diffB.forEach((part) => {
          if (part.added) {
            additions++;
          }
        });
      }
    });

    return { removalsCount: removals, additionsCount: additions };
  }, [matchedConversations, showDiff]);

  if (isLoading) {
    return <CompareConversationSkeleton />;
  }

  return (
    <Stack direction="column" gap={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography
          typography={"m3"}
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
        >
          Conversation Comparison
        </Typography>
        <Stack direction="row" alignItems="center" gap={1}>
          <ShowComponent condition={showDiff}>
            <Stack direction={"row"} alignItems="center" gap={0.5}>
              <SvgColor
                src={"/icons/datasets/diff_pencil.svg"}
                sx={{
                  height: "16px",
                  width: "16px",
                  bgcolor: "status.danger.solid",
                }}
              />
              <Typography
                typography={"s3"}
                fontWeight={"fontWeightMedium"}
                color={"status.danger.fg"}
              >
                Removals ({removalsCount})
              </Typography>
            </Stack>
            <Stack direction={"row"} alignItems="center" gap={0.5}>
              <SvgColor
                src={"/icons/datasets/diff_pencil.svg"}
                sx={{
                  height: "16px",
                  width: "16px",
                  bgcolor: "status.success.solid",
                }}
              />
              <Typography
                typography={"s3"}
                fontWeight={"fontWeightMedium"}
                color={"status.success.fg"}
              >
                Additions ({additionsCount})
              </Typography>
            </Stack>
            <Divider
              orientation="vertical"
              flexItem
              sx={{
                borderColor: "divider",
              }}
            />
          </ShowComponent>
          <Stack direction={"row"} alignItems="center" gap={0.75}>
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={showDiff}
                  onChange={(e) => setShowDiff(e.target.checked)}
                />
              }
              sx={{
                mx: 0,
              }}
              label={
                <Typography
                  typography={"s1"}
                  fontWeight={"fontWeightRegular"}
                  color={"text.primary"}
                >
                  Show Diff
                </Typography>
              }
            />
            <CustomTooltip
              show
              size="small"
              type={"black"}
              title="Show the difference between baseline chat and replayed chat"
              placement="bottom"
              arrow
            >
              <SvgColor
                sx={{ height: "16px", width: "16px", color: "text.primary" }}
                src={"/assets/icons/ic_info.svg"}
              />
            </CustomTooltip>
          </Stack>
        </Stack>
      </Stack>

      <TableContainer
        component={Paper}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          boxShadow: "none",
          maxHeight: "800px",
          overflowY: "auto",
          mb: 2,
        }}
      >
        <Table
          sx={{
            tableLayout: "fixed",
          }}
          stickyHeader
        >
          <TableBody>
            {/* Header Row - Now Sticky */}
            <TableRow>
              <TableCell
                sx={{
                  width: "50%",
                  padding: 2,
                  borderRight: "1px solid",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  backgroundColor: "background.default",
                  position: "sticky",
                  top: 0,
                  zIndex: 10,
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Box
                    sx={{
                      bgcolor: "status.discovery.bg",
                      height: 24,
                      width: 24,
                      borderRadius: "4px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      typography: "s3",
                      fontWeight: "fontWeightMedium",
                      color: "status.discovery.fg",
                    }}
                  >
                    A
                  </Box>
                  <Typography
                    typography={"s2_1"}
                    fontWeight="fontWeightMedium"
                    color={"text.secondary"}
                  >
                    {`Baseline ${itemLabel} Transcript`}
                  </Typography>
                </Box>
              </TableCell>
              <TableCell
                sx={{
                  width: "50%",
                  padding: 1.5,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  backgroundColor: "background.default",
                  position: "sticky",
                  top: 0,
                  zIndex: 10,
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Box
                    sx={{
                      bgcolor: "status.info.bg",
                      height: 24,
                      width: 24,
                      borderRadius: "4px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      typography: "s3",
                      fontWeight: "fontWeightMedium",
                      color: "status.info.fg",
                    }}
                  >
                    B
                  </Box>
                  <Typography
                    typography={"s2_1"}
                    fontWeight="fontWeightMedium"
                    color={"text.secondary"}
                  >
                    {`Replayed ${itemLabel} Transcript`}
                  </Typography>
                </Box>
              </TableCell>
            </TableRow>

            {/* Content Row */}
            <TableRow>
              <TableCell
                sx={{
                  width: "50%",
                  padding: 1.5,
                  borderRight: (theme) =>
                    `1px solid ${theme.palette.divider || "divider"} !important`,
                  verticalAlign: "top",
                  backgroundColor: (theme) =>
                    theme.palette.background.paper || "background.default",
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  {matchedConversations.map((match, index) => {
                    if (!match.baseline) return null;
                    const baselineContent = match.baseline.content || "";
                    const replayedContent = match.replayed?.content || "";
                    const highlightedContent = renderDiffContentA(
                      baselineContent,
                      replayedContent,
                    );
                    return (
                      <ConversationCard
                        key={match.baseline.id || `baseline-${index}`}
                        role={match.baseline.role}
                        content={showDiff ? undefined : baselineContent}
                        highlightedContent={
                          showDiff ? highlightedContent : undefined
                        }
                        align={match.baseline.align}
                        timeStamp={match.baseline.timeStamp}
                        agentName={match.baseline.agent_name}
                        simulatorName={match.baseline.simulatorName}
                        callType={match.baseline.call_type}
                        simulationCallType={AGENT_TYPES.CHAT}
                      />
                    );
                  })}
                </Box>
              </TableCell>
              <TableCell
                sx={{
                  width: "50%",
                  padding: 1.5,
                  verticalAlign: "top",
                  backgroundColor: "background.paper",
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  {matchedConversations.map((match, index) => {
                    if (!match.replayed) return null;
                    const baselineContent = match.baseline?.content || "";
                    const replayedContent = match.replayed.content || "";
                    const highlightedContent = renderDiffContentB(
                      baselineContent,
                      replayedContent,
                    );
                    return (
                      <ConversationCard
                        key={match.replayed.id || `replayed-${index}`}
                        role={match.replayed.role}
                        content={showDiff ? undefined : replayedContent}
                        highlightedContent={
                          showDiff ? highlightedContent : undefined
                        }
                        align={match.replayed.align}
                        timeStamp={match.replayed.timeStamp}
                        agentName={match.replayed.agent_name}
                        simulatorName={match.replayed.simulatorName}
                        callType={match.replayed.call_type}
                        simulationCallType={AGENT_TYPES.CHAT}
                      />
                    );
                  })}
                </Box>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </TableContainer>
    </Stack>
  );
};

export default CompareConversation;

CompareConversation.propTypes = {
  data: PropTypes.object,
  isLoading: PropTypes.bool.isRequired,
  simulationCallType: PropTypes.string,
};
