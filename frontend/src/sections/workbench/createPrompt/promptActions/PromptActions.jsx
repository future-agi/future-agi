import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  Popover,
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
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";

import { usePromptWorkbenchContext } from "../WorkbenchContext";
import { useIsVariablesDefined } from "../hooks/use-is-variables-defined";
import { useExtractAllVariables } from "../hooks/use-extract-all-variables";
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
import { useRestrictSimulateToEnvironments } from "src/hooks/useRestrictSimulateToEnvironments";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { usePromptStore } from "../../../workbench-v2/store/usePromptStore";
import { NavLink } from "react-router-dom";
import { createDraftPayload } from "../../constant";
import { getMetricsTabSx } from "../Metrics/common";
import SaveAndCommit from "./SaveAndCommit";
import { useRunOnDraftGuard } from "./SaveDraftFirstDialog";
import SvgColor from "src/components/svg-color";
import { DraftBadge } from "../SharedStyledComponents";
import VersionStyle from "./VersionStyle";
import MoreActions from "./MoreActions";
import RunOnRowsButton from "src/sections/common/RunSources/RunOnRowsButton"; // R4-F frozen line
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
  const restrictSimulateToEnvironments = useRestrictSimulateToEnvironments();

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
    setVariableData,
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
        [PropertyName.promptId]: data?.data?.result?.root_template,
      });
      navigate(
        `/dashboard/workbench/create/${data?.data?.result?.root_template}`,
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

  const isVariablesDefined = useIsVariablesDefined(
    prompts,
    variableData,
    templateFormat,
  );

  const isSingleVersion = selectedVersions.length === 1;

  // IT-R4 G4 P3: a run with an empty variable asks first (`Not run yet ·
  // {{x}} has no value, so lookup_order would be called with no x.
  // [Fill it] [Run anyway]`).
  const allVariables = useExtractAllVariables(prompts, templateFormat);
  const [emptyAsk, setEmptyAsk] = useState(null); // {anchor, names}
  const emptyVariables = (allVariables ?? []).filter((n) => {
    const v = variableData?.[n];
    return !(Array.isArray(v) ? v : [v]).some(
      (x) => x != null && String(x).trim(),
    );
  });
  const toolNames = (modelConfig?.[0]?.tools ?? [])
    .map((t) => t?.function?.name ?? t?.name)
    .filter(Boolean);
  const emptyLead = (names) =>
    `{{${names[0]}}}${names.length > 1 ? ` and ${names.length - 1} more` : ""} ${names.length > 1 ? "have" : "has"} no value`;
  // Run anyway defines a missing name as "" and the server fills {{name}}
  // with it (nothing is evaluated), so the run leaves it blank.
  const emptySentence = (names) => {
    const first = names[0];
    const what = first.replace(/_/g, " ");
    const lead = emptyLead(names);
    return toolNames.length
      ? `${lead}, so ${toolNames[0]} would be called with no ${what}.`
      : `${lead}. Fill it, or Run anyway and the run leaves ${names.length > 1 ? "them" : "it"} blank.`;
  };
  // Walk row 15: Escape, a click outside the box or a second press on Run
  // Prompt (it lands on the box's invisible backdrop) closed the box with
  // nothing sent and nothing said. Every way out other than its two buttons
  // now says that nothing ran and what to do.
  const closeEmptyAsk = () => {
    if (!emptyAsk) return; // already closing
    const names = emptyAsk.names ?? [];
    setEmptyAsk(null);
    enqueueSnackbar(
      names.length
        ? `Not run: ${emptyLead(names)}. Type a value under Variables, or press Run Prompt and choose Run anyway to send ${names.length > 1 ? "them" : "it"} blank.`
        : "Not run. Press Run Prompt again to run.",
      { variant: "info" },
    );
  };
  const fillFirstEmpty = () => {
    const name = emptyAsk?.names?.[0];
    setEmptyAsk(null);
    // Compared as text, not built into a selector: a name with a quote or a
    // backslash made querySelector throw after the box had closed.
    const el =
      name == null
        ? null
        : Array.from(
            document.querySelectorAll('[data-testid^="variable-value-"]'),
          ).find(
            (node) =>
              node.getAttribute("data-testid") === `variable-value-${name}`,
          );
    // After the popover has handed focus back to Run.
    if (el) setTimeout(() => el.focus(), 200);
    else setVariableDrawerOpen(true);
  };

  const noModelIndex = modelConfig?.findIndex((m) => !m?.model);

  const buttonTooltip = useMemo(() => {
    if (!RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]) {
      return "You don't have permission to run prompts.";
    }
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
    userRole,
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
  // IT-R2 G4 P2: Commit works once the prompt has content (no run needed);
  // when it can't, the tooltip says why.
  const commitBlocked =
    selectedVersions.length > 1
      ? "To commit one of these versions, use the commit icon on its column."
      : isEmptyPrompt?.includes(0)
        ? "Write a message first, then commit it as a version."
        : !baseVersion?.version
          ? "Saving your draft…"
          : null;
  const disableCommit = Boolean(commitBlocked);
  // The stored version (prompt history) says whether it is still a draft:
  // a run alone does not save a version for experiments.
  const storedDraft = Boolean(
    versions?.find((v) => v?.template_version === baseVersion?.version)
      ?.is_draft,
  );
  const runGuard = useRunOnDraftGuard({
    promptId: id,
    version: baseVersion?.version,
    isDraft: Boolean(baseVersion?.isDraft) || storedDraft,
  });

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
        // At every width, when the name, chips and actions do not fit on one
        // row, the actions move to their own right-aligned row instead of
        // squeezing the name to nothing or covering the Draft chip (a forced
        // single row lost the name at 1280 px with More open on a draft).
        sx={{
          flexWrap: "wrap",
          columnGap: 1.5,
          rowGap: 1,
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-start",
            alignItems: "center",
            gap: "12px",
            flexGrow: 1,
            // IT-R1 G4: on its own row the title still gives way (ellipsis)
            // when the name is very long.
            minWidth: 0,
            // On a phone (below sm) the name, version,
            // chips and rename button wrap onto a second line instead of
            // squeezing the name to nothing; from sm up they stay on one line.
            flexWrap: "wrap",
            rowGap: 0.5,
            [theme.breakpoints.up("sm")]: { flexWrap: "nowrap" },
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
              icon="solar:alt-arrow-right-linear"
              width={16}
              height={16}
            />
          </span>

          <ShowComponent
            condition={RolePermission.PROMPTS[PERMISSIONS.CREATE][userRole]}
          >
            <CustomTooltip show title="Add New Prompt" arrow size="small">
              <IconButton
                size="small"
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  // An enabled control, readable in both
                  // themes (text.disabled made it look disabled).
                  color: "text.secondary",
                  flexShrink: 0,
                }}
                onClick={handleWritePrompt}
                aria-label="Add new prompt"
              >
                <ShowComponent condition={!isLoadingCreate}>
                  <Iconify icon="solar:add-linear" width="16px" height="16px" />
                </ShowComponent>
                <ShowComponent condition={isLoadingCreate}>
                  <CircularProgress size={16} sx={{ color: "text.primary" }} />
                </ShowComponent>
              </IconButton>
            </CustomTooltip>
          </ShowComponent>
          {!isLoadingCreate ? (
            <>
              <Box sx={{ minWidth: 0, overflow: "hidden" }}>
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
                  {/* The name is cut by width only (about 30 characters at
                      most) and ends in an ellipsis; whenever it is cut, a
                      hover shows the full name. */}
                  <OverflowTooltip title={promptName}>
                    {/* RS-13: the prompt's name is the page's one h1. */}
                    <Typography
                      component="h1"
                      typography="M3"
                      fontWeight={"fontWeightMedium"}
                      color="text.primary"
                      sx={{
                        m: 0,
                        maxWidth: "24ch",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                      width={"auto"}
                      whiteSpace={"nowrap"}
                    >
                      {`${promptName}`}
                    </Typography>
                  </OverflowTooltip>
                </ShowComponent>

                <Typography
                  typography="s3"
                  fontWeight={"fontWeightRegular"}
                  noWrap
                  color={
                    currentTab === "Metrics" ? "text.primary" : "text.tertiary"
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
                    sx={{ borderRadius: "4px" }}
                  />
                )}
                {baseVersion?.isDraft ? <DraftBadge>Draft</DraftBadge> : null}
              </ShowComponent>

              {(() => {
                if (!isSingleVersion) {
                  return null;
                }
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
                          typography: "s3",
                          "&:hover": {
                            backgroundColor: getTagColorMap(label?.name, theme)
                              ?.hoverBackgroundColor,
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
                    <IconButton onClick={handleEdit} aria-label="Rename prompt">
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

        <Box
          display="flex"
          gap={theme.spacing(1.5)}
          alignItems={"flex-start"}
          sx={{
            ml: "auto",
            // Below lg the actions wrap onto more rows
            // (right-aligned) when the screen is narrower than all of them,
            // so Commit, Variables and Run Prompt stay on screen at 390 px.
            // From lg up they stay on one row, as before.
            minWidth: 0,
            maxWidth: "100%",
            flexWrap: "wrap",
            justifyContent: "flex-end",
            rowGap: 1,
            [theme.breakpoints.up("lg")]: { flexShrink: 0, flexWrap: "nowrap" },
          }}
          onClickCapture={runGuard.onClickCapture}
        >
          {/* Hide More Actions, Variables, and Run Prompt on Simulation tab */}
          <ShowComponent condition={currentTab !== "Simulation"}>
            <ShowComponent
              condition={RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]}
            >
              <CustomTooltip
                show
                title={
                  commitBlocked ??
                  "Save this prompt as a version (its tools and variables too), so experiments can run it."
                }
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
                <span style={{ display: "inline-block" }}>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    sx={{
                      whiteSpace: "nowrap",
                    }}
                    disabled={
                      disableCommit ||
                      currentTab === "Evaluation" ||
                      currentTab === "Metrics"
                    }
                    onClick={() => {
                      setSaveCommitOpen(true);
                      trackEvent(Events.promptCommitClicked, {
                        [PropertyName.promptId]: id,
                      });
                    }}
                    startIcon={
                      <SvgColor width={16} src="/assets/icons/ic_commit.svg" />
                    }
                  >
                    <Typography variant="s1" fontWeight={"fontWeightMedium"}>
                      Commit
                    </Typography>
                  </Button>
                </span>
              </CustomTooltip>
            </ShowComponent>
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
              selectedVersions={selectedVersions}
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
                size="small"
                sx={{
                  backgroundColor: "background.paper",
                  border: "1px solid",
                  borderColor: "divider",
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
            {/* IT-R4 G4 P2: the tooltip wrapper stays mounted (an empty
                title shows nothing). Toggling `show` swapped a Fragment for
                a Tooltip under the pointer: the click that blurred a
                variable's input (committing its value) re-mounted the
                button between mousedown and mouseup, so Run did nothing. */}
            <CustomTooltip show title={buttonTooltip || ""} arrow>
              <span style={{ display: "inline-flex" }}>
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
                    data-testid="run-prompt"
                    disabled={
                      loadingPrompt ||
                      currentTab === "Evaluation" ||
                      currentTab === "Metrics" ||
                      !RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]
                    }
                    onClick={(e) => {
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
                          // IT-R4 G4 P3: ask in place, not a full-screen
                          // drawer.
                          setEmptyAsk({
                            anchor: e.currentTarget,
                            names: emptyVariables,
                          });
                        } else {
                          // IT-R4 G4 P2: never a silent click.
                          enqueueSnackbar(
                            `Couldn't run yet: ${buttonTooltip}`,
                            {
                              variant: "info",
                            },
                          );
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
                      if (emptyVariables.length && !emptyAsk) {
                        setEmptyAsk({
                          anchor: e.currentTarget,
                          names: emptyVariables,
                        });
                        return;
                      }
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
              </span>
            </CustomTooltip>
            <Popover
              open={Boolean(emptyAsk)}
              anchorEl={emptyAsk?.anchor ?? null}
              onClose={closeEmptyAsk}
              anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
              transformOrigin={{ vertical: "top", horizontal: "right" }}
              slotProps={{
                paper: {
                  role: "alertdialog",
                  "aria-labelledby": "run-empty-variables-title",
                  "aria-describedby": "run-empty-variables-text",
                  sx: { mt: 0.5, p: 1.5, maxWidth: 340 },
                },
              }}
            >
              <Stack gap={1} data-testid="run-empty-variables">
                <Typography
                  id="run-empty-variables-title"
                  typography="s1"
                  fontWeight="fontWeightMedium"
                >
                  Not run yet
                </Typography>
                <Typography id="run-empty-variables-text" typography="s2">
                  {emptyAsk ? emptySentence(emptyAsk.names) : ""}
                </Typography>
                <Stack direction="row" gap={1} justifyContent="flex-end">
                  <Button size="small" variant="text" onClick={fillFirstEmpty}>
                    Fill it
                  </Button>
                  <Button
                    size="small"
                    variant="contained"
                    onClick={() => {
                      setEmptyAsk(null);
                      if (!isVariablesDefined) {
                        // Define the missing names (empty) so the run
                        // leaves them blank.
                        const next = { ...(variableData ?? {}) };
                        (allVariables ?? []).forEach((n) => {
                          if (!(n in next)) next[n] = [""];
                        });
                        setVariableData(next);
                        setTimeout(() => saveAndRun(), 0);
                      } else saveAndRun();
                    }}
                  >
                    Run anyway
                  </Button>
                </Stack>
              </Stack>
            </Popover>
            {/* R4-F frozen line: Run on a dataset… (D24; lane Rb fills it) */}
            <RunOnRowsButton
              source="prompt"
              promptId={id}
              promptVersionId={runGuard.savedVersion ?? baseVersion?.version}
              label="Run on a dataset…"
              variant="text"
            />
          </ShowComponent>
        </Box>
      </Box>

      <Divider
        sx={{
          borderColor: "divider",
          marginBottom: 0.5,
        }}
      />

      <Box sx={{ paddingX: "16px", marginBottom: "8px" }}>
        <Tabs
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
          sx={tabSx}
        >
          <Tab
            icon={
              <SvgColor
                width={16}
                src={"/assets/icons/navbar/ic_evaluate.svg"}
              />
            }
            iconPosition="start"
            label={"Playground"}
            value={"Playground"}
          />

          <Tab
            icon={
              <SvgColor width={16} src={"/assets/icons/navbar/ic_eval.svg"} />
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
            icon={<SvgColor width={16} src={"/assets/icons/ic_metrics.svg"} />}
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

          {!restrictSimulateToEnvironments && (
            <Tab
              icon={
                <Iconify
                  icon="solar:chat-round-line-linear"
                  width={16}
                  height={16}
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
          )}
        </Tabs>
      </Box>
      <SaveAndCommit
        open={saveCommitOpen}
        onClose={() => setSaveCommitOpen(false)}
        data={baseVersion}
        promptName={promptName}
        // WLA-R2-01: a run also saves the version (is_draft false), so the
        // name is asked until a version has a commit message (what Commit
        // History lists).
        askName={!versions?.some((v) => Boolean(v?.commit_message))}
      />
      {runGuard.dialog}
    </Stack>
  );
};

export default PromptActions;

PromptActions.propTypes = {};
