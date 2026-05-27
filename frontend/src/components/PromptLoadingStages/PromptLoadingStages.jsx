import { Box, Typography } from "@mui/material";
import React, { useEffect, useState, useRef } from "react";
import SvgColor from "../svg-color";
import PropTypes from "prop-types";
import styles from "./PromptLoadingStages.module.css";

export default function PromptLoadingStages({ stage, stages, onFinish }) {
  const stageKeys = Object.keys(stages);
  const defaultStageKey = stageKeys[0];
  const [currentStage, setCurrentStage] = useState(stage || defaultStageKey);
  const [displayedText, setDisplayedText] = useState("");
  const [mode, setMode] = useState("typing"); // "typing" or "deleting"
  const [isTypingDone, setIsTypingDone] = useState(false);
  const intervalRef = useRef(null);
  const stageQueue = useRef([]);

  const { icon, text } = stages[currentStage] || stages[defaultStageKey];

  // Queue new incoming stages
  useEffect(() => {
    if (stage !== currentStage && !stageQueue.current.includes(stage)) {
      stageQueue.current.push(stage);
    }
  }, [stage, currentStage]);

  // Typing/deleting animation
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    let currentIndex = mode === "deleting" ? displayedText.length : 0;
    setIsTypingDone(false);

    intervalRef.current = setInterval(
      () => {
        setDisplayedText((prev) => {
          if (mode === "deleting") {
            const updated = prev.slice(0, -1);
            if (updated.length === 0) {
              clearInterval(intervalRef.current);
            }
            return updated;
          } else {
            const nextChar = text[currentIndex];
            currentIndex++;
            const updated = prev + (nextChar || "");

            if (currentIndex >= text.length) {
              clearInterval(intervalRef.current);
              setIsTypingDone(true);
            }

            return updated;
          }
        });
      },
      mode === "deleting" ? 7 : 40,
    );

    return () => clearInterval(intervalRef.current);
  }, [text, mode]);

  // After typing is done, wait and start deleting
  useEffect(() => {
    if (isTypingDone && stageQueue.current.length > 0) {
      const timeout = setTimeout(() => {
        setMode("deleting");
      }, 1000); // Wait 1s before deleting
      return () => clearTimeout(timeout);
    }
  }, [isTypingDone, stage]);

  // Call onFinish when final stage is done
  useEffect(() => {
    const isLastStage =
      currentStage === stageKeys[stageKeys.length - 1] &&
      isTypingDone &&
      stageQueue.current.length === 0;

    if (isLastStage && typeof onFinish === "function") {
      onFinish();
    }
  }, [currentStage, isTypingDone]);

  // After deletion is done (text is empty), move to next stage if any
  useEffect(() => {
    if (mode === "deleting" && displayedText === "") {
      const nextStage = stageQueue.current.shift();
      if (nextStage) {
        setCurrentStage(nextStage);
        setMode("typing");
      }
    }
  }, [displayedText, mode]);

  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "center", gap: 1 }}
      className={styles["prompt-loading-container"]}
    >
      <SvgColor
        src={icon}
        sx={{ width: "20px", height: "20px", flexShrink: 0 }}
        className={styles["final-icon-loading"]}
      />
      <Typography
        variant="s1"
        sx={{ display: "flex", alignItems: "center", gap: 1 }}
        className={styles["final-prompt-loading"]}
      >
        {displayedText}
      </Typography>
    </Box>
  );
}

PromptLoadingStages.propTypes = {
  stage: PropTypes.string.isRequired,
  stages: PropTypes.objectOf(
    PropTypes.shape({
      icon: PropTypes.string.isRequired,
      text: PropTypes.string.isRequired,
    }),
  ).isRequired,
  onFinish: PropTypes.func,
};
