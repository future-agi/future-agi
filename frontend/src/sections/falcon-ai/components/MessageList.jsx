import React, { useRef, useEffect } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import useFalconStore from "../store/useFalconStore";
import UserMessage from "./UserMessage";
import AssistantMessage from "./AssistantMessage";
import QuickActions from "./QuickActions";

export default function MessageList({ onQuickAction, onFeedback }) {
  const messages = useFalconStore((s) => s.messages);
  const isStreaming = useFalconStore((s) => s.isStreaming);
  const scrollRef = useRef(null);
  const isNearBottomRef = useRef(true);

  // Track whether user is near the bottom
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    isNearBottomRef.current = scrollHeight - scrollTop - clientHeight < 120;
  };

  // Auto-scroll only when near bottom or streaming
  useEffect(() => {
    if (scrollRef.current && (isNearBottomRef.current || isStreaming)) {
      scrollRef.current.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: isStreaming ? "auto" : "smooth",
      });
    }
  }, [messages, isStreaming]);

  if (messages.length === 0) {
    return (
      <Box
        ref={scrollRef}
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          px: 3,
          py: 6,
        }}
      >
        {/* Icon */}
        <Box
          sx={{
            width: 56,
            height: 56,
            borderRadius: "12px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            mb: 3,
            bgcolor: "background.neutral",
          }}
        >
          <Iconify
            icon="solar:stars-minimalistic-linear"
            width={28}
            sx={{
              color: "text.secondary",
            }}
          />
        </Box>

        <Typography
          variant="h5"
          sx={{
            fontWeight: 600,
            mb: 1,
            color: "text.primary",
            letterSpacing: "-0.02em",
          }}
        >
          How can I help?
        </Typography>

        <Typography
          variant="body2"
          sx={{
            mb: 4,
            textAlign: "center",
            color: "text.disabled",
            fontSize: 13,
            maxWidth: 360,
            lineHeight: 1.6,
          }}
        >
          Ask about your data, evaluations, experiments, traces, and more.
        </Typography>

        <QuickActions onAction={onQuickAction} />
      </Box>
    );
  }

  return (
    <Box
      ref={scrollRef}
      onScroll={handleScroll}
      sx={{
        flex: 1,
        overflow: "auto",
        py: 2,
        px: 3,
      }}
    >
      {messages.map((msg) => {
        if (msg.role === "user") {
          return <UserMessage key={msg.id} message={msg} />;
        }
        return (
          <AssistantMessage
            key={msg.id}
            message={msg}
            onFeedback={onFeedback}
          />
        );
      })}
    </Box>
  );
}

MessageList.propTypes = {
  onQuickAction: PropTypes.func,
  onFeedback: PropTypes.func,
};
