import React, {
  useCallback,
  useEffect,
  useMemo,
  useState,
  useRef,
} from "react";
import PropTypes from "prop-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { getRandomId, sanitizeContent } from "src/utils/utils";
import { useDebouncedFunction } from "src/hooks/use-debounced-function";
import { useSnackbar } from "src/components/snackbar";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { DefaultMessages } from "../constant";
import { isContentNotEmpty } from "./Playground/common";
import { modelConfigDefault, PromptWorkbenchContext } from "./WorkbenchContext";
import {
  getVariables,
  normalizeConfigurationForLoad,
  normalizeConfigurationForSave,
  runPromptOverSocket,
} from "./common";
import logger from "src/utils/logger";
import { usePromptStreamUrl } from "src/sections/workbench/createPrompt/hooks/usePromptStreamUrl";

const WorkbenchProvider = ({ children }) => {
  const { id } = useParams();

  const unsavedDrafts = useRef({});
  const streamingIds = useRef({});
  const queryClient = useQueryClient();

  //Multi States
  const [currentTab, setCurrentTab] = useUrlState("tab", "Playground");
  const [selectedVersions, setSelectedVersions] = useUrlState(
    "selected-versions",
    [],
  );
  const [prompts, setPrompts] = useState([]);
  const [placeholders, setPlaceholders] = useState([]);
  const [placeholderData, setPlaceholderData] = useState({});
  const [originalPlaceholderData, setOriginalPlaceholderData] = useState({});
  const [results, setResults] = useState(() => {
    if (selectedVersions.length === 0) {
      return [
        {
          output: [],
          isAnimating: false,
          metadata: {},
          outputFormat: "string",
        },
      ];
    }
    return selectedVersions.map(() => ({
      output: [],
      isAnimating: false,
      metadata: {},
      outputFormat: "string",
    }));
  });
  const [modelConfig, setModelConfig] = useState(() => {
    if (selectedVersions.length === 0) {
      return [{ ...modelConfigDefault }];
    }
    return selectedVersions.map(() => ({
      ...modelConfigDefault,
    }));
  });
  const [loadingStatus, setLoadingStatus] = useState(() => {
    if (selectedVersions.length === 0) {
      return [false];
    }
    return selectedVersions.map(() => false);
  });

  // Singular global states
  const [versionHistoryOpen, setVersionHistoryOpen] = useState(false);
  const [variableDrawerOpen, setVariableDrawerOpen] = useState(false);
  const [isImportDatasetDrawerOpen, setImportDatasetDrawerOpen] =
    useState(false);
  const [variableData, setVariableData] = useState({});
  const [promptName, setPromptName] = useState("");
  const [openSelectModel, setOpenSelectModel] = useState(null);
  const [valuesChanged, setValuesChanged] = useState(false);
  const [templateFormat, setTemplateFormat] = useState("mustache");
  const stoppedIds = useRef([]);
  const runningVersionIndexMapping = useRef({});
  const activeSocketsRef = useRef({});
  const promptStreamUrl = usePromptStreamUrl();

  // Close all active sockets on unmount to prevent leaks
  useEffect(() => {
    return () => {
      Object.values(activeSocketsRef.current).forEach((s) => s?.close());
      activeSocketsRef.current = {};
    };
  }, []);

  // const { socket, sendMessage: sendSocketMessage } = useSocket();
  const { enqueueSnackbar } = useSnackbar();

  const isEmptyPrompt = useMemo(() => {
    return prompts.reduce((indices, { prompts: promptsArray }, index) => {
      const hasContent = promptsArray.some((prompt) => {
        if (prompt.role === "system") return false;
        return isContentNotEmpty(prompt.content);
      });
      if (!hasContent) {
        indices.push(index);
      }
      return indices;
    }, []);
  }, [prompts]);

  const setSelectedVersionByIndex = (index, valueOrUpdater) => {
    setSelectedVersions((pre) => {
      const newPre = [...pre];
      const newValue =
        typeof valueOrUpdater === "function"
          ? { id: pre[index]?.id, ...valueOrUpdater(pre[index]) }
          : { ...valueOrUpdater };

      newPre[index] = newValue;
      return newPre;
    });
  };

  const setPromptsByIndex = (index, valueOrUpdater) => {
    setPrompts((pre) => {
      const newPre = [...pre];
      const newValue =
        typeof valueOrUpdater === "function"
          ? { id: pre[index]?.id, prompts: valueOrUpdater(pre[index]?.prompts) }
          : valueOrUpdater;

      newPre[index] = newValue;
      return newPre;
    });
  };

  const setPlaceholdersByIndex = (index, valueOrUpdater) => {
    setPlaceholders((pre) => {
      const newPre = [...pre];
      const newValue =
        typeof valueOrUpdater === "function"
          ? valueOrUpdater(pre[index] || [])
          : valueOrUpdater;

      newPre[index] = Array.isArray(newValue) ? newValue : [];
      return newPre;
    });
  };

  const setResultsByIndex = useCallback((index, valueOrUpdater) => {
    setResults((pre) => {
      const newPre = [...pre];
      const newValue =
        typeof valueOrUpdater === "function"
          ? valueOrUpdater(pre[index])
          : valueOrUpdater;

      newPre[index] = newValue;
      return newPre;
    });
  }, []);

  const setModelConfigByIndex = (index, valueOrUpdater) => {
    setModelConfig((pre) => {
      const newPre = [...pre];
      const newValue =
        typeof valueOrUpdater === "function"
          ? { id: pre[index]?.id, ...valueOrUpdater(pre[index]) }
          : valueOrUpdater;

      newPre[index] = newValue;
      return newPre;
    });
  };

  const setLoadingStatusByIndex = useCallback((index, valueOrUpdater) => {
    setLoadingStatus((pre) => {
      const newPre = [...pre];
      newPre[index] =
        typeof valueOrUpdater === "function"
          ? valueOrUpdater(pre[index])
          : valueOrUpdater;
      return newPre;
    });
  }, []);

  const reset = () => {
    setSelectedVersions([]);
    setVariableData({});
    setPrompts([]);
    setPlaceholders([]);
    setPlaceholderData({});
    setOriginalPlaceholderData({});
    setModelConfig([{ ...modelConfigDefault }]);
    setResults([{ output: [], isAnimating: false }]);
    setLoadingStatus([false]);
    unsavedDrafts.current = {};
  };

  const resultCache = useRef({});

  const { data, isPending, isLoading } = useQuery({
    queryKey: ["prompt-latest-version", id],
    queryFn: async () => {
      const res = await axios.get(endpoints.develop.runPrompt.getPrompt(id));
      return res.data;
    },
    gcTime: 0,
    staleTime: Infinity,
    enabled: selectedVersions.length <= 1,
  });

  const {
    data: compareVersionData,
    isPending: compareIsPending,
    isLoading: compareIsLoading,
  } = useQuery({
    queryKey: ["compare-versions", id],
    queryFn: async () =>
      axios.post(endpoints.develop.runPrompt.compareVersions(id), {
        versions: selectedVersions?.map(({ version }) => version),
      }),
    enabled: () => {
      const isEmptyAnyPrompt =
        prompts.length === 0 ||
        prompts.some(({ prompts }) => prompts.length === 0);
      return selectedVersions.length > 1 && isEmptyAnyPrompt;
    },
    select: (data) => data.data.result,
  });

  const resultSetter = useCallback(
    (versionIndex, resultIndex, chunk, status, outputFormat) => {
      setResultsByIndex(versionIndex, ({ output }) => {
        const cache = resultCache?.current?.[versionIndex]?.[resultIndex] ?? "";
        const existingResultData = output?.[resultIndex];

        // Create a NEW object instead of mutating the existing one.
        // AG Grid compares row object references to detect changes —
        // mutating in place keeps the same reference, so the grid
        // never re-renders the cell (even when text/status change).
        const updatedResult = existingResultData
          ? {
              ...existingResultData,
              text: existingResultData.text + cache + chunk,
              status,
              outputFormat,
            }
          : {
              id: getRandomId(),
              text: cache + chunk,
              status,
              outputFormat,
            };

        // Spread the array AND assign the new object at the index.
        // [...output] alone only creates a new array reference —
        // the objects inside are still the old references.
        const newResults = [...output];
        newResults[resultIndex] = updatedResult;

        if (resultCache?.current?.[versionIndex]) {
          resultCache.current[versionIndex][resultIndex] = "";
        }
        return {
          output: newResults,
          isAnimating: true,
        };
      });
    },
    [setResultsByIndex],
  );

  // const resultCacheSetter = (versionIndex, resultIndex, chunk) => {
  //   const versionCache = resultCache.current?.[versionIndex];
  //   if (versionCache) {
  //     resultCache.current[versionIndex][resultIndex] += chunk;
  //   } else {
  //     resultCache.current[versionIndex] = { [resultIndex]: chunk };
  //   }
  // };

  // const resultSetterThrottled = throttleWithElse(
  //   resultSetter,
  //   1000,
  //   resultCacheSetter,
  // );

  const getStreamingIds = useCallback((versions) => {
    return Object.entries(streamingIds.current || {}).reduce(
      (acc, [version, id]) => {
        if (versions.includes(version)) {
          acc.push(id);
        }
        return acc;
      },
      [],
    );
  }, []);

  const setStreamingIdForVersion = useCallback((version, id) => {
    streamingIds.current[version] = id;
  }, []);

  const pushStoppedIds = useCallback((id) => {
    stoppedIds.current.push(id);
  }, []);

  const closeSocketByIndex = useCallback((index) => {
    const socket = activeSocketsRef.current[index];
    if (socket) {
      socket.onerror = null;
      socket.onclose = null;
      socket.onmessage = null;
      socket.close();
      delete activeSocketsRef.current[index];
    }
  }, []);

  const setWsData = useCallback(
    (event) => {
      if (compareIsLoading || isLoading) {
        return;
      }
      try {
        const wsData = event;
        const stoppedId = stoppedIds.current;

        if (
          wsData?.type !== "run_prompt" ||
          stoppedId.includes(wsData?.session_uuid)
        ) {
          return;
        }

        const version = wsData?.version;

        const versionIndexMapping = selectedVersions.reduce(
          (acc, version, index) => {
            acc[version.version] = index;
            return acc;
          },
          {},
        );

        const streamingStatus = wsData?.streaming_status;
        const resultIndex = wsData?.result_index;
        const noOfResult = wsData?.num_results;
        const sessionUuid = wsData?.session_uuid;
        const isStreamingIdMatched =
          streamingIds.current[version] === wsData?.session_uuid;

        let versionIndex = versionIndexMapping[version];

        if (!versionIndex && sessionUuid) {
          versionIndex = runningVersionIndexMapping.current[version];
        }

        const versionLoadingStatus = loadingStatus[versionIndex];

        if (!versionLoadingStatus) {
          // check if running event are coming from this version
          // if (
          //   streamingStatus === "running" &&
          //   versionIndexMapping[version] !== undefined &&
          //   !streamingIds.current[version]
          // ) {
          //   setLoadingStatusByIndex(versionIndexMapping[version], true);
          //   streamingIds.current[version] = wsData?.session_uuid;
          //   isStreamingIdMatched = true;

          //   // also set all result index after the current one to empty string
          //   setResultsByIndex(versionIndexMapping[version], (versionResult) => {
          //     if (!versionResult?.output) {
          //       return { ...versionResult };
          //     }

          //     const { output, ...rest } = versionResult;

          //     return { ...rest, output: output.slice(0, resultIndex + 1) };
          //   });
          // } else {
          //   return;
          // }
          return;
        }

        switch (streamingStatus) {
          case "process_started":
          case "started": {
            streamingIds.current[version] = wsData?.session_uuid;

            if (
              resultIndex > 0 &&
              (wsData?.output_format === "image" ||
                wsData?.output_format === "audio")
            ) {
              resultSetter(
                versionIndex,
                resultIndex,
                "",
                "started",
                wsData?.output_format,
              );
            }
            break;
          }
          case "running": {
            if (versionIndex === undefined) {
              break;
            }
            if (!isStreamingIdMatched) {
              break;
            }
            resultSetter(versionIndex, resultIndex, wsData?.chunk, "running");
            break;
          }
          case "completed": {
            resultSetter(versionIndex, resultIndex, "", "completed");
            if (versionIndex === undefined || !isStreamingIdMatched) {
              break;
            }
            if (noOfResult === 1) {
              setLoadingStatusByIndex(versionIndex, false);
            }
            setResultsByIndex(versionIndex, ({ output }) => ({
              output: output.map((opt, rIndex) => {
                if (rIndex === resultIndex) {
                  return {
                    ...opt,
                    metadata: {
                      cost: wsData.metadata?.cost?.total_cost,
                      tokens: wsData.metadata?.usage?.total_tokens,
                      responseTime: wsData.metadata?.response_time,
                    },
                    outputFormat: wsData?.output_format,
                  };
                }
                return {
                  ...opt,
                };
              }),
              isAnimating: false,
            }));

            streamingIds.current[version] = null;
            queryClient.invalidateQueries({
              queryKey: ["prompt-versions", id],
            });
            queryClient.invalidateQueries({
              queryKey: ["prompt-latest-version", id],
            });
            break;
          }
          case "all_completed": {
            setLoadingStatus((d) => d.map(() => false));
            streamingIds.current[version] = null;
            runningVersionIndexMapping.current[version] = null;
            queryClient.invalidateQueries({
              queryKey: ["prompt-versions", id],
            });
            queryClient.invalidateQueries({
              queryKey: ["prompt-latest-version", id],
            });
            break;
          }
          case "error": {
            enqueueSnackbar(wsData?.error || "Something went wrong", {
              variant: "error",
            });
            resultSetter(versionIndex, resultIndex, wsData?.error, "error");
            if (versionIndex !== undefined) {
              setLoadingStatusByIndex(versionIndex, false);
            }
            streamingIds.current[version] = null;
            runningVersionIndexMapping.current[version] = null;
            queryClient.invalidateQueries({
              queryKey: ["prompt-versions", id],
            });
            break;
          }
          default:
            break;
        }
      } catch (err) {
        logger.error("Error parsing WebSocket data:", err);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      setResultsByIndex,
      setLoadingStatusByIndex,
      selectedVersions,
      loadingStatus,
      enqueueSnackbar,
      resultSetter,
      compareIsLoading,
      isLoading,
      id,
    ],
  );

  useEffect(() => {
    if (!data) {
      return;
    }
    setPromptName(data?.name);
    setSelectedVersions([
      {
        isDraft: data?.is_draft,
        version: data?.version,
        lastSaved: data?.last_saved,
        isDefault: data?.is_default,
        labels: data?.labels || [],
        originalTemplate: data?.original_template || id,
        templateVersion: data?.template_version,
        id: getRandomId(),
      },
    ]);
    if (data?.is_draft) {
      setCurrentTab("Playground");
    }

    if (data?.prompt_config?.[0]?.messages && !prompts?.[0]?.prompts.length) {
      const newPrompts = data?.prompt_config?.[0]?.messages?.map((prompt) => ({
        ...prompt,
        id: getRandomId(),
      }));
      setPromptsByIndex(0, { prompts: newPrompts, id: getRandomId() });
    }

    if (data?.prompt_config?.[0]?.placeholders && !placeholders?.[0]?.length) {
      const newPlaceholders = data?.prompt_config?.[0]?.placeholders || [];
      setPlaceholdersByIndex(0, newPlaceholders);
    }

    if (data?.placeholders) {
      setPlaceholderData(data.placeholders);
      setOriginalPlaceholderData(data.placeholders);
    }

    // Load variable_names from template
    if (data?.variable_names) {
      setVariableData(data.variable_names);
    }

    if (data?.prompt_config?.[0]?.configuration) {
      setModelConfigByIndex(0, {
        id: getRandomId(),
        ...modelConfigDefault,
        ...normalizeConfigurationForLoad(data?.prompt_config?.[0]?.configuration),
      });
      const savedFormat = data?.prompt_config?.[0]?.configuration?.template_format;
      setTemplateFormat(savedFormat || "mustache");
    }
    if (data?.output?.length && !results?.[0]?.output?.length) {
      setResultsByIndex(0, {
        output: data?.output?.map((opt, outputIdx) => ({
          id: getRandomId(),
          text: opt,
          metadata: {
            cost: data?.metadata?.[outputIdx]?.cost?.total_cost,
            tokens: data?.metadata?.[outputIdx]?.usage?.total_tokens,
            responseTime: data?.metadata?.[outputIdx]?.response_time,
          },
          outputFormat: data?.prompt_config?.[0]?.configuration?.output_format,
        })),
        isAnimating: false,
      });
    }
  }, [data]);

  const { mutate: compareRun } = useMutation({
    mutationFn: async () => {
      // Create promises for all versions in parallel
      const promises = selectedVersions.map(({ version }, versionIndex) => {
        return new Promise((resolve, reject) => {
          const comparePayload = {
            type: "run_template",
            template_id: id,
            version: version,
            is_run: "prompt",
          };

          // @ts-ignore
          const socket = runPromptOverSocket({
            url: promptStreamUrl,
            payload: { ...comparePayload },
            onMessage: (data) => {
              const shouldShowCompleted =
                data?.streaming_status === "all_completed";

              if (!shouldShowCompleted) {
                setWsData(data);
              }

              if (data?.streaming_status === "all_completed") {
                closeSocketByIndex(`compare-${versionIndex}`);
                resolve({ version, data });
              }
            },
            onError: (err) => {
              closeSocketByIndex(`compare-${versionIndex}`);
              reject({ version, error: err });
            },
          });
          activeSocketsRef.current[`compare-${versionIndex}`] = socket;
        });
      });

      // Wait for all versions to complete
      // eslint-disable-next-line no-useless-catch
      try {
        const results = await Promise.allSettled(promises);

        // Separate successful and failed results
        const successful = results
          .filter((r) => r.status === "fulfilled")
          .map((r) => r.value);

        const failed = results
          .filter((r) => r.status === "rejected")
          .map((r) => r.reason);

        if (failed.length > 0) {
          logger.info(`${failed.length} version(s) failed:`, failed);
        }

        return { successful, failed };
      } catch (err) {
        throw err;
      }
    },
    onSuccess: (results) => {
      for (const success of results.successful) {
        setWsData(success.data);
      }
    },
  });

  useEffect(() => {
    if (!compareVersionData) {
      return;
    }
    setPromptName(compareVersionData?.data?.[0].template_name);
    const newVersions = [];
    const newPrompts = [];
    const newResults = [];
    const newModelConfigs = [];
    const newPlaceholders = [];
    const newPlaceholdersData = {};

    compareVersionData.data.forEach((version, _idx) => {
      newVersions.push({
        isDraft: version?.is_draft,
        version: version?.template_version,
        lastSaved: version?.updated_at,
        isDefault: version?.is_default,
        labels: version?.labels || [],
        originalTemplate: version?.original_template || id,
        templateVersion: version?.template_version,
        id: getRandomId(),
      });

      newPrompts.push({
        prompts: version?.prompt_config_snapshot?.messages,
        id: getRandomId(),
      });

      if (version?.placeholders) {
        Object.assign(newPlaceholdersData, version?.placeholders);
      }

      newModelConfigs.push({
        id: getRandomId(),
        ...modelConfigDefault,
        ...normalizeConfigurationForLoad(
          version?.prompt_config_snapshot?.configuration,
        ),
      });

      const placeholderArray =
        version?.prompt_config_snapshot?.placeholders || [];
      newPlaceholders.push(placeholderArray);

      if (version?.output?.length) {
        newResults.push({
          output: version?.output?.map((opt, outputIdx) => ({
            id: getRandomId(),
            text: opt,
            metadata: {
              cost: version?.metadata?.[outputIdx]?.cost?.total_cost,
              tokens: version?.metadata?.[outputIdx]?.usage?.total_tokens,
              responseTime: version?.metadata?.[outputIdx]?.response_time,
            },
            outputFormat:
              version?.prompt_config_snapshot?.configuration?.output_format,
          })),
          isAnimating: false,
        });
      }
    });

    setSelectedVersions(newVersions);
    setPrompts(newPrompts);
    setModelConfig(newModelConfigs);
    setResults(newResults);
    setPlaceholders(newPlaceholders);
    setPlaceholderData(newPlaceholdersData);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compareVersionData]);

  const getSaveTemplatePayload = useCallback(
    (index, isRun) => {
      const currentConfig = { ...modelConfig[index] };
      delete currentConfig.id;
      const configuration = normalizeConfigurationForSave(currentConfig);
      configuration["tool_choice"] =
        currentConfig?.tools?.length > 0 ? "auto" : "";
      configuration["template_format"] = templateFormat || "mustache";

      const selectedVersion = selectedVersions[index];

      const currentPrompts = prompts[index]?.prompts;

      const currentPlaceholders = placeholders[index] || [];

      const finalPlaceholders = currentPlaceholders.reduce((acc, name) => {
        acc[name] = placeholderData?.[name] || [];
        return acc;
      }, {});

      const finalVariables = getVariables(currentPrompts, variableData, templateFormat);

      const sanitizedMessages = currentPrompts?.map(({ id, ...rest }) => {
        return {
          role: rest.role,
          content: rest.content.map((con) => {
            if (con.type === "text") {
              return {
                ...con,
                text: sanitizeContent(con.text),
              };
            }
            return con;
          }),
        };
      });

      const sanitizedPlaceholders = currentPlaceholders || [];

      const payload = {
        name: promptName,
        variable_names: finalVariables,
        placeholders: finalPlaceholders,
        prompt_config: [
          {
            messages: sanitizedMessages,
            configuration: configuration,
            placeholders: sanitizedPlaceholders,
          },
        ],
      };
      payload["is_run"] = isRun ?? false;
      payload["evaluation_configs"] = [];
      payload["version"] = selectedVersion?.version;

      return payload;
    },
    [
      modelConfig,
      prompts,
      promptName,
      selectedVersions,
      variableData,
      placeholders,
      placeholderData,
      templateFormat,
    ],
  );

  const getRunTemplatePayload = useCallback(
    (index) => {
      const selectedVersion = selectedVersions[index];

      return {
        type: "run_template",
        template_id: id,
        version: selectedVersion?.version,
        is_run: "prompt",
      };
    },
    [id, selectedVersions],
  );

  const { mutate: saveOrRunPromptTemplate } = useMutation({
    /**
     *
     * @param {Object} data
     * @returns
     */
    mutationFn: (data) => {
      const { isRun, index } = data;
      const url = endpoints.develop.runPrompt.runTemplatePrompt(id);
      const payload = getSaveTemplatePayload(index, isRun);

      return axios.post(url, payload);
    },
  });

  const { mutate: runPromptTemplate } = useMutation({
    /**
     *
     * @param {Object} data
     * @returns
     */
    mutationFn: ({ index }) => {
      const payload = getRunTemplatePayload(index);

      return new Promise((resolve, reject) => {
        // @ts-ignore
        const socket = runPromptOverSocket({
          url: promptStreamUrl,
          payload,
          onMessage: (data) => {
            setWsData(data);
            if (data?.streaming_status === "all_completed") {
              closeSocketByIndex(`run-${index}`);
              resolve(data);
            }
          },
          onError: (err) => {
            enqueueSnackbar(
              typeof err === "string"
                ? err
                : "Failed to connect. Please try again.",
              {
                variant: "error",
              },
            );
            closeSocketByIndex(`run-${index}`);
            reject(err);
          },
        });
        activeSocketsRef.current[`run-${index}`] = socket;
      });
    },
  });

  const { isPending: isAddingDraft, mutateAsync: addDraftToPromptAsync } =
    useMutation({
      mutationFn: (data) =>
        axios.post(endpoints.develop.runPrompt.addDraftInPrompt(id), data),
    });

  const debouncedSaveOrRunPromptTemplate = useDebouncedFunction(
    saveOrRunPromptTemplate,
    500,
    (args) => args[0].index,
  );

  const saveAndDraft = async (index) => {
    const currentVersion = selectedVersions[index];

    if (currentVersion.isDraft) {
      if (currentVersion.version) {
        debouncedSaveOrRunPromptTemplate({ index, isRun: false });
      } else {
        unsavedDrafts.current[index] = true;
      }
    } else {
      const payload = getSaveTemplatePayload(index, false);

      setSelectedVersionByIndex(index, (p) => {
        return {
          ...p,
          isDraft: true,
          version: null,
        };
      });

      unsavedDrafts.current[index] = true;

      await addDraftToPromptAsync(
        // @ts-ignore
        {
          new_prompts: [
            {
              variable_names: payload.variable_names,
              prompt_config: payload.prompt_config,
              evaluation_configs: [],
            },
          ],
        },
        {
          onSuccess: (newDraftRes) => {
            const newVersion = newDraftRes.data.result?.[0];

            setSelectedVersionByIndex(index, (p) => {
              return {
                ...p,
                version: newVersion?.template_version,
                lastSaved: newVersion?.updated_at,
                isDefault: newVersion?.is_default,
                isDraft: newVersion?.is_draft,
              };
            });

            // run the save which if remaining
            if (unsavedDrafts.current[index]) {
              debouncedSaveOrRunPromptTemplate({
                index,
                isRun: false,
              });
              unsavedDrafts.current[index] = null;
            }
          },
        },
      );
    }
  };

  const saveAndDraftAll = useCallback(
    async (saveDraftIndexes) => {
      const indexesToRun = saveDraftIndexes?.length
        ? saveDraftIndexes
        : selectedVersions.map((_, idx) => idx);

      const isDraftIndexes = indexesToRun.filter(
        (index) => selectedVersions[index]?.isDraft,
      );

      const isNotDraftIndexes = indexesToRun.filter(
        (index) => !selectedVersions[index]?.isDraft,
      );

      isDraftIndexes.forEach((index) => {
        debouncedSaveOrRunPromptTemplate({ index, isRun: false });
      });

      const payloads = isNotDraftIndexes.reduce((acc, index) => {
        acc[index] = getSaveTemplatePayload(index, false);
        return acc;
      }, {});

      setSelectedVersions((pre) => {
        return pre.map((version, idx) => {
          if (isNotDraftIndexes.includes(idx)) {
            return {
              ...version,
              isDraft: true,
              version: null,
            };
          }
          return version;
        });
      });

      isNotDraftIndexes.forEach((isNotDraftIndex) => {
        unsavedDrafts.current[isNotDraftIndex] = true;
      });

      if (isNotDraftIndexes.length) {
        // @ts-ignore
        const addResults = await addDraftToPromptAsync({
          new_prompts: isNotDraftIndexes.map(
            (isNotDraftIndex) => payloads[isNotDraftIndex],
          ),
        });

        const addDraftResults = addResults?.data.result;

        setSelectedVersions((pre) => {
          return pre.map((version, idx) => {
            const isNotDraftIndex = isNotDraftIndexes.findIndex(
              (id) => id === idx,
            );
            if (isNotDraftIndex !== -1) {
              const newVersion = addDraftResults[isNotDraftIndex];
              return {
                ...version,
                version: newVersion?.template_version,
                lastSaved: newVersion?.updated_at,
                isDefault: newVersion?.is_default,
                isDraft: newVersion?.is_draft,
              };
            }
            return version;
          });
        });

        isNotDraftIndexes.forEach((isNotDraftIndex) => {
          if (unsavedDrafts.current[isNotDraftIndex]) {
            debouncedSaveOrRunPromptTemplate({
              index: isNotDraftIndex,
              isRun: false,
            });

            unsavedDrafts.current[isNotDraftIndex] = null;
          }
        });
      }
    },
    [
      addDraftToPromptAsync,
      debouncedSaveOrRunPromptTemplate,
      getSaveTemplatePayload,
      selectedVersions,
      setSelectedVersions,
    ],
  );

  // Auto-save when user changes template format (skip initial load from API).
  const templateFormatUserChanged = useRef(false);
  useEffect(() => {
    if (!templateFormatUserChanged.current) return;
    saveAndDraftAll();
    templateFormatUserChanged.current = false;
  }, [templateFormat]); // eslint-disable-line react-hooks/exhaustive-deps

  const onRestoreVersion = (version) => {
    setSelectedVersionByIndex(0, (prev) => {
      return {
        ...prev,
        isDraft: version?.is_draft,
        version: version?.template_version,
        lastSaved: version?.created_at,
        isDefault: version?.is_default,
      };
    });

    const newPrompts =
      version?.prompt_config_snapshot?.messages?.map((rest) => ({
        ...rest,
        id: getRandomId(),
      })) || [];

    setPrompts([{ prompts: newPrompts, id: getRandomId() }]);

    // Load variable_names from template
    if (version?.variable_names) {
      setVariableData(version.variable_names);
    }

    const newPlaceholders = version?.prompt_config_snapshot?.placeholders || [];

    setPlaceholders([newPlaceholders]);

    setModelConfig([
      {
        id: getRandomId(),
        ...(normalizeConfigurationForLoad(
          version?.prompt_config_snapshot?.configuration,
        ) || {
          ...modelConfigDefault,
        }),
      },
    ]);

    setResults([
      {
        output: version?.output?.map((opt, index) => ({
          id: getRandomId(),
          text: opt,
          metadata: {
            cost: version?.metadata?.[index]?.cost?.total_cost,
            tokens: version?.metadata?.[index]?.usage?.total_tokens,
            responseTime: version?.metadata?.[index]?.response_time,
          },
          outputFormat:
            version?.prompt_config_snapshot?.configuration?.output_format,
        })),
        isAnimating: false,
        id: getRandomId(),
      },
    ]);
  };

  const addToCompare = async () => {
    if (selectedVersions.length === 3) {
      enqueueSnackbar("You can only compare 3 prompts at a time", {
        variant: "info",
      });
      return;
    }

    const newIndex = selectedVersions.length;

    setPromptsByIndex(newIndex, {
      prompts: DefaultMessages.map((content) => ({
        ...content,
        id: getRandomId(),
      })),
      id: getRandomId(),
    });

    setPlaceholdersByIndex(newIndex, []);

    setModelConfigByIndex(newIndex, {
      id: getRandomId(),
      ...modelConfigDefault,
    });

    setResultsByIndex(newIndex, {
      output: [],
      isAnimating: false,
      id: getRandomId(),
      outputFormat: "string",
    });

    const versionId = getRandomId();

    const versionObject = {
      isDraft: true,
      version: null,
      lastSaved: new Date().toISOString(),
      isDefault: false,
      id: versionId,
    };

    setSelectedVersions((pre) => {
      const existing = [...pre];

      existing[newIndex] = {
        ...versionObject,
      };
      return existing;
    });

    //@ts-ignore
    const newDraftRes = await addDraftToPromptAsync(
      //@ts-ignore
      {
        new_prompts: [
          {
            variable_names: {},
            prompt_config: [
              {
                messages: [...DefaultMessages],
                configuration: { ...modelConfigDefault },
              },
            ],
            evaluation_configs: [],
          },
        ],
      },
    );

    const newVersion = newDraftRes.data.result?.[0];

    setSelectedVersionByIndex(newIndex, (p) => {
      return {
        ...p,
        version: newVersion?.template_version,
        lastSaved: newVersion?.updated_at,
        isDefault: newVersion?.is_default,
        isDraft: newVersion?.is_draft,
        id: versionId,
      };
    });

    // run the save which if remaining
    if (unsavedDrafts.current[newIndex]) {
      debouncedSaveOrRunPromptTemplate({
        index: newIndex,
        isRun: false,
      });
      unsavedDrafts.current[newIndex] = null;
    }
  };

  const applyCompare = (newCompareVersions) => {
    const newVersions = [];
    const newPrompts = [];
    const newPlaceholders = [];
    const newResults = [];
    const newModelConfigs = [];

    newCompareVersions.forEach((eachCompareVersions) => {
      newVersions.push({
        isDraft: eachCompareVersions.is_draft,
        version: eachCompareVersions.template_version,
        lastSaved: eachCompareVersions.updated_at,
        isDefault: eachCompareVersions.is_default,
        id: getRandomId(),
      });

      newPrompts.push({
        prompts: eachCompareVersions.prompt_config_snapshot.messages,
        id: getRandomId(),
      });

      const placeholderArray =
        eachCompareVersions.prompt_config_snapshot.placeholders || [];
      newPlaceholders.push(placeholderArray);

      newModelConfigs.push({
        id: getRandomId(),
        ...modelConfigDefault,
        ...normalizeConfigurationForLoad(
          eachCompareVersions.prompt_config_snapshot.configuration,
        ),
      });

      newResults.push({
        output: eachCompareVersions.output?.map((opt) => ({
          id: getRandomId(),
          text: opt,
          // audio:
          //   "https://fi-content-dev.s3.ap-south-1.amazonaws.com/tempcust/bbcb733d-5209-4710-8981-21d12bc1260a",
        })),
        isAnimating: false,
      });
    });

    setSelectedVersions((e) => [e[0], ...newVersions]);
    setPrompts((e) => [e[0], ...newPrompts]);
    setPlaceholders((e) => [e[0], ...newPlaceholders]);
    setModelConfig((e) => [e[0], ...newModelConfigs]);
    setResults((e) => [e[0], ...newResults]);
  };

  const removeFromCompare = (index) => {
    closeSocketByIndex(`compare-${index}`);
    closeSocketByIndex(`run-${index}`);
    setSelectedVersions((pre) => {
      const newPre = [...pre];
      newPre.splice(index, 1);
      return newPre;
    });
    setPrompts((pre) => {
      const newPre = [...pre];
      newPre.splice(index, 1);
      return newPre;
    });
    setPlaceholders((pre) => {
      const newPre = [...pre];
      newPre.splice(index, 1);
      return newPre;
    });
    setResults((pre) => {
      const newPre = [...pre];
      newPre.splice(index, 1);
      return newPre;
    });
    setModelConfig((pre) => {
      const newPre = [...pre];
      newPre.splice(index, 1);
      return newPre;
    });
    setLoadingStatus((pre) => {
      const newPre = [...pre];
      newPre.splice(index, 1);
      return newPre;
    });
    unsavedDrafts.current[index] = null;
  };

  const saveAndRun = (index) => {
    // If no index we run all
    if (index === undefined) {
      runningVersionIndexMapping.current = selectedVersions.reduce(
        (acc, version, index) => {
          acc[version.version] = index;
          return acc;
        },
        {},
      );
      setLoadingStatus(selectedVersions?.map(() => true));
      setSelectedVersions(
        selectedVersions?.map((version) => {
          if (!version.isDraft) {
            return { ...version };
          }
          return {
            ...version,
            isDraft: false,
          };
        }),
      );
      setResults(
        selectedVersions?.map(() => ({ output: [], isAnimating: true })),
      );
      if (selectedVersions.length > 1) {
        compareRun();
      } else {
        runPromptTemplate({ isRun: true, index: 0 });
      }
      return;
    }

    setLoadingStatusByIndex(index, true);
    setResultsByIndex(index, {
      output: [],
      isAnimating: true,
      outputFormat: "string",
    });
    setSelectedVersionByIndex(index, (pre) => {
      runningVersionIndexMapping.current = {
        ...(runningVersionIndexMapping.current || {}),
        [pre.version]: index,
      };
      if (!pre.isDraft) {
        return { ...pre };
      }
      return { ...pre, isDraft: false };
    });

    // saveOrRunPromptTemplate({ index, isRun: "prompt" });
    runPromptTemplate({ isRun: true, index });
  };

  const setVariableDataModified = useCallback(
    (...v) => {
      setVariableData(...v);
      const saveDraftIndexes = prompts.reduce(
        (acc, { prompts: currentPrompts }, i) => {
          const finalVariables = getVariables(currentPrompts, v[0], templateFormat);
          if (Object.keys(finalVariables).length) {
            acc.push(i);
          }
          return acc;
        },
        [],
      );

      saveAndDraftAll(saveDraftIndexes);
    },
    [saveAndDraftAll, setVariableData, prompts, templateFormat],
  );

  const setPlaceholdersDataModified = useCallback(
    (data) => {
      setPlaceholderData(data);
    },
    [setPlaceholderData],
  );

  const submitPlaceholders = useCallback(() => {
    const saveDraftIndexes = prompts.map((_, i) => i);
    saveAndDraftAll(saveDraftIndexes);
  }, [prompts, saveAndDraftAll]);

  return (
    <PromptWorkbenchContext.Provider
      value={{
        prompts,
        placeholders,
        currentTab,
        setCurrentTab,
        versionHistoryOpen,
        setVersionHistoryOpen,
        modelConfig,
        variableDrawerOpen,
        setVariableDrawerOpen,
        variableData,
        setVariableData: setVariableDataModified,
        placeholderData,
        setPlaceholderData: setPlaceholdersDataModified,
        originalPlaceholderData,
        setOriginalPlaceholderData,
        submitPlaceholders,
        valuesChanged,
        setValuesChanged,
        promptGeneratingStatus: loadingStatus,
        results,
        selectedVersions,
        setSelectedVersionByIndex,
        onRestoreVersion,
        openSelectModel,
        setOpenSelectModel,
        reset,
        loadingPrompt:
          (isLoading && isPending) || (compareIsLoading && compareIsPending),
        promptName,
        setPromptName,
        setPromptsByIndex: (index, valueOrUpdater) => {
          setPromptsByIndex(index, valueOrUpdater);
          saveAndDraft(index);
        },
        setPlaceholdersByIndex: (index, valueOrUpdater) => {
          setPlaceholdersByIndex(index, valueOrUpdater);
          saveAndDraft(index);
        },
        setModelConfigByIndex: (index, valueOrUpdater, options = {}) => {
          setModelConfigByIndex(index, valueOrUpdater);
          if (!options?.skipSave) {
            saveAndDraft(index);
          }
        },
        addToCompare,
        saveAndRun,
        setPrompts: (values, skipIndex) => {
          setPrompts(values);
          const saveDraftIndexes = selectedVersions.reduce((acc, _, idx) => {
            if (idx !== skipIndex) {
              acc.push(idx);
            }
            return acc;
          }, []);
          saveAndDraftAll(saveDraftIndexes);
        },
        setPlaceholders: (values, skipIndex) => {
          setPlaceholders(values);
          const saveDraftIndexes = selectedVersions.reduce((acc, _, idx) => {
            if (idx !== skipIndex) {
              acc.push(idx);
            }
            return acc;
          }, []);
          saveAndDraftAll(saveDraftIndexes);
        },
        removeFromCompare,
        isImportDatasetDrawerOpen,
        setImportDatasetDrawerOpen,
        applyCompare,
        isEmptyPrompt,
        setLoadingStatusByIndex,
        setLoadingStatus,
        isAddingDraft,
        getStreamingIds,
        setStreamingIdForVersion,
        pushStoppedIds,
        closeSocketByIndex,
        setModelConfig,
        templateFormat,
        setTemplateFormat: (v) => {
          templateFormatUserChanged.current = true;
          setTemplateFormat(v);
        },
      }}
    >
      {children}
    </PromptWorkbenchContext.Provider>
  );
};

WorkbenchProvider.propTypes = {
  children: PropTypes.any,
};

export default WorkbenchProvider;
