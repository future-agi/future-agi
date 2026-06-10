import { Editor } from "@monaco-editor/react";
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { copyToClipboard } from "src/utils/utils";

const SDK_LANGUAGES = [
  { key: "python", label: "Python", icon: "hugeicons:python" },
  { key: "javascript", label: "JavaScript", icon: "proicons:javascript" },
  { key: "curl", label: "cURL", icon: "icon-park-outline:code" },
];

const editorOptions = {
  automaticLayout: true,
  domReadOnly: true,
  lineNumbers: "off",
  minimap: { enabled: false },
  readOnly: true,
  scrollBeyondLastLine: false,
  wordWrap: "on",
};

function removeEmptyMappingValues(mapping) {
  return Object.fromEntries(
    Object.entries(mapping || {}).filter(
      ([, value]) => value !== null && value !== undefined && String(value),
    ),
  );
}

function editorLanguage(languageKey) {
  if (languageKey === "curl") return "shell";
  return languageKey;
}

const EvalSdkCodeTab = ({
  templateId,
  templateName,
  model,
  mapping,
  errorLocalizerEnabled,
}) => {
  const { enqueueSnackbar } = useSnackbar();
  const [activeLanguage, setActiveLanguage] = useState("python");

  const requestMapping = useMemo(
    () => removeEmptyMappingValues(mapping),
    [mapping],
  );
  const mappingParam = useMemo(
    () => JSON.stringify(requestMapping),
    [requestMapping],
  );

  const { data, isFetching, error } = useQuery({
    queryKey: [
      "evalsSDKCode",
      templateId,
      model,
      mappingParam,
      errorLocalizerEnabled,
    ],
    queryFn: async ({ signal }) => {
      const response = await axios.get(endpoints.develop.eval.evalsSDKCode, {
        params: {
          template_id: templateId,
          model: model || undefined,
          mapping: mappingParam,
          error_localizer: Boolean(errorLocalizerEnabled),
        },
        signal,
      });
      return response?.data?.result || {};
    },
    enabled: Boolean(templateId),
    staleTime: 5 * 60 * 1000,
  });

  const activeSnippet = data?.[activeLanguage] || "";
  const activeLabel =
    SDK_LANGUAGES.find((language) => language.key === activeLanguage)?.label ||
    "SDK";

  const handleCopy = async () => {
    const copied = await copyToClipboard(activeSnippet);
    enqueueSnackbar(
      copied ? `${activeLabel} SDK code copied` : "Failed to copy SDK code",
      { variant: copied ? "success" : "error" },
    );
  };

  return (
    <Box
      sx={{
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <Box>
        <Typography variant="m2" fontWeight="fontWeightSemiBold">
          SDK Code
        </Typography>
        <Typography variant="s1" color="text.secondary">
          {templateName || "Evaluation"} snippets use placeholder credentials.
        </Typography>
      </Box>

      <Stack direction="row" spacing={1}>
        {SDK_LANGUAGES.map((language) => {
          const active = activeLanguage === language.key;
          return (
            <Button
              key={language.key}
              variant={active ? "contained" : "outlined"}
              size="small"
              aria-label={`Show ${language.label} Eval SDK Code`}
              startIcon={<Iconify icon={language.icon} width={16} />}
              onClick={() => setActiveLanguage(language.key)}
              sx={{ textTransform: "none" }}
            >
              {language.label}
            </Button>
          );
        })}
      </Stack>

      <Box
        sx={{
          position: "relative",
          flex: 1,
          minHeight: 360,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.neutral",
          overflow: "hidden",
        }}
      >
        <Tooltip title={`Copy ${activeLabel} SDK Code`} arrow>
          <span>
            <IconButton
              aria-label={`Copy ${activeLabel} Eval SDK Code`}
              size="small"
              onClick={handleCopy}
              disabled={!activeSnippet || isFetching}
              sx={{
                position: "absolute",
                top: 8,
                right: 8,
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

        {isFetching ? (
          <Box
            sx={{
              height: "100%",
              minHeight: 360,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <CircularProgress size={24} />
          </Box>
        ) : error ? (
          <Box sx={{ p: 2 }}>
            <Typography color="error" variant="s1">
              Failed to load SDK code
            </Typography>
          </Box>
        ) : (
          <Editor
            height="100%"
            language={editorLanguage(activeLanguage)}
            options={editorOptions}
            value={activeSnippet}
            onMount={(editor) => {
              editor.updateOptions({ readOnly: true, domReadOnly: true });
            }}
          />
        )}
      </Box>
    </Box>
  );
};

EvalSdkCodeTab.propTypes = {
  templateId: PropTypes.string,
  templateName: PropTypes.string,
  model: PropTypes.string,
  mapping: PropTypes.object,
  errorLocalizerEnabled: PropTypes.bool,
};

export default EvalSdkCodeTab;
