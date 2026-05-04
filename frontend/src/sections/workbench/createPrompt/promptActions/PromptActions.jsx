import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  Skeleton,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import React, { useMemo, useState, useRef, useEffect } from "react";
import { useNavigate, useParams } from "react-router";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import _ from "lodash";
import { format, isValid } from "date-fns";
import CustomTooltip from "src/components/tooltip";

import { usePromptWorkbenchContext } from "../WorkbenchContext";
import { useIsVariablesDefined } from "../hooks/use-is-variables-defined";
import { usePromptVersions } from "../hooks/use-prompt-versions";
import {
  changeVersion,
  checkContentIsEmpty,
  checkIfAudioModelHasAudioContent,
} from "../common";

import RunPromptButton from "./RunPromptButton";

import StopGeneratingButton from "./StopGeneratingButton";
import { enqueueSnackbar } from "src/components/snackbar";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { usePromptStore } from "../../../workbench-v2/store/usePromptStore";
import { NavLink } from "react-router-dom";
import { createDraftPayload } from "../../constant";
import { getMetricsTabSx } from "../Metrics/common";
import SaveAndCommit from "./SaveAndCommit";
import SvgColor from "src/components/svg-color";
import { DraftBadge } from "../SharedStyledComponents";
import VersionStyle from "./VersionStyle";
import MoreActions from "./MoreActions";
import { getColorMap as getTagColorMap } from "../VersionHistory/common";

const PromptActions = () => {
  const theme = useTheme();
  const { id } = useParams();
  const queryClient = useQueryClient();
  const [editName, setEdit] = useState(false);
  const [title, setTitle] = useState("");
  const { versions, refetch } = usePromptVersions(id);

  const [saveCommitOpen, setSaveCommitOpen] = useState(false);
  const { role: userRole } = useAuthContext();

  const {
    setOpenSelectModel,
    setVersionHistoryOpen,
    setVariableDrawerOpen,
    setCurrentTab,
    currentTab,
    selectedVersions,
    modelConfig,
    loadingPrompt,
    promptName,
    addToCompare,
    saveAndRun,
    isEmptyPrompt,
    prompts,
    variableData,
    setPromptName,
    promptGeneratingStatus,
    setLoadingStatus,
    isAddingDraft,
    getStreamingIds,
    setStreamingIdForVersion,
    pushStoppedIds,
    closeSocketByIndex,
    results,
    reset,
    templateFormat,
    // placeholders,
    // placeholderData,
  } = usePromptWorkbenchContext();
  const tabSx = useMemo(() => getMetricsTabSx(theme), [theme]);

  const timeoutRef = useRef(null);
  const navigate = useNavigate();
  const handleWritePrompt = () => {
    setCurrentTab("Playground");
    createDraft(createDraftPayload);
  };

  const { mutate: createDraft, isPending: isLoadingCreate } = useMutation({
    /**
     *
     * @param {Object} body
     * @returns
     */
    mutationFn: (body) =>
      axios.post(endpoints.develop.runPrompt.createPromptDraft, body),
    onSuccess: (data) => {
      enqueueSnackbar("Prompt created successfully.", {
        variant: "success",
      });
      reset();
      trackEvent(Events.promptTemplateCreated, {
        [PropertyName.click]: true,
        [PropertyName.promptId]: data?.data?.result?.rootTemplate,
      });
      navigate(
        `/dashboard/workbench/create/${data?.data?.result?.rootTemplate}`,
        { replace: true },
      );
    },
  });
  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  // const allPlaceholderLabels = Array.isArray(placeholders?.[0])
  //   ? placeholders.flat()
  //   : placeholders || [];

  // const filledPlaceholderLabels = Object.keys(placeholderData || {});

  // const areAllPlaceholdersPresent = allPlaceholderLabels.every((label) =>
  //   filledPlaceholderLabels.includes(label),
  // );

  const isVariablesDefined = useIsVariablesDefined(prompts, variableData, templateFormat);

  const isSingleVersion = selectedVersions.length === 1;

  const noModelIndex = modelConfig?.findIndex((m) => !m?.model);

  const buttonTooltip = useMemo(() => {
    if (isAddingDraft) {
      return "Creating new version...";
    }
    if (noModelIndex !== -1) {
      return "Select a model to Run Prompt";
    } else if (isEmptyPrompt?.length) {
      return "Please add content to the prompt";
    } else if (!isVariablesDefined) {
      return "Please define all variables";
    }
    // else if (!areAllPlaceholdersPresent) {
    //   return "Please fill all placeholders";
    // }
    return null;
  }, [
    isEmptyPrompt,
    isVariablesDefined,
    noModelIndex,
    isAddingDraft,
    // areAllPlaceholdersPresent,
  ]);

  const baseVersion = selectedVersions[0];
  const { isMoreOpen, setMoreOpen } = usePromptStore();

  useEffect(() => {
    if (baseVersion?.version) {
      refetch();
    }
  }, [baseVersion?.version, refetch]);

  const handleEdit = () => {
    setEdit(true);
    setTitle(promptName);
  };

  const resetEdit = () => {
    setEdit(false);
    setTitle("");
  };

  const handleSave = (e) => {
    e.preventDefault();
    if (!title.length) {
      enqueueSnackbar("Name cannot be empty", { variant: "error" });
      resetEdit();
      return;
    }
    changeName({ name: title });
  };

  const handleNameChange = (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (!title.length) {
        enqueueSnackbar("Name cannot be empty", { variant: "error" });
        resetEdit();
        return;
      }
      const data = { name: title };
      changeName(data);
      trackEvent(Events.promptRenamed, {
        [PropertyName.originalName]: title,
        [PropertyName.newName]: data,
        [PropertyName.propId]: id,
      });
      resetEdit();
    }
  };

  const { mutate: changeName } = useMutation({
    /**
     *
     * @param {Object} data
     * @returns
     */
    mutationFn: (data) => {
      return axios.post(endpoints.develop.runPrompt.getNameChange(id), data);
    },
    onSuccess: () => {
      resetEdit();
      queryClient.invalidateQueries({
        queryKey: ["prompt-latest-version"],
      });
      queryClient.invalidateQueries({
        queryKey: ["prompt-versions"],
      });

      // enqueueSnackbar("Name Changed successfully", { variant: "success" });
    },
    onMutate: ({ name }) => {
      setPromptName(name);
    },
  });

  const getDate = () => {
    if (!baseVersion?.lastSaved) {
      return "";
    }
    if (isValid(new Date(baseVersion?.lastSaved))) {
      return (
        "Last saved " +
        format(new Date(baseVersion?.lastSaved), "MMM d, yyyy 'at' h:mm a")
      );
    }
    return "";
  };

  const { mutate: stopGenerating, isPending: isStoppingGenerating } =
    useMutation({
      mutationFn: (versions) => {
        const session_uuid = getStreamingIds(versions);
        return axios.get(endpoints.develop.runPrompt.stopGenerating(id), {
          params: {
            session_uuid,
          },
          paramsSerializer: (params) => {
            return params.session_uuid
              .map((uuid) => `session_uuid=${encodeURIComponent(uuid)}`)
              .join("&");
          },
        });
      },
      onSuccess: (_, versions) => {
        if (timeoutRef.current) {
          clearTimeout(timeoutRef.current);
          timeoutRef.current = null;
        }
        const session_uuid = getStreamingIds(versions);
        pushStoppedIds(...session_uuid);
        timeoutRef.current = setTimeout(() => {
          versions.map((version) => {
            setStreamingIdForVersion(version, null);
          });
          setLoadingStatus((prev) => prev.map(() => false));
        }, 100);
      },
    });

  const onStopGenerating = () => {
    const versions = selectedVersions.reduce((acc, version, versionIdx) => {
      if (promptGeneratingStatus[versionIdx]) {
        closeSocketByIndex(`compare-${versionIdx}`);
        closeSocketByIndex(`run-${versionIdx}`);
        if (version.isDraft) {
          acc.push(changeVersion(version, "down"));
        } else {
          acc.push(version.version);
        }
      }
      return acc;
    }, []);
    //@ts-ignore
    stopGenerating(versions);
  };
  const isGenerating = promptGeneratingStatus?.some((v) => v);

  const isContentEmpty = useMemo(() => checkContentIsEmpty(results), [results]);
  const isAnyPromptDraft = useMemo(
    () => !!selectedVersions?.some((version) => version?.isDraft),
    [selectedVersions],
  );

  // const selectedVersion = useMemo(() => {
  //   return versions?.find(
  //     (ver) => ver?.templateVersion === baseVersion?.version,
  //   );
  //   // eslint-disable-next-line react-hooks/exhaustive-deps
  // }, [baseVersion, versions?.length]);
  return (
    <Stack width={"100%"}>
      <Box
        width="100%"
        display="flex"
        justifyContent={"space-between"}
        padding={2}
        paddingBottom={1.5}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-start",
            alignItems: "center",
            gap: "12px",
            flexGrow: 1,
          }}
        >
          <NavLink
            to="/dashboard/workbench"
            style={{
              textDecoration: "none",
              whiteSpace: "nowrap",
              color: theme.palette.text.secondary,
            }}
          >
            <Typography
              variant="M3"
              color={"text.secondary"}
              fontWeight={500}
              fontSize={16}
            >
              All Prompts
            </Typography>
          </NavLink>
          <span style={{ display: "inline-block", marginTop: "6px" }}>
            <Iconify
              icon="fluent:chevron-right-12-regular"
              width={24}
              height={24}
            />
          </span>

          <ShowComponent
            condition={RolePermission.PROMPTS[PERMISSIONS.CREATE][userRole]}
          >
            <CustomTooltip show title="Add New Prompt" arrow size="small">
              <Box
                sx={{
                  cursor: "pointer",
                  border: "1px solid",
                  borderColor: "divider",
                  color: "text.disabled",
                  borderRadius: theme.spacing(0.5),
                  padding: theme.spacing(0.5),
                  lineHeight: 0,
                  height: "32px",
                  width: "32px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
                onClick={handleWritePrompt}
              >
                <ShowComponent condition={!isLoadingCreate}>
                  <Iconify icon="eva:plus-fill" width="16px" height="16px" />
                </ShowComponent>
                <ShowComponent condition={isLoadingCreate}>
                  <CircularProgress size={16} sx={{ color: "text.primary" }} />
                </ShowComponent>
              </Box>
            </CustomTooltip>
          </ShowComponent>
          {!isLoadingCreate ? (
            <>
              <Box>
                <ShowComponent condition={editName}>
                  <TextField
                    value={title}
                    size="small"
                    onChange={(e) => setTitle(e.target.value)}
                    onKeyDown={handleNameChange}
                    onBlur={handleSave}
                    autoFocus
                    InputProps={{ disableUnderline: true }}
                    sx={{
                      maxWidth: "200px",
                      flexGrow: 1,
                      fontSize: "16px",
                      "& .MuiInputBase-input": {
                        padding: "0px 8px",
                        border: "1px solid",
                        borderColor: "divider",
                      },
                    }}
                  />
                </ShowComponent>
                <ShowComponent condition={!editName}>
                  <CustomTooltip
                    show={promptName?.length > 30}
                    title={promptName}
                  >
                    <Typography
                      typography="M3"
                      fontWeight={"fontWeightMedium"}
                      color="text.primary"
                      sx={{
                        cursor: promptName?.length > 30 ? "pointer" : "cursor",
                      }}
                      width={"auto"}
                      whiteSpace={"nowrap"}
                    >
                      {promptName?.length > 30
                        ? `${promptName?.slice(0, 30)}..`
                        : `${promptName}`}
                    </Typography>
                  </CustomTooltip>
                </ShowComponent>

                <Typography
                  typography="s3"
                  fontWeight={"fontWeightRegular"}
                  color={
                    currentTab === "Metrics" ? "text.primary" : "text.disabled"
                  }
                >
                  {currentTab === "Metrics"
                    ? "These metrics are only for linked versions"
                    : getDate()}
                </Typography>
              </Box>
              <ShowComponent
                condition={isSingleVersion || currentTab === "Evaluation"}
              >
                {baseVersion?.version ? (
                  <VersionStyle text={baseVersion?.version} />
                ) : (
                  <Skeleton
                    variant="rectangular"
                    width={25}
                    height={23}
                    sx={{ borderRadius: "2px" }}
                  />
                )}
                {baseVersion?.isDraft ? <DraftBadge>Draft</DraftBadge> : null}
              </ShowComponent>

              {(() => {
                const versionLabels =
                  baseVersion?.labels?.length > 0
                    ? baseVersion.labels
                    : versions?.find(
                        (v) =>
                          v.template_version === baseVersion?.version ||
                          v.template_version === baseVersion?.templateVersion,
                      )?.labels;
                return versionLabels?.length > 0
                  ? versionLabels.map((label) => (
                      <Chip
                        key={label.id}
                        label={label.name}
                        size="small"
                        sx={{
                          backgroundColor: getTagColorMap(label?.name, theme)
                            ?.backgroundColor,
                          color: getTagColorMap(label?.name, theme)?.color,
                          borderRadius: "100px",
                          typography: "s3",
                          fontWeight: "fontWeightMedium",
                          height: 24,
                          "&:hover": {
                            backgroundColor: getTagColorMap(label?.name, theme)
                              ?.backgroundColor,
                            cursor: "default",
                          },
                        }}
                      />
                    ))
                  : null;
              })()}

              {!editName &&
                !isLoadingCreate &&
                RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole] && (
                  <span style={{ display: "inline-block" }}>
                    <IconButton
                      onClick={handleEdit}
                      sx={{
                        padding: 0.5,
                      }}
                    >
                      <SvgColor
                        src="/assets/icons/ic_edit_pencil.svg"
                        sx={{
                          width: 16,
                          height: 16,
                        }}
                      />
                    </IconButton>
                  </span>
                )}
            </>
          ) : (
            <Skeleton
              variant="rectangular"
              width={300}
              height={30}
              sx={{ borderRadius: "6px" }}
            />
          )}
        </Box>

        <Box display="flex" gap={theme.spacing(1.5)} alignItems={"flex-start"}>
          {/* Hide More Actions, Variables, and Run Prompt on Simulation tab */}
          <ShowComponent condition={currentTab !== "Simulation"}>
            <MoreActions
              isMoreOpen={isMoreOpen}
              setMoreOpen={setMoreOpen}
              loadingPrompt={loadingPrompt}
              currentTab={currentTab}
              setVersionHistoryOpen={setVersionHistoryOpen}
              trackEvent={trackEvent}
              Events={Events}
              PropertyName={PropertyName}
              id={id}
              isAnyPromptDraft={isAnyPromptDraft}
              selectedVersions={selectedVersions}
              setSaveCommitOpen={setSaveCommitOpen}
              theme={theme}
              isAddingDraft={isAddingDraft}
              addToCompare={addToCompare}
              userRole={userRole}
            />
            <Divider
              orientation="vertical"
              flexItem
              sx={{
                height: "30px",
                borderColor: "divider",
                marginBottom: 1,
                alignSelf: "center",
              }}
            />

            <CustomTooltip
              show
              title="Add and manage data points that can be inserted into prompt for flexible and reusable setups"
              arrow
              size="small"
              type="black"
              slotProps={{
                tooltip: {
                  sx: {
                    maxWidth: "200px !important",
                  },
                },
              }}
            >
              <Button
                sx={{
                  borderRadius: "4px",
                  backgroundColor: "background.paper",
                  border: "1px solid",
                  borderColor: "divider",
                  height: "30px",
                  "&:hover": {
                    backgroundColor: `${theme.palette.background.neutral} !important`,
                  },
                }}
                disabled={
                  loadingPrompt ||
                  currentTab === "Evaluation" ||
                  currentTab === "Metrics"
                }
                onClick={() => {
                  setVariableDrawerOpen(true);
                  trackEvent(Events.promptVariablesClicked, {
                    [PropertyName.method]: PropertyName.click,
                    [PropertyName.promptId]: id,
                  });
                }}
                startIcon={
                  <SvgColor
                    sx={{
                      width: "16px",
                      cursor: "pointer",
                      color:
                        loadingPrompt ||
                        currentTab === "Evaluation" ||
                        currentTab === "Metrics"
                          ? "divider"
                          : "text.primary",
                    }}
                    src="/assets/icons/ic_variables.svg"
                  />
                }
              >
                <Typography variant="s1" fontWeight={"fontWeightMedium"}>
                  Variables
                </Typography>
              </Button>
            </CustomTooltip>
            <CustomTooltip
              show={Boolean(buttonTooltip)}
              title={buttonTooltip || ""}
              arrow
            >
              <ShowComponent condition={isGenerating}>
                <StopGeneratingButton
                  disabled={loadingPrompt}
                  onClick={onStopGenerating}
                  loading={isStoppingGenerating}
                >
                  <Typography variant="s1" fontWeight={"fontWeightMedium"}>
                    Stop Generating
                  </Typography>
                </StopGeneratingButton>
              </ShowComponent>
              <ShowComponent condition={!isGenerating}>
                <RunPromptButton
                  disabled={
                    loadingPrompt ||
                    currentTab === "Evaluation" ||
                    currentTab === "Metrics" ||
                    !RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]
                  }
                  onClick={() => {
                    if (buttonTooltip) {
                      if (noModelIndex !== -1) {
                        setOpenSelectModel(noModelIndex);
                        trackEvent(Events.promptSelectModelClicked, {
                          [PropertyName.promptId]: id,
                          [PropertyName.type]: "system",
                          [PropertyName.version]: selectedVersions?.map(
                            (item) => item?.version,
                          ),
                        });
                      } else if (!isVariablesDefined) {
                        setVariableDrawerOpen(true);
                      }
                      return;
                    }
                    trackEvent(Events.promptRunPromptClicked, {
                      [PropertyName.promptId]: id,
                      [PropertyName.type]: "overall",
                      [PropertyName.version]: selectedVersions?.map(
                        (item) => item?.version,
                      ),
                    });
                    if (
                      !checkIfAudioModelHasAudioContent(modelConfig, prompts)
                    ) {
                      enqueueSnackbar(
                        "Audio input is missing. Please add audio before running the prompt.",
                        { variant: "error" },
                      );
                      return;
                    }
                    saveAndRun();
                  }}
                >
                  {" "}
                  <Typography variant="s1" fontWeight={"fontWeightMedium"}>
                    Run Prompt
                  </Typography>
                </RunPromptButton>
              </ShowComponent>
            </CustomTooltip>
          </ShowComponent>
        </Box>
      </Box>

      <Divider
        sx={{
          borderColor: "divider",
          marginBottom: 0.5,
          marginRight: "-16px",
          marginLeft: "-16px",
        }}
      />

      <Box sx={{ paddingX: "16px", marginBottom: "8px" }}>
        <Tabs
          textColor="primary"
          value={currentTab}
          onChange={(e, value) => {
            // Prevent tab change if conditions aren't met
            if (
              (value === "Evaluation" ||
                value === "Metrics" ||
                value === "Simulation") &&
              (versions.length === 0 ||
                isAnyPromptDraft ||
                isContentEmpty ||
                isGenerating)
            ) {
              return;
            }

            setCurrentTab(value);
            trackEvent(Events.promptEvaluationToggled, {
              [PropertyName.promptId]: id,
              value: value,
            });
          }}
          TabIndicatorProps={{
            style: {
              backgroundColor: theme.palette.primary.main,
              height: "2px",
              borderRadius: "20px",
            },
          }}
          sx={{ ...tabSx, borderColor: "divider" }}
        >
          <Tab
            icon={<SvgColor src={"/assets/icons/navbar/ic_evaluate.svg"} />}
            iconPosition="start"
            label={"Playground"}
            value={"Playground"}
          />

          <Tab
            icon={<SvgColor src={"/assets/icons/navbar/ic_eval.svg"} />}
            iconPosition="start"
            label={
              versions.length === 0 ||
              isAnyPromptDraft ||
              isContentEmpty ||
              isGenerating ? (
                <CustomTooltip
                  size="small"
                  title={
                    versions.length === 0
                      ? "You need to submit at least one prompt and get an output before accessing the evaluation."
                      : "Run the prompt to evaluate the responses"
                  }
                  show
                  arrow
                >
                  <span>Evaluation</span>
                </CustomTooltip>
              ) : (
                "Evaluation"
              )
            }
            value={"Evaluation"}
          />

          <Tab
            icon={<SvgColor src={"/assets/icons/ic_metrics.svg"} />}
            iconPosition="start"
            label={
              versions.length === 0 ||
              isAnyPromptDraft ||
              isContentEmpty ||
              isGenerating ? (
                <CustomTooltip
                  size="small"
                  title={
                    versions.length === 0
                      ? "You need to submit at least one prompt and get an output before accessing the metrics."
                      : "Run the prompt to view metrics"
                  }
                  show
                  arrow
                >
                  <span>Metrics</span>
                </CustomTooltip>
              ) : (
                "Metrics"
              )
            }
            value="Metrics"
          />

          <Tab
            icon={
              <Iconify
                icon="mdi:chat-processing-outline"
                width={18}
                height={18}
              />
            }
            iconPosition="start"
            label={
              versions.length === 0 ||
              isAnyPromptDraft ||
              isContentEmpty ||
              isGenerating ? (
                <CustomTooltip
                  size="small"
                  title={
                    versions.length === 0
                      ? "You need to submit at least one prompt before running simulations."
                      : "Save your prompt to run simulations"
                  }
                  show
                  arrow
                >
                  <span>Simulation</span>
                </CustomTooltip>
              ) : (
                "Simulation"
              )
            }
            value="Simulation"
          />
        </Tabs>
      </Box>
      <SaveAndCommit
        open={saveCommitOpen}
        onClose={() => setSaveCommitOpen(false)}
        data={baseVersion}
        promptName={promptName}
      />
    </Stack>
  );
};

export default PromptActions;

PromptActions.propTypes = {};
