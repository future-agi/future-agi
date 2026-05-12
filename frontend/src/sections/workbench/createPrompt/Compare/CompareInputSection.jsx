import {
  Box,
  Button,
  IconButton,
  Skeleton,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useRef, useEffect } from "react";
import { format, isValid } from "date-fns";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";

import { useIsVariablesDefined } from "../hooks/use-is-variables-defined";
import VersionStyle from "../promptActions/VersionStyle";
import PromptSection from "../Playground/PromptSection";
import { usePromptWorkbenchContext } from "../WorkbenchContext";
import StopGeneratingButton from "../promptActions/StopGeneratingButton";
import { changeVersion, checkIfAudioModelHasAudioContent } from "../common";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { enqueueSnackbar } from "notistack";

const getDate = (lastSaved) => {
  if (!lastSaved) {
    return "";
  }
  if (isValid(new Date(lastSaved))) {
    return (
      "Last saved " + format(new Date(lastSaved), "MMM d, yyyy 'at' h:mm a")
    );
  }
  return "";
};

const CompareInputSection = ({
  index,
  promptVersion,
  setSaveCommitOpen,
  isSync,
  setIsSync,
  syncSystemPrompt,
}) => {
  const {
    prompts,
    setPromptsByIndex,
    placeholders,
    // placeholderData,
    setPlaceholdersByIndex,
    modelConfig,
    setModelConfigByIndex,
    selectedVersions,
    saveAndRun,
    removeFromCompare,
    setOpenSelectModel,
    isEmptyPrompt,
    variableData,
    setVariableDrawerOpen,
    promptGeneratingStatus,
    setLoadingStatusByIndex,
    isAddingDraft,
    getStreamingIds,
    setStreamingIdForVersion,
    pushStoppedIds,
    closeSocketByIndex,
    templateFormat,
  } = usePromptWorkbenchContext();

  const timeoutRef = useRef(null);

  const { role: userRole } = useAuthContext();

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  // const currentPlaceholderLabels = placeholders[index] || [];

  // const filledPlaceholderLabels = Object.keys(placeholderData || {});

  // const areAllCurrentPlaceholdersPresent = currentPlaceholderLabels.every(
  //   (label) => filledPlaceholderLabels.includes(label),
  // );

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
        }
        const session_uuid = getStreamingIds(versions);
        pushStoppedIds(...session_uuid);
        timeoutRef.current = setTimeout(() => {
          versions.map((version) => {
            setStreamingIdForVersion(version, null);
          });
          setLoadingStatusByIndex(index, false);
        }, 100);
      },
    });

  const onStopGenerating = () => {
    closeSocketByIndex(`compare-${index}`);
    closeSocketByIndex(`run-${index}`);
    if (promptVersion.isDraft) {
      //@ts-ignore
      stopGenerating([changeVersion(promptVersion, "down")]);
    } else {
      //@ts-ignore
      stopGenerating([promptVersion?.version]);
    }
  };

  const theme = useTheme();

  const { id } = useParams();

  const currentPrompts = prompts?.filter((_, i) => i === index);

  const isGenerating = promptGeneratingStatus?.[index];

  const isVariablesDefined = useIsVariablesDefined(
    currentPrompts,
    variableData,
    templateFormat,
  );

  let buttonTooltip = null;
  if (isAddingDraft) {
    buttonTooltip = "Creating new version...";
  } else if (!modelConfig?.[index]?.model) {
    buttonTooltip = "Select a model to Run Prompt";
  } else if (isEmptyPrompt?.includes(index)) {
    buttonTooltip = "Please add content to the prompt";
  } else if (!isVariablesDefined) {
    buttonTooltip = "Please define all variables";
  }
  // else if (!areAllCurrentPlaceholdersPresent) {
  //   buttonTooltip = "Please fill all placeholders";
  // }

  return (
    <Box
      key={id}
      sx={{
        flex: 1,
        borderRightStyle: "solid",
        borderRightColor: "divider",
        borderRightWidth: index < selectedVersions.length - 1 ? "1px" : "0px",
        paddingRight: index < selectedVersions.length - 1 ? 2 : 0,
        paddingBottom: 2,
        overflowY: "hidden",
        height: "100%",
        gap: 2,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            {promptVersion?.version ? (
              <VersionStyle index={index} text={promptVersion?.version} />
            ) : (
              <Skeleton
                variant="rectangular"
                width={25}
                height={23}
                sx={{ borderRadius: "2px" }}
              />
            )}
            {promptVersion?.isDraft ? (
              <Typography
                typography="s3"
                fontWeight={"fontWeightMedium"}
                sx={{
                  backgroundColor: "orange.o10",
                  borderRadius: "2px",
                  color: "text.primary",
                  paddingX: theme.spacing(0.75),
                  paddingTop: theme.spacing(0.5),
                  paddingBottom: theme.spacing(0.375),
                }}
              >
                Draft
              </Typography>
            ) : null}
          </Box>
          <Typography
            typography="s3"
            fontWeight={"fontWeightRegular"}
            color="text.disabled"
          >
            {getDate(promptVersion?.lastSaved)}
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <CustomTooltip
            title={
              promptVersion?.isDraft
                ? "Please run the prompt before saving and committing"
                : "Commit Changes"
            }
            show={true}
            arrow
            size="small"
          >
            <IconButton
              sx={{
                borderRadius: "4px",
                backgroundColor: "background.paper",
                border: "1px solid",
                borderColor: "divider",
                height: "24px",
                padding: 0.5,
              }}
              disabled={!RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]}
              onClick={() => {
                if (promptVersion?.isDraft || !promptVersion?.version) return;
                setSaveCommitOpen({
                  version: promptVersion?.version,
                  isDefault: promptVersion?.isDefault,
                  isDraft: promptVersion?.isDraft,
                });
              }}
            >
              <SvgColor
                src="/assets/icons/ic_commit.svg"
                sx={{
                  height: "16px",
                  width: "16px",
                  color: "text.disabled",
                }}
              />
            </IconButton>
          </CustomTooltip>
          <ShowComponent condition={isGenerating}>
            <StopGeneratingButton
              sx={{
                lineHeight: 1.5,
                borderRadius: 0.5,
                height: "24px",
              }}
              onClick={onStopGenerating}
              loading={isStoppingGenerating}
            >
              Stop Generating
            </StopGeneratingButton>
          </ShowComponent>
          <ShowComponent condition={!isGenerating}>
            <CustomTooltip
              show={Boolean(buttonTooltip)}
              title={buttonTooltip}
              arrow
            >
              <Button
                variant="outlined"
                color="primary"
                size="small"
                sx={{
                  lineHeight: 1.5,
                  borderRadius: 0.5,
                  height: "24px",
                  width: "56px",
                }}
                disabled={!RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]}
                startIcon={
                  <SvgColor
                    src="/assets/icons/navbar/ic_get_started.svg"
                    sx={{ width: "16px", height: "16px" }}
                  />
                }
                onClick={() => {
                  if (buttonTooltip) {
                    if (!modelConfig?.[index]?.model) {
                      setOpenSelectModel(index);
                      trackEvent(Events.promptSelectModelClicked, {
                        [PropertyName.promptId]: id,
                        [PropertyName.type]: "system",
                        [PropertyName.version]:
                          selectedVersions?.[index]?.version,
                      });
                    } else if (!isVariablesDefined) {
                      setVariableDrawerOpen(true);
                    }
                    // else if (!areAllCurrentPlaceholdersPresent) {
                    //   setVariableDrawerOpen(true);
                    //   return;
                    // }
                    return;
                  }

                  if (
                    !checkIfAudioModelHasAudioContent(
                      modelConfig,
                      prompts,
                      index,
                    )
                  ) {
                    enqueueSnackbar(
                      "Audio input is missing. Please add audio before running the prompt.",
                      { variant: "error" },
                    );
                    return;
                  }

                  trackEvent(Events.promptRunPromptClicked, {
                    [PropertyName.promptId]: id,
                    [PropertyName.type]: "individual",
                    [PropertyName.version]: selectedVersions?.[index]?.version,
                  });
                  saveAndRun(index);
                }}
              >
                Run
              </Button>
            </CustomTooltip>
          </ShowComponent>

          <IconButton
            onClick={() => removeFromCompare(index)}
            sx={{
              padding: 0,
            }}
          >
            <SvgColor
              src="/assets/icons/ic_delete.svg"
              sx={{
                width: "20px",
                height: "20px",
                color: "text.primary",
              }}
            />
          </IconButton>
        </Box>
      </Box>
      <Box sx={{ overflowY: "hidden" }}>
        <PromptSection
          prompts={prompts[index]?.prompts || []}
          setPrompts={(v) => setPromptsByIndex(index, v)}
          modelConfig={modelConfig[index] || {}}
          setModelConfig={(v, options) =>
            setModelConfigByIndex(index, v, options)
          }
          index={index}
          isSync={isSync}
          onSyncChange={(c) => {
            const baseSystemPrompt = prompts[0]?.prompts[0]?.content;

            if (c) {
              syncSystemPrompt(baseSystemPrompt, 0);
            }
            setIsSync(c);
          }}
          syncSystemPrompt={syncSystemPrompt}
          placeholders={placeholders[index] || []}
          setPlaceholders={(v) => setPlaceholdersByIndex(index, v)}
        />
      </Box>
    </Box>
  );
};

CompareInputSection.propTypes = {
  index: PropTypes.number,
  promptVersion: PropTypes.string,
  setSaveCommitOpen: PropTypes.func,
  isSync: PropTypes.bool,
  setIsSync: PropTypes.func,
  syncSystemPrompt: PropTypes.func,
};

export default CompareInputSection;
