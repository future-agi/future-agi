import React, { useRef, useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography, Stack } from "@mui/material";

import ConversationCard from "./ConversationCard";
import SvgColor from "src/components/svg-color";
import { useTraceDrawerStore } from "../TracesDrawer/useTraceDrawerStore";
import { getVisiblePercentage } from "./common";
import { throttle } from "lodash";

const Conversation = ({ human, ai, activeSessionId }) => {
  const { viewType } = useTraceDrawerStore();
  const containerRef = useRef(null);
  const humanRef = useRef(null);
  const aiRef = useRef(null);
  const [showButton, setShowButton] = useState(null); // 'human' or 'ai' or null

  const Inputvalue = {
    cellValue: human,
  };

  const Outputvalue = {
    cellValue: ai,
  };

  const Inputcolumn = {
    headerName: "Human",
    dataType: typeof human,
  };

  const Outputcolumn = {
    headerName: "AI",
    dataType: typeof ai,
  };

  useEffect(() => {
    const container = containerRef?.current;
    if (!container) return;

    const checkVisibility = () => {
      const containerRect = container?.getBoundingClientRect();
      const humanRect = humanRef?.current?.getBoundingClientRect();
      const aiRect = aiRef?.current?.getBoundingClientRect();

      if (!humanRect || !aiRect) return;

      const humanVisiblePercent = getVisiblePercentage(
        humanRect,
        containerRect,
      );
      const aiVisiblePercent = getVisiblePercentage(aiRect, containerRect);

      const isHumanVisible = humanVisiblePercent >= 20;
      const isAiVisible = aiVisiblePercent >= 20;

      if (!isHumanVisible && isAiVisible) {
        setShowButton("human");
      } else if (!isAiVisible && isHumanVisible) {
        setShowButton("ai");
      } else if (!isHumanVisible && !isAiVisible) {
        const scrollTop = container.scrollTop;
        const midpoint = container.scrollHeight / 2;
        setShowButton(scrollTop < midpoint ? "ai" : "human");
      } else {
        setShowButton(null);
      }
    };

    const throttledCheckVisibility = throttle(checkVisibility, 300, {
      leading: true,
      trailing: true,
    });

    container.addEventListener("scroll", throttledCheckVisibility);
    throttledCheckVisibility();

    return () => {
      container.removeEventListener("scroll", throttledCheckVisibility);
      throttledCheckVisibility.cancel?.();
    };
  }, [viewType, activeSessionId]);

  const scrollToElement = (ref) => {
    const container = containerRef?.current;
    const element = ref?.current;
    if (!container || !element) return;

    const offset = ref === humanRef ? 50 : 20; // Different offset per element
    container.scrollTo({
      top: element.offsetTop - offset,
      behavior: "smooth",
    });
  };

  return (
    <Box
      sx={{
        position: "relative",
        height: "100%",
        minWidth: 0,
        backgroundColor: "background.paper",
      }}
    >
      <Box
        ref={containerRef}
        sx={{
          display: "flex",
          flexDirection: "column",
          rowGap: "50px",
          height: "100%",
          overflowY: "scroll",
          overflowX: "hidden",
          py: 4,
          minWidth: 0,
        }}
      >
        {/* Human Conversation */}
        <Box
          ref={humanRef}
          sx={{
            display: "flex",
            justifyContent: "flex-start",
            position: "relative",
            marginLeft: 10,
            minWidth: 0,
          }}
        >
          <Box
            sx={{
              height: "35px",
              width: "35px",
              borderRadius: "50%",
              position: "absolute",
              left: "-50px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <SvgColor
              src="/icons/runTest/ic_user.svg"
              sx={{
                height: "16px",
                width: "16px",
                bgcolor: "blue.500",
              }}
            />
          </Box>
          <Box
            sx={{
              position: "relative",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              backgroundColor: "background.paper",
              padding: "12px",
              maxWidth: "100%",
              minWidth: 0,
            }}
          >
            <Stack gap={0} mb={1}>
              <Typography
                sx={{
                  typography: "s1",
                  fontWeight: "fontWeightMedium",
                  color: "text.primary",
                }}
              >
                Human
              </Typography>
            </Stack>
            <ConversationCard value={Inputvalue} column={Inputcolumn} />
          </Box>
        </Box>

        {/* AI Conversation */}
        <Box
          ref={aiRef}
          sx={{
            display: "flex",
            justifyContent: "flex-end",
            position: "relative",
            marginRight: 10,
            minWidth: 0,
          }}
        >
          <Box
            sx={{
              height: "35px",
              width: "35px",
              borderRadius: "50%",
              bgcolor: "action.hover",
              position: "absolute",
              right: "-50px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <SvgColor
              src="/assets/icons/ic_bot.svg"
              sx={{
                height: "16px",
                width: "16px",
                bgcolor: "pink.500",
              }}
            />
          </Box>

          <Box
            sx={{
              position: "relative",
              border: "1px solid",
              borderColor: "primary.main",
              borderRadius: "4px",
              backgroundColor: "background.paper",
              padding: "12px",
              maxWidth: "100%",
              minWidth: 0,
            }}
          >
            <Stack gap={0} mb={1}>
              <Typography
                sx={{
                  typography: "s1",
                  fontWeight: "fontWeightMedium",
                  color: "text.primary",
                }}
              >
                AI
              </Typography>
            </Stack>
            <ConversationCard value={Outputvalue} column={Outputcolumn} />
          </Box>
        </Box>

        {showButton && (
          <Stack
            direction={"row"}
            alignItems={"center"}
            component={"button"}
            onClick={() =>
              scrollToElement(showButton === "human" ? humanRef : aiRef)
            }
            sx={{
              position: "absolute",
              top: showButton === "human" ? "unset" : 20,
              bottom: showButton === "ai" ? "unset" : 20,
              border: "none",
              right: 10,
              gap: 1.5,
              bgcolor: "transparent",
              cursor: "pointer",
            }}
          >
            <Box
              sx={{
                height: "27px",
                width: "27px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                bgcolor: "divider",
                borderRadius: "50%",
              }}
            >
              <SvgColor
                src="/assets/icons/custom/lucide--chevron-down.svg"
                sx={{
                  height: 20,
                  width: 20,
                  rotate: showButton === "human" ? "180deg" : "0deg",
                  bgcolor: "primary.main",
                }}
              />
            </Box>
            <Typography
              sx={{
                bgcolor: "transparent",
              }}
              color={"primary.main"}
              typography="s2_1"
              fontWeight={"fontWeightMedium"}
            >
              {showButton === "human" ? <>View Input</> : <>View Output</>}
            </Typography>
          </Stack>
        )}
      </Box>
    </Box>
  );
};

Conversation.propTypes = {
  human: PropTypes.any,
  ai: PropTypes.any,
  activeSessionId: PropTypes.string,
};

export default Conversation;
