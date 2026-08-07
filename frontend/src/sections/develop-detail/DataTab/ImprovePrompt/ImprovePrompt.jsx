import { zodResolver } from "@hookform/resolvers/zod";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  InputAdornment,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { improvePromptValidationSchema } from "./validation";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import { FormSelectField } from "src/components/FormSelectField";
import { PromptSection } from "src/components/prompt-section";
import { copyToClipboard } from "src/utils/utils";
import {
  handleAuthFailClose,
  handleWsErrorFrame,
  runPromptOverSocket,
} from "src/sections/workbench/createPrompt/common";
import { enqueueSnackbar } from "notistack";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Markdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useImprovePromptStore, useRunPromptStoreShallow } from "../../states";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import { useParams } from "react-router";
import { usePromptStreamUrl } from "src/sections/workbench/createPrompt/hooks/usePromptStreamUrl";

const ImprovePrompt = () => {
  const { improvePrompt: data, setImprovePrompt } = useImprovePromptStore();
  const { dataset } = useParams();
  const onClose = () => {
    setImprovePrompt(null);
  };
  const setOpenRunPrompt = useRunPromptStoreShallow((s) => s.setOpenRunPrompt);
  const allColumns = useDatasetColumnConfig(dataset);
  return (
    <Drawer
      anchor="right"
      open={Boolean(data)}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <ImprovePromptForm
        onClose={onClose}
        data={data}
        allColumns={allColumns}
        setConfigureRunPrompt={setOpenRunPrompt}
      />
    </Drawer>
  );
};

export default ImprovePrompt;

const ImprovePromptForm = ({ ...all }) => {
  const [isGenerateVisible, setIsGenerateVisible] = useState(false);
  const [selectedPrompt, setSelectedPrompt] = useState(null);
  const [streamedText, setStreamedText] = useState("");
  return (
    <Box sx={{ display: "flex", height: "100vh" }}>
      <LeftSection
        {...all}
        selectedPrompt={selectedPrompt}
        setSelectedPrompt={setSelectedPrompt}
        setIsGenerateVisible={setIsGenerateVisible}
        setStreamedTextParent={setStreamedText}
      />
      <div style={{ borderRight: "2px solid var(--border-light)" }} />
      <RightSection
        {...all}
        selectedPrompt={selectedPrompt}
        isGenerateVisible={isGenerateVisible}
        streamedText={streamedText}
      />
    </Box>
  );
};

const LeftSection = ({
  onClose,
  data,
  allColumns,
  selectedPrompt,
  setSelectedPrompt,
  setIsGenerateVisible,
  setStreamedTextParent,
}) => {
  const [promptData, setPromptData] = useState("");
  const [edit, setEdit] = useState(false);
  const controllerRef = useRef(null);
  const websocketRef = useRef(null);
  const promptStreamUrl = usePromptStreamUrl();

  // Helper function to update streamed text
  const setStreamedText = (updater) => {
    if (setStreamedTextParent) {
      if (typeof updater === "function") {
        setStreamedTextParent((prev) => updater(prev));
      } else {
        setStreamedTextParent(updater);
      }
    }
  };
  const { control, handleSubmit, watch, setValue } = useForm({
    defaultValues: {
      // explanation: data?.valueInfos?.reason,
      promptType: "",
      userPrompt: {
        // id: getRandomId(),
        role: "user",
        content: "",
      },
      improvement_requirements: "",
    },
    resolver: zodResolver(improvePromptValidationSchema),
  });

  const selectedValue = watch("promptType");
  const userPrompt = watch("userPrompt");
  const improvement_requirements = watch("improvement_requirements");

  const availableRunPrompt = useMemo(
    () => allColumns?.find((item) => item.originType === "run_prompt"),
    [allColumns],
  );

  const replaceIdWithColumnName = (content) => {
    let incomingText = content?.[0]?.text || content || "";
    allColumns.forEach(({ headerName, field }) => {
      const pattern = new RegExp(`{{${field}}}`, "g");
      if (incomingText && incomingText?.length)
        incomingText = incomingText.replace(pattern, `{{${headerName}}}`);
    });
    return incomingText;
  };

  const replaceColumnNameWithId = (content) => {
    let incomingText = content?.[0]?.text || content || "";
    allColumns.forEach(({ headerName, field }) => {
      const pattern = new RegExp(`{{${headerName}}}`, "g");
      if (incomingText && incomingText?.length)
        incomingText = incomingText.replace(pattern, `{{${field}}}`);
    });
    return incomingText;
  };

  const { mutate: submitImprovePrompt, isPending: isPendingSubmittedPrompt } =
    useMutation({
      mutationFn: (formData) => {
        return new Promise((resolve, reject) => {
          // Add settled flag to track promise resolution
          let settled = false;
          const isSettled = () => settled;
          const markSettled = () => {
            settled = true;
          };
          const resetImprovementState = () => {
            setIsGenerateVisible(false);
            setStreamedText("");
          };

          // Close any existing WebSocket connection
          if (websocketRef.current) {
            websocketRef.current.close();
          }

          // Reset streamed text
          setStreamedText("");

          // Create WebSocket connection
          const socket = runPromptOverSocket({
            url: promptStreamUrl,
            payload: {
              existing_prompt: formData.existing_prompt,
              improvement_requirements: formData.improvement_requirements,
              type: "improve_prompt",
            },
            onMessage: (wsData) => {
              // Surface top-level BE error frames (permission, workspace, etc.)
              // before the type filter so the spinner doesn't hang on 4003/4004.
              const handled = handleWsErrorFrame({
                wsData,
                isSettled,
                markSettled,
                cleanup: resetImprovementState,
                reject,
                defaultMessage: "Failed to improve prompt",
              });
              if (handled) return;
              if (wsData?.type !== "improve_prompt") return;

              const current_activity = wsData?.current_activity;
              const status = wsData?.status;

              if (status === "error") {
                settled = true;
                enqueueSnackbar(wsData?.error || "Failed to improve prompt", {
                  variant: "error",
                });
                setIsGenerateVisible(false);
                setStreamedText("");
                reject(wsData?.error);
                return;
              }

              if (
                status === "running" &&
                current_activity === "generate_refined_prompt"
              ) {
                // Stream chunks in real-time
                setStreamedText((prev) => prev + (wsData?.chunk ?? ""));
              } else if (
                status === "completed" &&
                current_activity === "generate_refined_prompt"
              ) {
                settled = true;
                // Set final improved prompt
                const improvedPrompt = wsData?.chunk;
                const prompt = [userPrompt];
                prompt.push({
                  role: "assistant",
                  content: improvedPrompt || "",
                });
                const config = runPromptDataRaw?.data?.result?.config;
                const matchData = allColumns
                  .filter((item) => item.originType === "run_prompt")
                  ?.find((item) => item.field === selectedValue);

                const payload = {
                  promptData: { prompt: improvedPrompt },
                  config: config,
                  messages: prompt,
                  data: matchData?.col || {},
                };
                setSelectedPrompt(payload);
                setIsGenerateVisible(false);
                setStreamedText("");
                enqueueSnackbar("Improve Prompt completed successfully!", {
                  variant: "success",
                });
                resolve(improvedPrompt);
              }
            },
            onError: (err) => {
              settled = true;
              enqueueSnackbar(
                typeof err === "string"
                  ? err
                  : "Failed to connect. Please try again.",
                {
                  variant: "error",
                },
              );
              setIsGenerateVisible(false);
              setStreamedText("");
              reject(err);
            },
            onClose: (event) => {
              const handled = handleAuthFailClose({
                event,
                isSettled,
                markSettled,
                cleanup: resetImprovementState,
                reject,
              });
              if (handled) return;
              if (settled) return;
              settled = true;
              resetImprovementState();
              reject(new Error("WebSocket connection closed unexpectedly"));
            },
          });

          websocketRef.current = socket;
        });
      },
    });

  const { data: runPromptDataRaw, mutate } = useMutation({
    mutationFn: (id) =>
      axios.get(endpoints.develop.runPrompt.getRunPrompt(), {
        params: { column_id: id || selectedValue },
      }),
    onSuccess: (runPromptDataRaw) => {
      const runPromptData = runPromptDataRaw?.data?.result?.config;
      const userprompt = watch("userPrompt");
      const content = runPromptData?.messages
        .filter((item) => item.role === "user")
        ?.map((item) =>
          typeof item.content === "string"
            ? item.content
            : item.content.map((temp) => temp.text),
        )
        ?.join(",");
      if (content) {
        const payload = {
          prompt: content,
          explanation: data?.valueInfos?.reason,
        };
        analyzePrompt(payload);
      }
      setValue("userPrompt", {
        ...userprompt,
        content: replaceIdWithColumnName(content),
      });
    },
  });

  const { mutate: analyzePrompt, isPending: analyzePromptLoading } =
    useMutation({
      mutationFn: (formData) => {
        if (controllerRef?.current) {
          controllerRef?.current.abort();
        }
        controllerRef.current = new AbortController();
        const signal = controllerRef.current.signal;
        return axios.post(endpoints.develop.runPrompt.analyzePrompt, formData, {
          signal,
        });
      },
      onMutate: () => {
        // Abort previous request before making a new one
        if (controllerRef?.current) {
          controllerRef.current.abort();
        }
      },
      onSuccess: (analyzePromptData) => {
        const value = analyzePromptData?.data?.result?.improvementSuggestions;
        setValue("improvement_requirements", replaceIdWithColumnName(value));
      },
    });

  useEffect(() => {
    return () => {
      if (controllerRef.current) {
        controllerRef.current.abort();
      }
      if (websocketRef.current) {
        websocketRef.current.close();
      }
    };
  }, []);

  const onSubmit = (formData) => {
    setIsGenerateVisible(true);
    setPromptData(improvement_requirements);
    const payload = {
      improvement_requirements: formData.improvement_requirements,
    };
    setValue("improvement_requirements", "");
    // Use previously improved prompt if available, otherwise use current user prompt
    if (selectedPrompt?.promptData?.prompt) {
      payload.existing_prompt = replaceColumnNameWithId(
        selectedPrompt.promptData.prompt,
      );
    } else {
      payload.existing_prompt = replaceColumnNameWithId(
        formData.userPrompt.content,
      );
    }
    submitImprovePrompt(payload);
  };

  const fieldOptions = useMemo(() => {
    const options = allColumns
      .filter((item) => item.originType === "run_prompt")
      ?.map((item) => ({ value: item.field, label: item.headerName }));
    if (data?.originType === "run_prompt" || data?.metadata?.runPrompt) {
      const id = data?.metadata?.runPrompt
        ? data?.metadata?.runPromptId?.length > 0
          ? data.metadata.runPromptId[0]
          : null
        : data.id;
      if (id) {
        mutate(id);
        setValue("promptType", id);
      }
    }
    return options;
  }, [data]);

  // const fieldOptions = useMemo(
  //   () =>
  //     allColumns
  //       .filter((item) => item.originType === "run_prompt")
  //       ?.map((item) => ({ value: item.field, label: item.headerName })),
  //   [allColumns],
  // );

  return (
    <Box
      sx={{
        padding: "10px 15px 10px 10px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
        width: "400px",
      }}
      component="form"
      onSubmit={handleSubmit(onSubmit)}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography
          fontWeight={700}
          sx={{ fontSize: "14px" }}
          color="text.primary"
        >
          Improve AI
        </Typography>
        <IconButton onClick={onClose} size="small">
          {/* <Iconify icon="mingcute:close-line" /> */}
        </IconButton>
      </Box>
      {availableRunPrompt ? (
        <Box
          sx={{
            gap: 2,
            display: "flex",
            flexDirection: "column",
            flex: 1,
            overflow: "auto",
            paddingBottom: "10px",
          }}
        >
          <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
            {/* <Iconify icon="solar:info-circle-bold" color="text.secondary" /> */}
            <Typography fontSize="12px" color="text.secondary">
              You can generate a structured prompt by sharing basic details
              about your task
            </Typography>
          </Box>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <FormSelectField
              label="Choose prompt column"
              control={control}
              fieldName="promptType"
              valueSelector={(o) => o.value}
              // options={[{ value: "Meta Lama", label: "Meta Lama" }]}
              options={fieldOptions}
              fullWidth
              onChange={() => {
                mutate();
              }}
              // InputProps={inputProps}
              // sx={{ height: "45px" }}
              size="small"
            />
            <PromptSection
              allColumns={allColumns}
              control={control}
              contentSuffix="content"
              roleSelectDisabled={false}
              prefixControlString={`userPrompt`}
              roleSuffix="role"
            />
            {promptData && (
              <Box
                sx={{
                  backgroundColor: "rgba(147, 143, 163, 0.08)",
                  padding: "8px 10px",
                  borderRadius: "8px",
                  marginLeft: "50px",
                }}
              >
                {edit ? (
                  <TextFieldWithoutBorder
                    control={control}
                    name={"improvement_requirements"}
                  />
                ) : (
                  <Typography sx={{ fontSize: "14px" }}>
                    {promptData}
                  </Typography>
                )}
                <Box sx={{ textAlign: "right" }}>
                  {!edit ? (
                    <Iconify
                      icon="material-symbols:edit"
                      color="text.secondary"
                      sx={{ cursor: "pointer" }}
                      onClick={() => setEdit(true)}
                    />
                  ) : (
                    <Box
                      sx={{
                        display: "flex",
                        gap: 1,
                        justifyContent: "flex-end",
                        marginBottom: "5px",
                      }}
                      onClick={() => setEdit(false)}
                    >
                      <Button
                        sx={{
                          padding: "0px 5px",
                          backgroundColor: "action.selected",
                          color: "text.primary",
                        }}
                      >
                        Cancel
                      </Button>
                      <Button
                        variant="contained"
                        color="primary"
                        sx={{ padding: "0px 5px" }}
                        type="submit"
                      >
                        Update
                      </Button>
                    </Box>
                  )}
                </Box>
              </Box>
            )}
          </Box>
        </Box>
      ) : (
        <Box
          sx={{
            gap: 2,
            display: "flex",
            flexDirection: "column",
            flex: 1,
            overflow: "auto",
            paddingBottom: "10px",
          }}
        >
          <Typography sx={{ fontSize: "14px" }}>
            To run the Improvement, It has to be run prompt only.
          </Typography>
        </Box>
      )}
      {availableRunPrompt && (
        <Box sx={{ padding: "0 10px" }}>
          <AllInputField
            label="What would you like to improve?"
            placeholder="Enter what would you like to improve in the prompt"
            size="small"
            control={control}
            onChange={() => {
              if (controllerRef.current) {
                controllerRef.current.abort();
              }
            }}
            fieldName="improvement_requirements"
            variant="filled"
            multiline
            fullWidth
            rows={4}
            sx={{ marginBottom: "12px" }}
            {...(analyzePromptLoading
              ? {
                  InputProps: {
                    endAdornment: (
                      <InputAdornment
                        position="end"
                        sx={{
                          alignItems: "self-end",
                          marginTop: "-30px",
                          marginRight: "-15px",
                        }}
                      >
                        <Iconify
                          icon="codex:loader"
                          sx={{
                            color: "primary.main",
                            width: "40px",
                            height: "40px",
                          }}
                        />
                      </InputAdornment>
                    ),
                  },
                }
              : {})}
          />
          <LoadingButton
            // onSubmit={handleSubmit(onSubmit)}
            variant="contained"
            color="primary"
            type="submit"
            fullWidth
            size="small"
            loading={isPendingSubmittedPrompt}
            sx={{ marginTop: "10px" }}
          >
            Submit
          </LoadingButton>
        </Box>
      )}
    </Box>
  );
};

const RightSection = ({
  isGenerateVisible,
  onClose,
  selectedPrompt,
  setConfigureRunPrompt,
  streamedText,
}) => {
  const onClickCopy = () => {
    copyToClipboard(selectedPrompt?.promptData?.prompt);
    enqueueSnackbar({
      variant: "success",
      message: "Response copied to clipboard",
    });
    // trackEvent(Events.optimizeDetailPageCopyPrompt);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    // const config = selectedPrompt.config;
    // const payload = {
    //   columnId: config.columnId,
    //   datasetId: config.datasetId,
    //   name: config.name,
    //   config: {
    //     ...config,
    //     messages: selectedPrompt.messages,
    //   },
    // };
    selectedPrompt?.promptData?.prompt &&
      setConfigureRunPrompt({
        ...(selectedPrompt?.data || {}),
        improvePrompt: selectedPrompt?.promptData?.prompt,
      });
    onClose();
    // console.log("payload", payload);
    // mutate(payload);
  };

  return (
    <Box
      sx={{
        padding: "20px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
        width: "400px",
      }}
      // component="form"
      // onSubmit={handleSubmit}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography
          fontWeight={700}
          sx={{ fontSize: "14px" }}
          color="text.secondary"
        >
          Prompt Improvement
        </Typography>
        <Box sx={{ display: "flex", gap: "10px" }}>
          <Tooltip title="See previous result" arrow>
            <IconButton size="small">
              <Iconify icon="ci:arrow-undo-down-left" />
            </IconButton>
          </Tooltip>
          <Tooltip title="See latest Result" arrow>
            <IconButton size="small">
              <Iconify icon="ci:arrow-undo-up-right" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Copy" arrow>
            <IconButton
              onClick={onClickCopy}
              disabled={!selectedPrompt?.promptData?.prompt}
              size="small"
            >
              <Iconify icon="basil:copy-outline" />
            </IconButton>
          </Tooltip>
          <LoadingButton
            disabled={!selectedPrompt?.promptData?.prompt}
            variant="contained"
            color="primary"
            // type="submit"
            size="small"
            // loading={isPending}
            onClick={handleSubmit}
          >
            Apply
          </LoadingButton>
          <IconButton onClick={onClose} size="small">
            <Iconify icon="mingcute:close-line" />
          </IconButton>
        </Box>
      </Box>
      <Box
        sx={{
          gap: 2,
          display: "flex",
          flexDirection: "column",
          flex: 1,
          color: isGenerateVisible ? "text.secondary" : "",
          padding: "10px",
          paddingTop: isGenerateVisible && "0px",
          overflow: "auto",
          lineHeight: "24px",
          fontSize: "14px",
          // paddingBottom: "10px",
          borderRadius: "8px",
          backgroundColor: "rgba(147, 143, 163, 0.08)",
        }}
      >
        {isGenerateVisible ? (
          <Markdown
            rehypePlugins={[rehypeSanitize]}
            components={{
              a: ({ node, ...props }) => (
                <a target="_blank" rel="noopener noreferrer" {...props} />
              ),
            }}
          >
            {streamedText || "Generating..."}
          </Markdown>
        ) : (
          <Markdown
            rehypePlugins={[rehypeSanitize]}
            components={{
              a: ({ node, ...props }) => (
                <a target="_blank" rel="noopener noreferrer" {...props} />
              ),
            }}
          >
            {selectedPrompt?.promptData?.prompt}
          </Markdown>
        )}
      </Box>
    </Box>
  );
};

const AllInputField = ({ label, ...rest }) => {
  return (
    <Box sx={{ width: "100%" }}>
      {label && (
        <Typography
          sx={{
            fontSize: "14px",
            fontWeight: "700",
            lineHeight: "18.2px",
            letterSpacing: "0.02em",
            color: "text.secondary",
            marginBottom: "10px",
          }}
        >
          {label}
        </Typography>
      )}
      <FormTextFieldV2
        {...rest}
        hiddenLabel
        fullWidth
        sx={{ border: "1px solid var(--border-default)", borderRadius: "8px" }}
      />
    </Box>
  );
};

const TextFieldWithoutBorder = ({ name, control }) => {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field }) => (
        <TextField
          {...field}
          fullWidth
          multiline
          minRows={1}
          sx={{
            background: "transparent",
            color: "text.primary",
            padding: "0px 2px",
            "& .MuiOutlinedInput-root": {
              padding: "0px 2px", // Removes padding from the root input container
              "&:hover .MuiOutlinedInput-notchedOutline": {
                border: "none", // Remove border on hover
              },
              "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
                border: "none", // Remove border on focus
              },
            },
            "& .MuiInputBase-input": {
              padding: "0px 2px", // Removes padding inside the input field
            },
            "& .MuiInputLabel-root": {
              padding: "0px 2px", // Removes padding from the label, if present
            },
            // "& .MuiOutlinedInput-notchedOutline": {
            //   border: "none", // Remove the default border
            // },
          }}
        />
      )}
    />
  );
};

ImprovePrompt.propTypes = {};

LeftSection.propTypes = {
  setIsGenerateVisible: PropTypes.func,
  onClose: PropTypes.func,
  data: PropTypes.object,
  allColumns: PropTypes.array,
  selectedPrompt: PropTypes.any,
  setSelectedPrompt: PropTypes.any,
  setStreamedTextParent: PropTypes.func,
};

RightSection.propTypes = {
  isGenerateVisible: PropTypes.bool,
  onClose: PropTypes.func,
  selectedPrompt: PropTypes.any,
  data: PropTypes.object,
  setConfigureRunPrompt: PropTypes.func,
  streamedText: PropTypes.string,
};

AllInputField.propTypes = {
  label: PropTypes.string,
};

TextFieldWithoutBorder.propTypes = {
  name: PropTypes.string,
  control: PropTypes.any,
};
