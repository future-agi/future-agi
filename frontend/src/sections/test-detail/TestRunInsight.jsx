import { Box } from "@mui/material";
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import TestRunInsightCard from "./TestRunInsightCard";
import useKpis from "src/hooks/useKpis";
import { useParams } from "react-router";
import { camelCaseToTitleCase } from "src/utils/utils";
import { extractKpis } from "./common";
import TestRunInsightSkeletonCard from "./TestRunInsightSkeletonCard";
import { AGENT_TYPES } from "src/sections/agents/constants";

const TestRunInsight = () => {
  const { executionId } = useParams();
  const { data: kpis, isPending } = useKpis(executionId);

  const sectionRef = useRef();
  const isHeightManipulated = useRef(false);

  const [isDragging, setIsDragging] = useState(false);
  const initialHeight = useRef(0);

  const updateHeight = useCallback(() => {
    if (sectionRef.current && !isHeightManipulated.current) {
      // adding 2 to accommodate for the border
      initialHeight.current = sectionRef.current.scrollHeight;
    }
  }, []);

  const handleMouseMove = useCallback(
    (e) => {
      if (!isDragging) return;

      let newHeight =
        e.clientY - sectionRef.current.getBoundingClientRect().top;
      if (newHeight < 0) {
        newHeight = 0;
      }
      if (newHeight > initialHeight.current) {
        newHeight = initialHeight.current;
      }
    },
    [isDragging],
  );

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
  }, []);

  useEffect(() => {
    const handleResize = () => {
      updateHeight();
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, [updateHeight]);

  useEffect(() => {
    if (isDragging) {
      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
    } else {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    }

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
  }, [isDragging, handleMouseMove, handleMouseUp]);

  useLayoutEffect(() => {
    updateHeight();
  }, [isPending, updateHeight]);

  const { systemMetrics, evalMetrics } = useMemo(() => {
    return extractKpis(kpis, kpis?.agent_type);
  }, [kpis]);

  const isVoice = kpis?.agent_type === AGENT_TYPES.VOICE;

  const renderMetrics = () => {
    if (isPending) {
      return (
        <>
          {Array.from({ length: 10 }).map((_, index) => (
            <TestRunInsightSkeletonCard key={index} />
          ))}
        </>
      );
    }

    return (
      <>
        <TestRunInsightCard
          title="Total calls"
          tooltipTitle="Number of calls scheduled for this test"
          value={systemMetrics?.totalCalls}
        />
        <TestRunInsightCard
          title="Calls attempted"
          value={systemMetrics?.callsAttempted}
          tooltipTitle="Number of calls processed during the test run"
        />
        <TestRunInsightCard
          title="Calls Connected (%)"
          value={systemMetrics?.connectedCalls}
          tooltipTitle="% of calls that successfully established an end-to-end connection"
          secondaryText={`(${systemMetrics?.percentageAttempted}% of attempted calls)`}
        />
        <TestRunInsightCard
          title="Average CSAT"
          value={systemMetrics?.avgScore}
          maxValue={10}
          highlight
          secondaryText={`(Connected Calls: ${systemMetrics?.connectedCalls})`}
          tooltipTitle="Mean customer satisfaction score across all completed calls"
        />
        {isVoice && (
          <TestRunInsightCard
            title="Avg. Agent Latency (ms)"
            value={systemMetrics?.avgAgentLatency}
            suffix={"ms"}
            tooltipTitle="Average time (ms) for the agent to start a response after the user finishes speaking"
          />
        )}
        {/* <TestRunInsightCard
          title="Sim Interrupts"
          value={systemMetrics?.avgUserInterruptionCount}
        /> */}
        {isVoice && (
          <TestRunInsightCard
            title="Agent WPM"
            value={Math.round(systemMetrics?.avgBotWpm)}
            tooltipTitle="Agent's average speaking rate, measuring conversational pace"
          />
        )}
        {isVoice && (
          <TestRunInsightCard
            title="Talk Ratio (A/S%)"
            value={`${Math.round(systemMetrics?.agentTalkPercentage)}%/${Math.round(systemMetrics?.customerTalkPercentage)}%`}
            tooltipTitle="Ratio of time the Agent (A) spoke versus the simulator (S) spoke"
          />
        )}
        {/* <TestRunInsightCard
          title="Agent Interrupts"
          value={systemMetrics?.avgAiInterruptionCount}
        /> */}
        {isVoice && (
          <TestRunInsightCard
            title="Agent Stop Latency (ms)"
            value={systemMetrics?.avgStopTimeAfterInterruption}
            suffix={"ms"}
            tooltipTitle=" Average time (ms) the agent takes to react to, and stop speaking, when interrupted by the user"
          />
        )}

        {Object.entries(evalMetrics || {}).map(([key, value]) => (
          <TestRunInsightCard
            key={key}
            title={camelCaseToTitleCase(key)}
            value={value}
            maxValue={100}
            suffix={"%"}
            highlight
          />
        ))}
      </>
    );
  };

  return (
    <Box
      sx={{
        position: "relative",
      }}
    >
      <Box
        sx={
          {
            // height: `${sectionHeight}px`,
            // transition: isDragging ? "none" : "height 0.3s ease-in-out",
          }
        }
      >
        <Box
          sx={{
            padding: 2,
            backgroundColor: "background.default",
            gap: 2,
            display: "grid",
            gridTemplateColumns: {
              xs: "repeat(1, 1fr)",
              sm: "repeat(2, 1fr)",
              md: "repeat(3, 1fr)",
              lg: "repeat(4, 1fr)",
              xl: "repeat(6, 1fr)",
            },
          }}
          ref={sectionRef}
        >
          {renderMetrics()}
        </Box>
      </Box>
    </Box>
  );
};

export default TestRunInsight;
