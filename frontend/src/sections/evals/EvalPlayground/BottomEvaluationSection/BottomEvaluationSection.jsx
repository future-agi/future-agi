import { Box, IconButton, Tooltip, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import LogsTabGrid from "../../EvalDetails/EvalsLog/LogsTabGrid";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import { Editor } from "@monaco-editor/react";
import { useSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";

const editorOptions = {
  selectOnLineNumbers: true,
  roundedSelection: false,
  readOnly: false,
  cursorStyle: "line",
  automaticLayout: true,
  minimap: { enabled: false },
  wordWrap: "on",
  lineNumbers: "off",
};

const buttonList = [
  {
    id: 1,
    title: "Python",
    icon: "hugeicons:python",
    iconType: "iconify",
  },
  {
    id: 2,
    title: "JavaScript",
    icon: "proicons:javascript",
    iconType: "iconify",
  },
  {
    id: 3,
    title: "cURL",
    icon: "icon-park-outline:code",
    iconType: "iconify",
  },
];

const BottomEvaluationSection = ({
  tableRef,
  evaluation,
  selectedData,
  setInitialLeftWidth,
}) => {
  const [activeTab, setActiveTab] = React.useState("");
  const editorRef = React.useRef(null);
  const { enqueueSnackbar } = useSnackbar();

  const { data, isPending, isLoading, isRefetching } = useQuery({
    queryFn: ({ signal }) => {
      const params = {
        template_id: evaluation?.id,
        ...selectedData,
      };
      if (params.mapping && typeof params.mapping === "object") {
        const mapping = { ...params.mapping };
        delete mapping.model;
        params.mapping = JSON.stringify(mapping);
      } else if (!params.mapping) {
        params.mapping = JSON.stringify({});
      }
      if (!params.model) delete params.model;
      return axios.get(endpoints.develop.eval.evalsSDKCode, {
        params,
        signal,
      });
    },
    queryKey: ["evalsSDKCode", evaluation?.id, selectedData],
    enabled: Boolean(activeTab && evaluation?.id),
    select: (data) => {
      const result = data?.data?.result;
      if (data.data.result) {
        return {
          Python: result.python,
          JavaScript: result.javascript,
          cURL: result.curl,
        };
      } else {
        return { Python: "", JavaScript: "", cURL: "" };
      }
    },
    staleTime: 5 * 60 * 1000, // 5 minutes
    gcTime: 10 * 60 * 1000, // 10 minutes (formerly cacheTime)
  });

  const handleEditorMount = (editor, monaco) => {
    editorRef.current = editor;
    const isDark = document.body.getAttribute("data-theme") === "dark";
    monaco.editor.defineTheme("myCustomTheme", {
      base: isDark ? "vs-dark" : "vs",
      inherit: true,
      rules: [],
      colors: { "editor.background": isDark ? "#111111" : "#F8F8F8" },
    });

    monaco.editor.setTheme("myCustomTheme");
    editor.updateOptions({
      readOnly: true,
      domReadOnly: true,
    });
  };

  const activeSnippet = data?.[activeTab] || "";
  const handleCopy = async () => {
    const copied = await copyToClipboard(activeSnippet);
    enqueueSnackbar(
      copied ? `${activeTab} SDK code copied` : "Failed to copy SDK code",
      { variant: copied ? "success" : "error" },
    );
  };

  return (
    <Box
      sx={{
        paddingTop: 2,
        display: "flex",
        justifyContent: "center",
        flexDirection: "column",
        height: "100%",
      }}
    >
      <Box display={"flex"} gap={2}>
        {buttonList.map((item) => {
          const active = activeTab === item.title;
          return (
            <IconButton
              key={item.id}
              onClick={() => {
                setActiveTab(item.title);
                setInitialLeftWidth(30);
              }}
              sx={{
                borderRadius: "4px",
                backgroundColor: active ? "action.hover" : "background.paper",
                border: "1px solid",
                borderColor: "divider",
                gap: (theme) => theme.spacing(0.5),
                height: "30px",
              }}
            >
              <Iconify
                // @ts-ignore
                icon={item.icon}
                width="16px"
                height="16px"
                sx={{
                  cursor: "pointer",
                  color: active ? "primary.main" : "text.primary",
                }}
              />
              <Typography
                typography="s2"
                fontWeight={"fontWeightSemiBold"}
                color={active ? "primary.main" : "text.primary"}
              >
                {item.title}
              </Typography>
            </IconButton>
          );
        })}
      </Box>
      {activeTab && !(isPending || isLoading || isRefetching) && (
        <Box sx={{ width: "100%", mt: 2 }}>
          <Box
            sx={{
              paddingX: "10px",
              paddingY: "10px",
              backgroundColor: "background.neutral",
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
              position: "relative",
            }}
          >
            <Tooltip title={`Copy ${activeTab} SDK Code`} arrow>
              <span>
                <IconButton
                  aria-label={`Copy ${activeTab} Eval SDK Code`}
                  size="small"
                  onClick={handleCopy}
                  disabled={!activeSnippet}
                  sx={{
                    position: "absolute",
                    top: "6px",
                    right: "6px",
                    zIndex: 1,
                    bgcolor: "background.paper",
                    border: "1px solid",
                    borderColor: "divider",
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <Iconify icon="basil:copy-outline" width={16} />
                </IconButton>
              </span>
            </Tooltip>
            <Box>
              <Editor
                ref={editorRef}
                defaultLanguage={activeTab.toLowerCase()}
                height="300px"
                options={editorOptions}
                value={activeSnippet}
                onMount={handleEditorMount}
              />
            </Box>
            {/* <Box
              component={"pre"}
              sx={{
                margin: 0,
                padding: 0,
                overflow: "auto",
                color: "text.disabled",
                fontSize: (theme) => theme.typography.s1.fontSize,
                lineHeight: (theme) => theme.typography.s1.lineHeight,
                fontWeight: (theme) => theme.typography.fontWeightRegular,
              }}
            >
              {data[activeTab]}
            </Box> */}
          </Box>
        </Box>
      )}
      <Box
        sx={{ width: "100%", height: "calc(100vh - 50%)", minHeight: "200px" }}
      >
        {evaluation?.id && (
          <LogsTabGrid
            tableRef={tableRef}
            templateId={evaluation?.id}
            isEvalPlayGround
          />
        )}
      </Box>
    </Box>
  );
};

export default BottomEvaluationSection;

BottomEvaluationSection.propTypes = {
  tableRef: PropTypes.object,
  evaluation: PropTypes.object,
  selectedData: PropTypes.object,
  setInitialLeftWidth: PropTypes.func,
};
