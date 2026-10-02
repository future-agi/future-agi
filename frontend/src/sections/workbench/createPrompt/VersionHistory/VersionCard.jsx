import {
  Box,
  Checkbox,
  Typography,
  useTheme,
  Popper,
  Stack,
} from "@mui/material";
import React, {
  useMemo,
  useRef,
  useState,
  useLayoutEffect,
  useCallback,
  useEffect,
} from "react";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import { format } from "date-fns";
import { getRandomId } from "src/utils/utils";
import { useSnackbar } from "src/components/snackbar";
import CustomModelTools from "src/components/custom-model-tools";
import CustomTooltip from "src/components/tooltip";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import Iconify from "src/components/iconify";
import CustomModelOptions from "src/components/custom-model-options/CustomModelOptions";

import VersionStyle from "../promptActions/VersionStyle";
import { usePromptWorkbenchContext } from "../WorkbenchContext";
import { DraftBadge } from "../SharedStyledComponents";

import Variable from "./Variable";
import {
  DefaultChip,
  RestoreButton,
  VersionCardWrapper,
  CommitBox,
} from "./VersionStyledComponents";
import LabelDropdown from "./LabelDropdown/LabelDropdown";
import PromptLabel from "./LabelDropdown/PromptLabel";

const VersionCard = ({
  version,
  showCheckbox,
  setChecked,
  checked,
  disableCheckbox,
  showRestore = true,
  checkboxMessage,
}) => {
  const theme = useTheme();
  const ref = useRef(null);
  const timeoutRef = useRef(null);
  const [hiddenLabelsCount, setHiddenLabelCount] = useState(0);
  const [anchorEl, setAnchorEl] = useState(null);
  const [isPopperHovered, setIsPopperHovered] = useState(false);

  const variableNames = useMemo(
    () =>
      Object.entries(version?.variable_names || {}).map(([key, value]) => ({
        id: getRandomId(),
        name: key,
        isSet: Boolean(value?.length),
      })),
    [version?.variable_names],
  );

  const variableNamesForToolTip = (
    <>
      {variableNames?.slice(2).map(({ id, name }) => (
        <Variable key={id} value={name} />
      ))}
    </>
  );

  const model = version?.prompt_config_snapshot?.configuration?.model;

  const toolConfig = version?.prompt_config_snapshot?.configuration;

  const { onRestoreVersion, selectedVersions } = usePromptWorkbenchContext();

  const { enqueueSnackbar } = useSnackbar();

  const adjustVisibility = useCallback(() => {
    if (!version?.labels?.length || !ref?.current) return;

    let labelWidth = 0;
    let visibleCount = 0;
    const containerWidth = ref.current.clientWidth;

    for (const label of version.labels) {
      // Calculate approximate width: padding(16) + gap(4) + text width(8 per char)
      const currentLabelWidth = 20 + (label?.name?.length * 7 || 0) + 20;
      labelWidth += currentLabelWidth;

      if (labelWidth + 24 <= containerWidth) {
        visibleCount++;
      } else {
        break;
      }
    }

    const hidden = Math.max(0, version.labels.length - visibleCount);
    setHiddenLabelCount(hidden);
  }, [version?.labels]);

  useLayoutEffect(() => {
    if (ref?.current) {
      if ("ResizeObserver" in window) {
        new ResizeObserver(adjustVisibility).observe(ref?.current);
      }
    }
  }, [ref, adjustVisibility]);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  return (
    <>
      <VersionCardWrapper>
        <Stack sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "flex-start",
              gap: 1,
            }}
          >
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: 0.5,
                flex: 1,
                overflow: "hidden",
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  gap: 1,
                  alignItems: "center",
                  overflow: "hidden",
                  flex: 1,
                }}
              >
                <ShowComponent condition={showCheckbox}>
                  <CustomTooltip
                    show={disableCheckbox}
                    title={checkboxMessage}
                    placement="bottom"
                    arrow
                  >
                    <Box
                      sx={{
                        cursor: disableCheckbox ? "not-allowed" : "default",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      <Checkbox
                        checked={checked}
                        onChange={setChecked}
                        disabled={disableCheckbox}
                        sx={{
                          padding: "0px",
                          margin: "0px",
                          borderRadius: "4px",
                          "&.Mui-disabled.Mui-checked": {
                            color: "primary.lighter",
                          },
                          "&.Mui-disabled": {
                            color: "divider",
                          },
                        }}
                      />
                    </Box>
                  </CustomTooltip>
                </ShowComponent>
                <Box sx={{ flexShrink: 0 }}>
                  <Typography typography="s1.2" fontWeight="fontWeightMedium">
                    {version?.template_name}
                  </Typography>
                </Box>
                <Typography
                  typography="s2"
                  fontWeight={"fontWeightMedium"}
                  sx={{
                    backgroundColor: "action.hover",
                    borderRadius: "2px",
                    color: "text.primary",
                    paddingX: theme.spacing(0.75),
                    paddingTop: theme.spacing(0.5),
                    paddingBottom: theme.spacing(0.375),
                  }}
                >
                  {version?.template_version}
                </Typography>
                <ShowComponent condition={version?.is_default}>
                  <DefaultChip label="Default" />
                </ShowComponent>
                <ShowComponent condition={version?.is_draft}>
                  <DraftBadge>Draft</DraftBadge>
                </ShowComponent>
                <Box
                  sx={{
                    display: "flex",
                    gap: 0.5,
                    flex: 1,
                    overflow: "hidden",
                  }}
                  ref={ref}
                >
                  {version?.labels
                    ?.slice(0, version.labels.length - hiddenLabelsCount)
                    .map((label) => (
                      <PromptLabel
                        key={label.id}
                        name={label.name}
                        id={label.id}
                        version={version}
                        viewOnly
                        showRemove={label.type !== "system"}
                      />
                    ))}
                  {hiddenLabelsCount > 0 && (
                    <>
                      <Box
                        sx={{
                          backgroundColor: "action.hover",
                          color: "text.primary",
                          borderRadius: "2px",
                          padding: "4px 8px",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          flexShrink: 0,
                        }}
                        onMouseEnter={(event) => {
                          // Clear any existing timeout
                          if (timeoutRef.current) {
                            clearTimeout(timeoutRef.current);
                          }
                          setAnchorEl(event.currentTarget);
                        }}
                        onMouseLeave={() => {
                          // Only close if popper is not hovered after a delay
                          timeoutRef.current = setTimeout(() => {
                            if (!isPopperHovered) {
                              setAnchorEl(null);
                            }
                          }, 100);
                        }}
                      >
                        <Typography typography="s3" fontWeight={500}>
                          +{hiddenLabelsCount}
                        </Typography>
                      </Box>
                      <Popper
                        open={Boolean(anchorEl)}
                        anchorEl={anchorEl}
                        placement="bottom"
                        modifiers={[
                          {
                            name: "offset",
                            options: {
                              offset: [0, 8],
                            },
                          },
                        ]}
                        sx={{
                          zIndex: theme.zIndex.tooltip,
                        }}
                      >
                        <Box
                          sx={{
                            backgroundColor: "background.paper",
                            border: "1px solid",
                            borderColor: "divider",
                            borderRadius: 1,
                            padding: 1,
                            boxShadow: 4,
                            minWidth: "150px",
                            display: "flex",
                            flexDirection: "column",
                            gap: 1,
                          }}
                          onMouseEnter={() => {
                            // Clear any existing timeout when entering popper
                            if (timeoutRef.current) {
                              clearTimeout(timeoutRef.current);
                            }
                            setIsPopperHovered(true);
                          }}
                          onMouseLeave={() => {
                            setIsPopperHovered(false);
                            setAnchorEl(null);
                          }}
                        >
                          {version?.labels
                            ?.slice(version.labels.length - hiddenLabelsCount)
                            .map((label) => (
                              <PromptLabel
                                key={label.id}
                                name={label.name}
                                id={label.id}
                                version={version}
                                viewOnly
                                showRemove={label.type !== "system"}
                              />
                            ))}
                        </Box>
                      </Popper>
                    </>
                  )}
                </Box>
              </Box>
              <Typography
                fontSize={"14px"}
                fontWeight={400}
                color={"text.primary"}
              >
                {format(
                  new Date(version?.created_at),
                  "MMM d, yyyy 'at' h:mm a",
                )}
              </Typography>
            </Box>
            <Box
              sx={{
                display: "flex",
                gap: 1,
                flexDirection: "row",
                alignItems: "center",
              }}
            >
              <ShowComponent condition={!version?.is_draft}>
                <LabelDropdown
                  haveTag={version?.is_default || version?.labels?.length}
                  version={version}
                />
              </ShowComponent>
              <ShowComponent
                condition={
                  selectedVersions.length === 1 &&
                  selectedVersions?.[0].version !== version?.template_version &&
                  showRestore
                }
              >
                <RestoreButton
                  sx={{
                    display: "block",
                    height: "32px",
                  }}
                  onClick={() => {
                    enqueueSnackbar(
                      <>
                        {version?.template_name}&nbsp;
                        <VersionStyle text={version?.template_version} />
                        &nbsp; has been restored
                      </>,
                      { variant: "info" },
                    );
                    onRestoreVersion(version);
                  }}
                >
                  Restore
                </RestoreButton>
              </ShowComponent>
            </Box>
          </Box>

          <Box
            sx={{
              display: "flex",
              gap: 1,
              flexDirection: "row",
              alignItems: "center",
            }}
          >
            <ShowComponent condition={Boolean(model)}>
              <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
                <CustomModelDropdown
                  disabledClick
                  hoverPlacement="bottom-end"
                  buttonTitle="Select Model"
                  buttonIcon={
                    <Iconify
                      icon="radix-icons:box-model"
                      width="16px"
                      height="16px"
                      sx={{
                        cursor: "pointer",
                        color: "text.primary",
                      }}
                    />
                  }
                  value={typeof model === "string" ? model : ""}
                  modelDetail={toolConfig?.model_detail}
                  onChange={() => {}}
                />
                <CustomModelOptions
                  modelConfig={toolConfig}
                  viewOnly
                  hoverPlacement="bottom-end"
                />
                <CustomModelTools
                  tools={toolConfig?.tools || []}
                  viewOnly
                  disableHover={false}
                  hoverPlacement="bottom-end"
                />
              </Box>
            </ShowComponent>
          </Box>

          <ShowComponent condition={Boolean(variableNames?.length)}>
            <Box
              sx={{
                display: "flex",
                gap: 1,
                alignItems: "center",
                flexWrap: "wrap",
              }}
            >
              <SvgColor
                src="/assets/icons/ic_variables.svg"
                sx={{ width: "20px", height: "20px", color: "text.primary" }}
              />
              <Typography sx={{ fontSize: "15px", fontWeight: 500 }}>
                Variables :
              </Typography>

              {variableNames?.length > 2 ? (
                <>
                  {variableNames?.slice(0, 2).map(({ id, name }) => {
                    return <Variable key={id} value={name} />;
                  })}
                  <CustomTooltip
                    show={variableNames?.length > 2}
                    title={variableNamesForToolTip}
                  >
                    <Typography
                      typography="s2"
                      sx={{
                        bgcolor: "success.lighter",
                        padding: 0.5,
                        color: "success.dark",
                        fontSize: "15px",
                        alignItems: "center",
                        fontWeight: 500,
                      }}
                    >
                      +{variableNames?.length - 2}
                    </Typography>
                  </CustomTooltip>
                </>
              ) : (
                <>
                  {variableNames?.map(({ id, name }) => {
                    return <Variable key={id} value={name} />;
                  })}
                </>
              )}
            </Box>
          </ShowComponent>

          <ShowComponent condition={Boolean(version?.commitMessage?.length)}>
            <CommitBox>
              <Box sx={{ display: "flex", flexDirection: "row", gap: "2px" }}>
                <SvgColor
                  src="/assets/icons/ic_commit.svg"
                  sx={{ width: "20px", height: "20px", color: "text.primary" }}
                />
                <Typography typography="s1" fontWeight="fontWeightMedium">
                  Commit
                </Typography>
              </Box>

              <Typography typography="s1" color="text.primary">
                {version?.commitMessage}
              </Typography>
            </CommitBox>
          </ShowComponent>
        </Stack>
      </VersionCardWrapper>
      {/* <ShowComponent condition={index === 0}>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
          }}
        />
      </ShowComponent> */}
    </>
  );
};

VersionCard.propTypes = {
  version: PropTypes.object,
  showCheckbox: PropTypes.bool,
  setChecked: PropTypes.func,
  checked: PropTypes.bool,
  disableCheckbox: PropTypes.bool,
  showRestore: PropTypes.bool,
  checkboxMessage: PropTypes.string,
};

export default VersionCard;
