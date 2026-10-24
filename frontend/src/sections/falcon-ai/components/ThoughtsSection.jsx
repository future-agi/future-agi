import React, { useState, useEffect } from "react";
import ButtonBase from "@mui/material/ButtonBase";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import Iconify from "src/components/iconify";

function StepIcon({ status }) {
  if (status === "completed") {
    return (
      <Iconify
        icon="solar:check-circle-bold"
        width={20}
        sx={{ color: "success.main" }}
      />
    );
  }
  if (status === "in_progress") {
    return <CircularProgress size={16} thickness={4} />;
  }
  if (status === "error") {
    return (
      <Iconify
        icon="solar:danger-circle-linear"
        width={20}
        sx={{ color: "error.main" }}
      />
    );
  }
  return (
    <Iconify
      icon="solar:circle-linear"
      width={20}
      sx={{ color: "text.disabled" }}
    />
  );
}

StepIcon.propTypes = {
  status: PropTypes.string.isRequired,
};

export default function ThoughtsSection({ thoughts, isStreaming }) {
  // Auto-expand when streaming, allow manual toggle when done
  const hasInProgress = thoughts?.some((t) => t.status === "in_progress");
  const [manualToggle, setManualToggle] = useState(null); // null = auto mode

  // Auto-expand during streaming
  useEffect(() => {
    if (hasInProgress || isStreaming) {
      setManualToggle(null); // reset to auto mode during streaming
    }
  }, [hasInProgress, isStreaming]);

  if (!thoughts || thoughts.length === 0) return null;

  const expanded =
    manualToggle !== null ? manualToggle : hasInProgress || isStreaming;

  const completedCount = thoughts.filter(
    (t) => t.status === "completed",
  ).length;
  const totalCount = thoughts.length;
  const statusText =
    hasInProgress || isStreaming
      ? `Thinking... (${completedCount}/${totalCount})`
      : `${completedCount} step${completedCount !== 1 ? "s" : ""} completed`;

  return (
    <Box
      sx={{
        mb: 1.5,
        border: 1,
        borderColor: hasInProgress ? "primary.main" : "divider",
        borderRadius: "8px",
        overflow: "hidden",
        transition: "border-color 0.2s",
      }}
    >
      {/* Header — always visible */}
      <ButtonBase
        component="div"
        onClick={() =>
          setManualToggle((prev) => (prev === null ? !expanded : !prev))
        }
        sx={{
          justifyContent: "flex-start",
          textAlign: "left",
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.5,
          py: 1,
          cursor: "pointer",
          userSelect: "none",
          bgcolor: hasInProgress ? "primary.lighter" : "background.neutral",
          "&:hover": { opacity: 0.85 },
        }}
      >
        {hasInProgress ? (
          <CircularProgress size={14} thickness={5} />
        ) : (
          <Iconify
            icon="solar:check-circle-bold"
            width={16}
            sx={{ color: "success.main" }}
          />
        )}
        <Typography variant="caption" fontWeight={600} sx={{ flex: 1 }}>
          {statusText}
        </Typography>
        <Iconify
          icon={
            expanded
              ? "solar:alt-arrow-up-linear"
              : "solar:alt-arrow-down-linear"
          }
          width={20}
          sx={{ color: "text.secondary" }}
        />
      </ButtonBase>

      {/* Steps timeline — collapsible */}
      <Collapse in={expanded}>
        <Box sx={{ px: 1.5, py: 1 }}>
          {thoughts.map((thought, index) => (
            <Box
              key={thought.step ?? index}
              sx={{
                display: "flex",
                gap: 1,
                mb: index < thoughts.length - 1 ? 0.5 : 0,
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  pt: 0.25,
                }}
              >
                <StepIcon status={thought.status} />
                {index < thoughts.length - 1 && (
                  <Box
                    sx={{
                      width: "1.5px",
                      flex: 1,
                      bgcolor: "divider",
                      mt: 0.25,
                      minHeight: 8,
                    }}
                  />
                )}
              </Box>
              <Box sx={{ pb: 0.75, minWidth: 0 }}>
                <Typography
                  variant="body2"
                  fontWeight={thought.status === "in_progress" ? 600 : 400}
                  sx={{
                    color:
                      thought.status === "pending"
                        ? "text.disabled"
                        : "text.primary",
                  }}
                >
                  {thought.name}
                </Typography>
                {thought.result && (
                  <Typography
                    variant="caption"
                    sx={{
                      color: "text.secondary",
                      display: "block",
                      mt: 0.25,
                    }}
                  >
                    {thought.result}
                  </Typography>
                )}
              </Box>
            </Box>
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}

ThoughtsSection.propTypes = {
  thoughts: PropTypes.arrayOf(
    PropTypes.shape({
      step: PropTypes.number,
      name: PropTypes.string,
      status: PropTypes.string,
      result: PropTypes.string,
    }),
  ),
  isStreaming: PropTypes.bool,
};
