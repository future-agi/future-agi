import React, { useMemo, useState } from "react";
import {
  Box,
  Typography,
  useTheme,
  CircularProgress,
  IconButton,
} from "@mui/material";
import HelperText from "../Common/HelperText";
import { CustomTab, CustomTabs, TabWrapper } from "./AddRowDataStyle";
import { ShowComponent } from "src/components/show";
import { FormCodeEditor } from "src/components/form-code-editor";
import { useForm } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { copyToClipboard as copyTextToClipboard } from "src/utils/utils";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const defaultValues = {
  Pcode: "",
  Tcode: "",
  CUcode: "",
};

const editorOptions = {
  selectOnLineNumbers: true,
  roundedSelection: false,
  readOnly: true,
  cursorStyle: "line",
  automaticLayout: true,
  minimap: {
    enabled: false,
  },
  wordWrap: "on",
  theme: "light",
};

const tabOptions = [
  { label: "Python", value: "python", disabled: false },
  { label: "Typescript", value: "typescript", disabled: false },
  { label: "Curl", value: "curl", disabled: false },
];

const fetchRowSDK = async (dataset) => {
  const response = await axios.post(endpoints.row.addRowSdk, {
    dataset_id: dataset,
  });
  return response.data;
};

const AddRowData = ({ dataset }) => {
  const theme = useTheme();
  const { control, setValue } = useForm({
    defaultValues,
  });
  const [currentTab, setCurrentTab] = useState("python");

  const copyToClipboard = (text, type) => {
    copyTextToClipboard(text).then((ok) => {
      if (ok) {
        enqueueSnackbar(`${type} copied to clipboard!`, { variant: "success" });
      } else {
        enqueueSnackbar(`Failed to copy ${type}`, { variant: "error" });
      }
    });
  };

  const { data, isLoading } = useQuery({
    queryKey: ["add-row-sdk", dataset], // Unique key for this query
    queryFn: () => fetchRowSDK(dataset),
    select: (data) => {
      return data;
    },
  });
  useMemo(() => {
    setValue("Pcode", data?.result?.code?.python_add_col || "");
    setValue("Tcode", data?.result?.code?.typescript_add_col || "");
    setValue("CUcode", data?.result?.code?.curl_add_col || "");
  }, [data, setValue]);

  if (isLoading) {
    return (
      <Box height="100vh" overflow="auto" py="50px" px="15px">
        <Box mx="auto" maxWidth="875px" width="100%">
          <Box display={"flex"} flexDirection={"column"} gap={0.5}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <Box display="flex" alignItems="center" gap={1}>
                <CircularProgress size={16} thickness={4} />
                <Typography
                  variant="m2"
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                >
                  Your data will automatically start showing once added.
                </Typography>
              </Box>
            </Box>
            <Box>
              <HelperText text="Datasets can be used to for evals, prompt experimentation, or fine-tuning." />
            </Box>
          </Box>
          <Box
            display="flex"
            justifyContent="center"
            alignItems="center"
            minHeight="400px"
          >
            <CircularProgress size={40} />
          </Box>
        </Box>
      </Box>
    );
  }

  return (
    <Box height="100vh" overflow="auto" py="50px" px="15px">
      <Box mx="auto" maxWidth="875px" width="100%">
        <Box display={"flex"} flexDirection={"column"} gap={0.5}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Box display="flex" alignItems="center" gap={1}>
              <CircularProgress size={16} thickness={4} />
              <Typography
                variant="m2"
                fontWeight={"fontWeightMedium"}
                color="text.primary"
              >
                Your data will automatically start showing once added.
              </Typography>
            </Box>
          </Box>
          <Box>
            <HelperText text="Datasets can be used to for evals, prompt experimentation, or fine-tuning." />
          </Box>
        </Box>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            listStyle: "none",
            margin: "20px 0",
          }}
        >
          <Box display="flex" alignItems="center">
            <Typography
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              variant="s1"
              component="span"
              width="141px"
            >
              Dataset Name:
            </Typography>
            <Box display="flex" alignItems="center" gap={1}>
              <Typography
                bgcolor="background.neutral"
                borderRadius="8px"
                variant="s1"
                fontWeight={"fontWeightRegular"}
                p="5px 12px"
                color="black"
              >
                {data?.result?.dataset?.name}
              </Typography>
              <IconButton
                size="small"
                onClick={() =>
                  copyToClipboard(data?.result?.dataset?.name, "Dataset Name")
                }
                sx={{ color: "text.secondary" }}
              >
                <Iconify icon="eva:copy-outline" width={16} height={16} />
              </IconButton>
            </Box>
          </Box>
          <Box display="flex" alignItems="center">
            <Typography
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              variant="s1"
              component="span"
              width="141px"
            >
              Dataset ID:
            </Typography>
            <Box display="flex" alignItems="center" gap={1}>
              <Typography
                bgcolor="background.neutral"
                borderRadius="8px"
                variant="s1"
                fontWeight={"fontWeightRegular"}
                p="5px 12px"
                color="black"
                sx={{
                  fontFamily: "monospace",
                  fontSize: "0.875rem",
                }}
              >
                {data?.result?.dataset?.id}
              </Typography>
              <IconButton
                size="small"
                onClick={() =>
                  copyToClipboard(data?.result?.dataset?.id, "Dataset ID")
                }
                sx={{ color: "text.secondary" }}
              >
                <Iconify icon="eva:copy-outline" width={16} height={16} />
              </IconButton>
            </Box>
          </Box>
          <Box display="flex" alignItems="center">
            <Typography
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              variant="s1"
              component="span"
              width="141px"
            >
              API Key:
            </Typography>
            <Box display="flex" alignItems="center" gap={1}>
              <Typography
                bgcolor="background.neutral"
                borderRadius="8px"
                variant="s1"
                fontWeight={"fontWeightRegular"}
                p="5px 12px"
                color="black"
              >
                {data?.result?.apiKeys?.apiKey}
              </Typography>
              <IconButton
                size="small"
                onClick={() =>
                  copyToClipboard(data?.result?.apiKeys?.apiKey, "API Key")
                }
                sx={{ color: "text.secondary" }}
              >
                <Iconify icon="eva:copy-outline" width={16} height={16} />
              </IconButton>
            </Box>
          </Box>
          <Box display="flex" alignItems="center">
            <Typography
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              variant="s1"
              component="span"
              width="141px"
            >
              Secret Key:
            </Typography>
            <Box display="flex" alignItems="center" gap={1}>
              <Typography
                bgcolor="background.neutral"
                borderRadius="8px"
                variant="s1"
                fontWeight={"fontWeightRegular"}
                p="5px 12px"
                color="black"
              >
                {data?.result?.apiKeys?.secretKey}
              </Typography>
              <IconButton
                size="small"
                onClick={() =>
                  copyToClipboard(
                    data?.result?.apiKeys?.secretKey,
                    "Secret Key",
                  )
                }
                sx={{ color: "text.secondary" }}
              >
                <Iconify icon="eva:copy-outline" width={16} height={16} />
              </IconButton>
            </Box>
          </Box>
        </Box>
        <TabWrapper>
          <CustomTabs
            textColor="primary"
            value={currentTab}
            onChange={(e, value) => setCurrentTab(value)}
            TabIndicatorProps={{
              style: {
                backgroundColor: theme.palette.primary.main,
                opacity: 0.08,
                height: "100%",
                borderRadius: "8px",
              },
            }}
          >
            {tabOptions.map((tab) => (
              <CustomTab
                key={tab.value}
                label={tab.label}
                value={tab.value}
                disabled={tab.disabled}
              />
            ))}
          </CustomTabs>
        </TabWrapper>
        <Box
          bgcolor="background.neutral"
          borderRadius="8px"
          border="1px solid var(--border-default)"
          p={2}
        >
          <ShowComponent condition={currentTab === "python"}>
            <FormCodeEditor
              height="300px"
              defaultLanguage="python"
              control={control}
              fieldName="Pcode"
              helperText=""
              options={editorOptions}
            />
          </ShowComponent>
          <ShowComponent condition={currentTab === "typescript"}>
            <FormCodeEditor
              height="300px"
              defaultLanguage="typescript"
              control={control}
              fieldName="Tcode"
              helperText=""
              options={editorOptions}
            />
          </ShowComponent>
          <ShowComponent condition={currentTab === "curl"}>
            <FormCodeEditor
              height="300px"
              defaultLanguage="bash"
              control={control}
              fieldName="CUcode"
              helperText=""
              options={editorOptions}
            />
          </ShowComponent>
        </Box>
      </Box>
    </Box>
  );
};

AddRowData.propTypes = {
  dataset: PropTypes.string,
};

export default AddRowData;
