import React, { useState, useRef, useEffect } from "react";
import { Box } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useForm } from "react-hook-form";
import { getRandomId } from "src/utils/utils";
import { useLocation, useNavigate } from "react-router";
import { useParams } from "src/routes/hooks";
import { enqueueSnackbar } from "notistack";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { extractJinjaVariables } from "src/utils/jinjaVariables";

import Results from "./PromptGenerate/Results";
import Workbench from "./PromptGenerate/Workbench";
import TopMenu from "./TopMenu";
import logger from "src/utils/logger";

const transformToPayload = (
  titles,
  checkVal,
  modelData,
  evalsConfigs,
  isRunTemplate,
  appliedVariableData,
  currentTitle,
  templateFormat,
) => {
  const configuration = {
    temperature: modelData?.temperature || 0.7,
    frequency_penalty: modelData?.frequencyPenalty || 0.0,
    presence_penalty: modelData?.presencePenalty || 0.0,
    max_tokens: modelData?.maxTokens || 1000,
    top_p: modelData?.topP || 1.0,
    model: modelData?.model,
    response_format: modelData?.responseFormat || "text",
    tool_choice: modelData?.toolChoice === "none" ? "" : modelData?.toolChoice,
    tools: modelData?.tools || [],
    template_format: templateFormat || "mustache",
  };

  const messages = checkVal.map((item) => {
    return {
      role: item.role,
      content: item.content?.map((object) => {
        const { type } = object;
        // console.log(type,value);
        const isImage = type === "image_url";
        const typeOfInput = isImage ? "image_url" : "text";
        const content = isImage ? object.imageUrl : object.text;
        return {
          type: type,
          [typeOfInput]: content,
        };
      }),
    };
  });

  // console.log(messages);

  const payload = {
    name: currentTitle,
    variable_names: appliedVariableData,
    prompt_config: [
      {
        messages: messages,
        configuration: configuration,
      },
    ],
  };
  payload.is_run = isRunTemplate;
  payload.evaluation_configs = evalsConfigs;

  return payload;
};

const AddNewPromptView = () => {
  const location = useLocation();
  const [modelData, setModelData] = useState({});
  const [extractedVars, setExtractedVars] = useState([]);
  const [templateFormat, setTemplateFormat] = useState("mustache");
  const debounceTimeout = useRef(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [titles, setTitles] = useState([]);
  const [versionIndex, setVersionIndex] = useState(0);
  const [resultState, setResultState] = useState(null);
  const [evalsConfigs, setEvalsConfigs] = useState([]);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [versionList, setVersionList] = useState([]);
  const [appliedVariableData, setAppliedVariableData] = useState({});
  const [currentTitle, setCurrentTitle] = useState(location.state?.title);
  const defaultValues = {
    config: {
      messages: [
        {
          id: getRandomId(),
          role: "system",
          content: [
            {
              type: "text",
              text: "",
            },
          ],
        },
        {
          id: getRandomId(),
          role: "user",
          content: [
            {
              type: "text",
              text: "",
            },
          ],
        },
      ],
    },
  };

  const { id } = useParams();
  const versions = useQuery({
    queryKey: ["versions", id],
    enabled: !!id,
    queryFn: () => axios.get(endpoints.develop.runPrompt.getPromptVersions(id)),
  });
  const latestVersion = useQuery({
    queryKey: ["latestVersionData", id],
    enabled: versions.isPending === false,
    queryFn: async () => {
      const res = await axios.get(endpoints.develop.runPrompt.getPrompt(id));
      return res.data;
    },
    gcTime: 0,
    staleTime: Infinity,
  });

  const convertDefaultValues = () => {
    const latestVersionData = latestVersion?.data;
    const defaultValueMessages =
      latestVersionData?.promptConfig?.[0].messages.map((item) => {
        const content = item.content.map((temp) => ({
          ...temp,
          text: temp.text,
        }));
        return {
          id: getRandomId(),
          role: item.role,
          content: content,
        };
      });
    return {
      config: {
        messages: defaultValueMessages,
      },
    };
  };

  const updateVersionList = () => {
    const draftItem = latestVersion?.data;
    const arrWithoutDraftInfo = versions?.data?.data?.results?.map((item) => {
      return {
        ...item,
        isDraft: false,
      };
    });
    const newItem = {
      ...arrWithoutDraftInfo?.[versionIndex],
      evaluationConfigs: draftItem?.evaluationConfigs,
      output: arrWithoutDraftInfo?.[versionIndex]?.output,
      variable_names: draftItem?.variable_names,
      templateVersion: draftItem?.version,
      promptConfigSnapshot: {
        configuration: draftItem?.promptConfig[0].configuration,
        messages: draftItem?.promptConfig[0].messages,
      },
      isDraft: true,
    };
    if (draftItem.isDraft) {
      setVersionList([newItem, ...arrWithoutDraftInfo]);
    } else {
      setVersionList(arrWithoutDraftInfo);
    }
  };

  const { control, watch, setValue } = useForm({
    defaultValues: async () => {
      const versionData = latestVersion?.data;
      if (versionData) {
        setAppliedVariableData(
          Array.isArray(versionData?.variable_names)
            ? {}
            : versionData?.variable_names ?? {},
        );
        setCurrentTitle(versionData?.name);
        setEvalsConfigs(versionData?.evaluationConfigs ?? []);
        return convertDefaultValues();
      }
      return defaultValues;
    },
  });

  const checkVal = watch("config.messages");

  const deletePrompt = useMutation({
    mutationFn: (id) =>
      axios.delete(endpoints.develop.runPrompt.promptDelete(id)),
    onSuccess: () => {
      // fetchPrompts();
      queryClient.invalidateQueries({
        queryKey: ["prompts"],
      });
    },
  });

  const handleDelete = (id) => {
    deletePrompt.mutate(id);
  };

  const handleModelSettingData = (data) => {
    setModelData(data?.config);
  };

  const { mutate: handleLabelRunTemplate } = useMutation({
    /**
     * @param {Object} variables - The variables object containing id and isRun.
     * @param {string} variables.id - The ID of the prompt.
     * @param {string | null} variables.isRun - Whether to run the template.
     */
    mutationFn: ({ id, isRun }) => {
      const url = endpoints.develop.runPrompt.runTemplatePrompt(id);
      const payload = transformToPayload(
        titles,
        checkVal,
        modelData,
        evalsConfigs,
        isRun,
        appliedVariableData,
        currentTitle,
        templateFormat,
      );
      trackEvent(Events.runPromptInitiated, {
        [PropertyName.formFields]: payload,
      });
      return axios.post(url, payload);
    },
    onSuccess: (data, variables) => {
      const { isRun } = variables;
      queryClient.invalidateQueries({ queryKey: ["latestVersionData"] });
      queryClient.invalidateQueries({ queryKey: ["versions"] });
      if (isRun) {
        setResultState(data?.data?.result?.status);
        setVersionIndex(0);
      } else {
        updateVersionList();
      }
    },
    onError: (error) => {
      logger.error("Mutation failed:", error);
    },
  });

  function handleLabelsAdd(isRun = "prompt") {
    if (!modelData?.model && isRun) {
      enqueueSnackbar(
        "Please select a model from model settings before proceeding.",
        {
          variant: "warning",
        },
      );
      return;
    }
    handleLabelRunTemplate({ id, isRun });
    const timer = setInterval(async () => {
      const response = await axios.get(
        endpoints.develop.runPrompt.getStatus(id),
      );
      if (
        response.data?.result?.status == "Completed" ||
        response.data?.result?.status == "NotStarted"
      ) {
        clearInterval(timer);
        if (isRun) {
          setVersionList([
            {
              ...response.data?.result?.executionsResult,
              isDraft: false,
            },
            ...versionList.slice(1),
          ]);
          setVersionIndex(0);
          setResultState("Completed");
        }
      }
    }, 2000);
    // const payload = transformToPayload(
    //   titles,
    //   checkVal,
    //   modelData,
    //   evalsConfigs,
    //   isRun,
    //   appliedVariableData,
    //   currentTitle,
    // );
    // console.log(payload);
  }

  const { mutate: createDraft } = useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.develop.runPrompt.createPromptDraft, body),
    onSuccess: (data) => {
      enqueueSnackbar("Prompt created successfully.", {
        variant: "success",
      });
      const titleName = data?.data?.result;
      setTitles((prev) => [
        { name: titleName.name, id: titleName.id },
        ...prev,
      ]);
      setCurrentTitle(titleName?.name);
      navigate(`/dashboard/prompt/add/${data?.data?.result?.rootTemplate}`);
    },
  });

  const handleCreateDraft = () => {
    const payload = {
      name: "",
      prompt_config: [
        {
          messages: [
            {
              id: getRandomId(),
              role: "system",
              content: [
                {
                  type: "text",
                  text: "",
                },
              ],
            },
            {
              id: getRandomId(),
              role: "user",
              content: [
                {
                  type: "text",
                  text: "",
                },
              ],
            },
          ],
        },
      ],
    };
    createDraft(payload);
    setVersionList([{ templateVersion: "V1", isDraft: true }]);
  };

  const extractVariables = (content) => {
    if (!Array.isArray(content) || content?.length === 0) {
      return [];
    }

    const variables = [];

    for (const item of content) {
      if (item.type === "text") {
        if (templateFormat === "jinja") {
          variables.push(...extractJinjaVariables(item.text));
        } else {
          const match = item.text.match(/{{(.*?)}}/g);
          if (match) {
            variables.push(...match.map((v) => v.replace(/{{|}}/g, "").trim()));
          }
        }
      }
    }
    return variables;
  };

  useEffect(() => {
    if (versions.isPending == false) {
      setVersionIndex(0);
      const arr = versions.data?.data?.results;
      if (arr.length > 0) {
        if (resultState !== "Completed") {
          setResultState("Completed");
        }
      } else {
        setVersionList([{ templateVersion: "V1", isDraft: false }]);
        if (resultState != null) {
          setResultState(null);
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, versions.isPending]);

  useEffect(() => {
    if (latestVersion.isPending === false) {
      const currentResult = latestVersion?.data;
      const convertedDefaultValues = convertDefaultValues();
      setValue("config.messages", convertedDefaultValues.config.messages);
      setCurrentTitle(currentResult?.name);
      setEvalsConfigs(currentResult?.evaluationConfigs ?? []);
      setModelData(currentResult?.promptConfig[0]?.configuration);
      const savedFormat =
        currentResult?.promptConfig[0]?.configuration?.template_format;
      setTemplateFormat(savedFormat || "mustache");
      updateVersionList();
      if (
        JSON.stringify(appliedVariableData) !==
        JSON.stringify(currentResult?.variable_names)
      ) {
        setAppliedVariableData(
          Array.isArray(currentResult?.variable_names)
            ? {}
            : currentResult?.variable_names ?? {},
        );
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, latestVersion.isPending]);

  useEffect(() => {
    if (debounceTimeout.current) {
      clearTimeout(debounceTimeout.current);
    }

    debounceTimeout.current = setTimeout(() => {
      if (checkVal?.length) {
        const allVariables = checkVal.flatMap((item) => {
          const currentContent = item?.content || "";
          return extractVariables(currentContent);
        });

        const uniqueVariables = Array.from(new Set(allVariables));
        setExtractedVars(uniqueVariables);
      }
    }, 600);

    return () => clearTimeout(debounceTimeout.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(checkVal), templateFormat]);

  useEffect(() => {
    const newEvalsConfigs = versionList[versionIndex]?.evaluationConfigs;
    const newVariableNames = versionList[versionIndex]?.variable_names;
    const newTemplateName = versionList[versionIndex]?.templateName;
    const newConfiguration =
      versionList[versionIndex]?.promptConfigSnapshot?.configuration;
    const newMessages =
      versionList[versionIndex]?.promptConfigSnapshot?.messages;

    if (
      newEvalsConfigs &&
      JSON.stringify(evalsConfigs) !== JSON.stringify(newEvalsConfigs)
    ) {
      setEvalsConfigs(newEvalsConfigs);
    }

    if (
      newVariableNames &&
      JSON.stringify(appliedVariableData) !== JSON.stringify(newVariableNames)
    ) {
      setAppliedVariableData(
        Array.isArray(newVariableNames) ? {} : newVariableNames ?? {},
      );
    }

    if (newTemplateName && currentTitle !== newTemplateName) {
      setCurrentTitle(newTemplateName);
    }

    if (
      newConfiguration &&
      JSON.stringify(modelData) !== JSON.stringify(newConfiguration)
    ) {
      setModelData(newConfiguration);
    }

    if (
      JSON.stringify(watch("config.messages")) !== JSON.stringify(newMessages)
    ) {
      const message = newMessages?.map((item) => {
        const content = item.content.map((temp) => ({
          ...temp,
          text: temp.text,
        }));
        return { ...item, content };
      });
      setValue("config.messages", message);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionIndex]);

  return (
    <Box
      sx={{
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        backgroundColor: "background.paper",
      }}
    >
      <TopMenu
        handleLabelsAdd={handleLabelsAdd}
        handleModelSettingData={handleModelSettingData}
        variables={extractedVars}
        setExtractedVars={setExtractedVars}
        versions={versionList}
        resultState={resultState}
        currentIndex={currentIndex}
        setCurrentIndex={setCurrentIndex}
        initialConfig={modelData}
        evalsConfigs={evalsConfigs}
        setEvalsConfigs={setEvalsConfigs}
        handleDelete={handleDelete}
        handleCreateDraft={handleCreateDraft}
        appliedVariableData={appliedVariableData}
        setAppliedVariableData={setAppliedVariableData}
        currentTitle={currentTitle}
        setCurrentTitle={setCurrentTitle}
        versionIndex={versionIndex}
        setVersionIndex={setVersionIndex}
        setVersionList={setVersionList}
        setTitles={setTitles}
        titles={titles}
        total={
          latestVersion?.data?.isDraft
            ? versions?.data?.data?.count + 1
            : versions?.data?.data?.count
        }
        templateFormat={templateFormat}
        setTemplateFormat={setTemplateFormat}
      />
      <Box sx={{ display: "flex", height: "calc(100% - 55px)", pb: "17px" }}>
        <Workbench
          control={control}
          appliedVariableData={appliedVariableData}
          handleLabelsAdd={handleLabelsAdd}
          loading={versions.isLoading || latestVersion.isLoading}
        />
        <Results
          resultState={resultState}
          output={versionList[versionIndex] ?? {}}
        />
      </Box>
    </Box>
  );
};

export default AddNewPromptView;
