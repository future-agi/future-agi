import React, { useMemo, useRef, useState } from "react";
import {
  Box,
  Paper,
  Typography,
  Chip,
  Stack,
  useTheme,
  Popper,
  IconButton,
} from "@mui/material";
import Section from "src/components/section";
import PropTypes from "prop-types";
import Iconify from "../../../components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { useWatch } from "react-hook-form";
import { EvalTypes } from "./validation";
import { useEvaluationContext } from "./context/EvaluationContext";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import EvalsActions from "./EvalsActions";
import SvgColor from "../../../components/svg-color";
import { ShowComponent } from "src/components/show/ShowComponent";

const EvaluationCard = ({
  eval: evalItem,
  recommended = false,
  eval_category,
  control,
  setValue,
  selectedEvals,
  createGroupMode,
  setSelectedEvals,
  ...rest
}) => {
  const { setSelectedEval, setVisibleSection, setViewEvalsDetails } =
    useEvaluationContext();
  const theme = useTheme();
  const tags =
    evalItem?.eval_template_tags?.filter((tag) => tag !== "FUTURE_EVALS") || [];
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);
  let hoverTimeout = null;

  const handleCategoryClick = (category) => {
    setValue("selectedEvalCategory", category);
  };

  const selectedEvalTags = useWatch({
    control,
    name: "selectedEvalTags",
  });

  const toggleTagSelection = (tag) => {
    const formattedTag = tag?.toUpperCase()?.replace(/ /g, "_");
    const tagExists = EvalTypes?.some((opt) => opt.value === formattedTag);
    const tagToUse = tagExists ? formattedTag : tag;

    const prevTags = selectedEvalTags || [];

    const newTags = prevTags?.includes(tagToUse)
      ? prevTags?.filter((t) => t !== tagToUse)
      : [...prevTags, tagToUse];

    setValue("selectedEvalTags", newTags);
  };

  const handleTagClick = (tag) => {
    toggleTagSelection(tag);
  };

  const handlePopperTagClick = (tag) => {
    toggleTagSelection(tag);
    setOpen(false);
  };

  const showPopper = () => {
    clearTimeout(hoverTimeout);
    setOpen(true);
  };

  const hidePopper = () => {
    hoverTimeout = setTimeout(() => setOpen(false), 100);
  };

  const getSmartTruncatedTags = () => {
    const firstTwoTags = tags?.slice(0, 2);
    if (firstTwoTags.length === 0) return [];

    const COMBINED_LENGTH_THRESHOLD = 25;
    const MAX_SINGLE_TAG_LENGTH = 20;
    const PLUS_N_CHIP_APPROX_LENGTH = 4;

    const willShowPlusNChip = tags?.length > 2;

    const adjustedThreshold = willShowPlusNChip
      ? COMBINED_LENGTH_THRESHOLD - PLUS_N_CHIP_APPROX_LENGTH
      : COMBINED_LENGTH_THRESHOLD;

    const combinedLength = firstTwoTags?.reduce(
      (sum, tag) => sum + tag?.length,
      0,
    );

    if (combinedLength <= adjustedThreshold) {
      return firstTwoTags?.map((tag) => ({ original: tag, display: tag }));
    }

    if (firstTwoTags?.length === 1) {
      const tag = firstTwoTags[0];
      const maxLength = willShowPlusNChip
        ? MAX_SINGLE_TAG_LENGTH - PLUS_N_CHIP_APPROX_LENGTH
        : MAX_SINGLE_TAG_LENGTH;
      return [
        {
          original: tag,
          display:
            tag.length > maxLength ? tag.substring(0, maxLength) + "..." : tag,
        },
      ];
    }

    const [tag1, tag2] = firstTwoTags;
    const availableSpace = adjustedThreshold - 2;
    const halfSpace = Math.floor(availableSpace / 2);

    let truncatedTag1 = tag1;
    let truncatedTag2 = tag2;

    if (tag1?.length > halfSpace && tag2?.length > halfSpace) {
      truncatedTag1 = tag1.substring(0, halfSpace) + "...";
      truncatedTag2 = tag2.substring(0, halfSpace) + "...";
    } else if (tag1.length <= halfSpace) {
      const remainingSpace = availableSpace - tag1?.length;
      truncatedTag2 =
        tag2?.length > remainingSpace
          ? tag2.substring(0, remainingSpace) + "..."
          : tag2;
    } else if (tag2?.length <= halfSpace) {
      const remainingSpace = availableSpace - tag2?.length;
      truncatedTag1 =
        tag1?.length > remainingSpace
          ? tag1?.substring(0, remainingSpace) + "..."
          : tag1;
    }

    return [
      { original: tag1, display: truncatedTag1 },
      { original: tag2, display: truncatedTag2 },
    ];
  };

  const smartTruncatedTags = getSmartTruncatedTags();
  const isSelected = useMemo(() => {
    if (!createGroupMode) return false;
    return selectedEvals?.some((item) => item?.id === evalItem?.id);
  }, [createGroupMode, evalItem?.id, selectedEvals]);

  return (
    <Section
      variant="panel"
      sx={{
        minHeight: "114px",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        cursor: "pointer",
        paddingX: "12px",
        paddingY: "16px",
        position: "relative",
        gap: 2,
        ...(isSelected && {
          borderColor: "text.primary",
          bgcolor: "action.hover",
        }),
      }}
      onClick={() => {
        if (createGroupMode && evalItem?.id) {
          setSelectedEvals((prev) => {
            const exists = prev?.some((item) => item?.id === evalItem?.id);
            if (exists) {
              return prev?.filter((item) => item?.id !== evalItem?.id);
            }
            return [...prev, evalItem];
          });
          return;
        }
        if (rest.isEvalsView) {
          setViewEvalsDetails({
            ...evalItem,
            eval_template_tags: [
              eval_category?.toString()?.toUpperCase(),
              ...tags,
            ],
          });
          trackEvent(Events.evalsEvalClicked, {
            [PropertyName.evalId]: evalItem?.id,
            [PropertyName.evalType]: eval_category,
          });
        } else {
          setVisibleSection("mapping");
          setSelectedEval({
            ...evalItem,
            eval_template_tags: [
              eval_category?.toString()?.toUpperCase(),
              ...tags,
            ],
          });
        }
      }}
    >
      {recommended ? (
        <CustomTooltip
          show={true}
          title={"Based on your dataset columns and use case selected"}
          placement="top"
          arrow
          size="small"
        >
          <Chip
            label="Recommended"
            size="small"
            icon={
              <Iconify
                icon="solar:like-linear"
                width={16}
                height={16}
                style={{
                  color: theme.palette.status.success.fg,
                  marginRight: "2px",
                  marginLeft: "0px",
                }}
              />
            }
            sx={{
              position: "absolute",
              top: 0,
              right: 0,
              backgroundColor: "status.success.bg",
              color: "status.success.fg",
              lineHeight: "18px",
              borderBottomLeftRadius: "4px",
              "& .MuiChip-label": {
                padding: 0,
              },
              "&:hover": {
                backgroundColor: "status.success.bg",
                color: "status.success.fg",
              },
            }}
          />
        </CustomTooltip>
      ) : null}
      <Box>
        <Box display={"flex"} gap={theme.spacing(1)}>
          <Typography
            sx={{
              fontWeight: 400,
              fontSize: "13px",
              lineHeight: "22px",
              color: "text.primary",
            }}
          >
            {evalItem?.name || "Unnamed Evaluation"}
          </Typography>
          <IconButton
            sx={{
              paddingY: 0,
            }}
            onClick={(e) => {
              e.stopPropagation();
              copyToClipboard(evalItem?.name);
              enqueueSnackbar({
                message: "Copied to clipboard",
                variant: "success",
              });
            }}
          >
            <Iconify
              icon="solar:copy-linear"
              sx={{
                width: theme.spacing(2),
                height: theme.spacing(2),
              }}
            />
          </IconButton>
        </Box>
        <Typography
          sx={{
            fontSize: "12px",
            color: "text.disabled",
            fontWeight: 400,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap", // for single line ellipsis
          }}
        >
          {evalItem?.description || "No description available."}
        </Typography>
      </Box>

      <Stack
        direction="row"
        spacing={1}
        sx={{ flexWrap: "nowrap", overflow: "hidden" }}
      >
        <Chip
          label={`${eval_category}`.toUpperCase()}
          size="small"
          onClick={(event) => {
            event.stopPropagation();
            handleCategoryClick(eval_category);
          }}
          sx={{
            backgroundColor: "action.hover",
            borderBottomLeftRadius: "4px",
            color: "primary.main",
            lineHeight: "18px",
            flexShrink: 0, // Prevent category chip from shrinking
            "& .MuiChip-label": {
              padding: 0,
            },
            "&:hover": {
              backgroundColor: "action.hover",
              color: "primary.main",
            },
          }}
        />

        <>
          {smartTruncatedTags?.map((tagObj) => (
            <Chip
              key={tagObj.original}
              label={tagObj.display}
              title={tagObj.original} // Show full tag on hover
              onClick={(event) => {
                event.stopPropagation();
                handleTagClick(tagObj.original);
              }}
              size="small"
              sx={{
                backgroundColor: "background.neutral",
                borderColor: "divider",
                lineHeight: "16px",
                color: "text.primary",
                "& .MuiChip-label": {
                  padding: 0,
                },
                "&:hover": {
                  backgroundColor: "background.neutral",
                  borderColor: "divider",
                },
              }}
            />
          ))}

          {tags?.length > 2 && (
            <>
              <Chip
                ref={anchorRef}
                label={`+${tags?.length - 2}`}
                size="small"
                onMouseEnter={showPopper}
                onClick={(event) => {
                  event.stopPropagation();
                }}
                onMouseLeave={hidePopper}
                sx={{
                  backgroundColor: "background.neutral",
                  borderColor: "divider",
                  lineHeight: "16px",
                  color: "text.primary",
                  cursor: "pointer",
                  flexShrink: 0, // Prevent +N chip from shrinking
                  "& .MuiChip-label": { padding: 0 },
                  "&:hover": {
                    backgroundColor: "background.neutral",
                    borderColor: "divider",
                  },
                }}
              />

              <Popper
                open={open}
                anchorEl={anchorRef.current}
                placement="bottom-start"
                disablePortal={false}
                modifiers={[{ name: "offset", options: { offset: [0, 8] } }]}
                style={{ zIndex: 1300 }}
                onMouseEnter={showPopper}
                onMouseLeave={hidePopper}
              >
                <Paper
                  sx={{
                    p: 1,
                    maxHeight: "160px",
                    overflowY: "auto",
                    width: "max-content",
                    minWidth: "135px",
                  }}
                >
                  <Box display="flex" flexDirection="column" gap={0.5}>
                    {tags?.slice(smartTruncatedTags?.length).map((tag, idx) => (
                      <Typography
                        key={idx}
                        paddingX={1}
                        paddingY={0.5}
                        onClick={(event) => {
                          event.stopPropagation();
                          handlePopperTagClick(tag);
                        }}
                        sx={{
                          fontSize: "13px",
                          fontWeight: 400,
                          color: "text.primary",
                          whiteSpace: "nowrap",
                          cursor: "pointer",
                          "&:hover": {
                            backgroundColor: "action.hover",
                          },
                        }}
                      >
                        {tag}
                      </Typography>
                    ))}
                  </Box>
                </Paper>
              </Popper>
            </>
          )}
        </>
      </Stack>
      <EvalsActions
        evalItem={evalItem}
        eval_category={eval_category}
        tags={tags}
      />
      <ShowComponent condition={createGroupMode}>
        <IconButton
          sx={{
            position: "absolute",
            top: 4,
            right: 4,
          }}
        >
          {isSelected ? (
            <Box
              component={"img"}
              src="/assets/icons/ic_success_filled.svg"
              sx={{
                height: "20px",
                width: "20px",
              }}
            />
          ) : (
            <SvgColor
              src={"/assets/icons/ic_circle.svg"}
              sx={{
                height: "20px",
                width: "20px",
              }}
            />
          )}
        </IconButton>
      </ShowComponent>
    </Section>
  );
};

EvaluationCard.propTypes = {
  eval: PropTypes.shape({
    name: PropTypes.string,
    description: PropTypes.string,
    eval_template_tags: PropTypes.arrayOf(PropTypes.string),
    id: PropTypes.string,
  }),
  recommended: PropTypes.bool,
  eval_category: PropTypes.string,
  control: PropTypes.object,
  setValue: PropTypes.func,
  selectedEvals: PropTypes.array,
  createGroupMode: PropTypes.bool,
  setSelectedEvals: PropTypes.func,
};

export default EvaluationCard;
