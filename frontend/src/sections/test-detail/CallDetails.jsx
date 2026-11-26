import { Box } from "@mui/material";
import React, { useCallback, useEffect, useState } from "react";
import TestRunDetailBar from "./TestRunDetailBar";
import TestRunDetailGrid from "./TestRunDetailGrid";
import PerformanceMetrics from "./PerformanceMetrics/PerformanceMetrics";
import LiveCallMonitor from "src/components/live-call-monitor/LiveCallMonitor";

const CallDetails = () => {
  const [liveCallId, setLiveCallId] = useState(null);

  const handleLiveCallEvent = useCallback((e) => {
    setLiveCallId(e.detail?.callId || null);
  }, []);

  useEffect(() => {
    window.addEventListener("open-live-call-monitor", handleLiveCallEvent);
    return () =>
      window.removeEventListener("open-live-call-monitor", handleLiveCallEvent);
  }, [handleLiveCallEvent]);

  return (
    <Box
      sx={{
        flex: 1,
        paddingX: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        paddingBottom: 2,
        backgroundColor: "background.paper",
        paddingTop: 1,
      }}
    >
      <PerformanceMetrics />
      <TestRunDetailBar />
      <TestRunDetailGrid />
      <LiveCallMonitor
        callId={liveCallId}
        open={Boolean(liveCallId)}
        onClose={() => setLiveCallId(null)}
      />
    </Box>
  );
};

export default CallDetails;
