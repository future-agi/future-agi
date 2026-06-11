import { Box, IconButton, Skeleton } from "@mui/material";
import React, { useState } from "react";
import CustomAgentTabs from "src/sections/agents/CustomAgentTabs";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import SvgColor from "src/components/svg-color";
import { primaryFont } from "src/theme/typography";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";

const MetricEmptyState = () => {
  const [activeTab, setActiveTab] = useState("python");
  const tabs = [
    { label: "Python", value: "python" },
    { label: "TypeScript", value: "typescript" },
  ];

  const { data, isLoading } = useQuery({
    queryKey: ["metric-empty-screen-snippets"],
    queryFn: () =>
      axios.get(endpoints.develop.runPrompt.promptMetricEmptyScreen()),
    select: (res) => res?.data?.result,
  });

  const onCopy = () => {
    copyToClipboard(data?.[activeTab] || "");
    enqueueSnackbar("Copied to clipboard", { variant: "success" });
  };

  return (
    <EmptyLayout
      title="Add prompt to begin monitoring performance indicators"
      link="https://docs.futureagi.com/docs/prompt/features/linked-traces"
      linkText="Check docs"
      icon="/assets/icons/agent/performance_analytics.svg"
      sx={{ mt: 8 }}
      action={
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            padding: "10px",
            backgroundColor: "background.paper",
            width: "32vw",
          }}
        >
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <CustomAgentTabs
              value={activeTab}
              onChange={(_, newValue) => setActiveTab(newValue)}
              tabs={tabs}
            />
            <IconButton
              sx={{
                p: 0.5,
                borderRadius: "50%",
                zIndex: 1,
                mb: 1,
              }}
              onClick={onCopy}
            >
              <SvgColor
                src="/assets/icons/ic_copy.svg"
                alt="Copy"
                sx={{ width: "18px", height: "18px" }}
              />
            </IconButton>
          </Box>
          {isLoading ? (
            <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
              <Skeleton variant="rectangular" height={14} width="90%" />
              <Skeleton variant="rectangular" height={14} width="80%" />
              <Skeleton variant="rectangular" height={14} width="95%" />
              <Skeleton variant="rectangular" height={14} width="70%" />
            </Box>
          ) : (
            <Box
              sx={{
                flex: 1,
                maxHeight: "42vh",
                overflow: "auto",
                textAlign: "left",
                "&::-webkit-scrollbar": {
                  width: "6px",
                  height: "6px",
                },
                "&::-webkit-scrollbar-thumb": {
                  backgroundColor: "var(--scrollbar-thumb)",
                  borderRadius: "10px",
                },
                "&::-webkit-scrollbar-thumb:hover": {
                  backgroundColor: "var(--scrollbar-thumb)",
                },
                "&::-webkit-scrollbar-track": {
                  backgroundColor: "transparent",
                },
                scrollbarWidth: "thin", // Firefox
                scrollbarColor: "var(--scrollbar-thumb) transparent",
              }}
            >
              <SyntaxHighlighter
                useInlineStyles={false}
                language={activeTab}
                codeTagProps={{
                  style: {
                    color: "var(--text-primary)",
                    textShadow: "none",
                    background: "transparent",
                  },
                }}
                customStyle={{
                  fontSize: "12px",
                  fontFamily: primaryFont,
                  margin: "0px",
                  overflowX: "auto",
                  backgroundColor: "transparent",
                  color: "var(--text-primary)",
                  textShadow: "none",
                  overflowY: "hidden",
                  maxWidth: "100%",
                  minWidth: 0,
                  whiteSpace: "pre",
                  wordWrap: "normal",
                  wordBreak: "normal",
                  boxSizing: "border-box",
                }}
              >
                {data?.[activeTab] || ""}
              </SyntaxHighlighter>
            </Box>
          )}
        </Box>
      }
    />
  );
};

export default MetricEmptyState;
