import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const TranscriptTooltip = ({ transcript }) => {
  if (!transcript?.length) return "No transcript";
  const lines = transcript.slice(0, 10);
  return (
    <Box sx={{ p: 0.5, maxWidth: 350, maxHeight: 300, overflow: "auto" }}>
      <Typography sx={{ fontSize: 12, fontWeight: 600, mb: 0.5 }}>
        Transcript
      </Typography>
      {lines.map((t, i) => (
        <Box key={i} sx={{ mb: 0.5 }}>
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: t.role === "user" ? "info.main" : "secondary.main",
              textTransform: "none",
            }}
          >
            {t.role}
          </Typography>
          <Typography sx={{ fontSize: 12, color: "inherit", lineHeight: 1.3 }}>
            {t.content}
          </Typography>
        </Box>
      ))}
      {transcript.length > 10 && (
        <Typography sx={{ fontSize: 12, color: "text.disabled", mt: 0.5 }}>
          +{transcript.length - 10} more messages
        </Typography>
      )}
    </Box>
  );
};

TranscriptTooltip.propTypes = { transcript: PropTypes.array };

const TranscriptPreviewCell = (params) => {
  const data = params?.data;
  const transcript = data?.transcript;
  const available = data?.transcriptAvailable ?? data?.transcript_available;

  if (!available || !transcript?.length) {
    return (
      <Typography
        variant="body2"
        sx={{ fontSize: 13, color: "text.disabled", px: 2 }}
      >
        -
      </Typography>
    );
  }

  // First user message as preview
  const firstMsg = transcript.find((t) => t.role === "user") || transcript[0];
  const preview = firstMsg?.content || "";
  const truncated =
    preview.length > 50 ? preview.slice(0, 47) + "..." : preview;

  return (
    <Tooltip
      title={<TranscriptTooltip transcript={transcript} />}
      arrow
      placement="bottom-start"
      slotProps={{
        tooltip: { sx: { maxWidth: 400, bgcolor: "grey.900", p: 1 } },
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          px: 2,
          height: "100%",
          cursor: "default",
        }}
      >
        <Iconify
          icon="solar:chat-round-line-linear"
          width={14}
          color="text.disabled"
        />
        <Typography
          variant="body2"
          noWrap
          sx={{ fontSize: 13, color: "text.secondary" }}
        >
          {truncated}
        </Typography>
      </Box>
    </Tooltip>
  );
};

TranscriptPreviewCell.propTypes = { data: PropTypes.object };

export default React.memo(TranscriptPreviewCell);
