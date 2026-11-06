import {
  Button,
  IconButton,
  Stack,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import React from "react";
import SvgColor from "../svg-color";
import PropTypes from "prop-types";

export default function ImporvedPromptHeader({
  handleClose,
  promptController,
}) {
  const theme = useTheme();
  const {
    hasNext,
    hasPrevious,
    onNext,
    onPrevious,
    copyCurrent,
    apply,
    isImprovingPrompt,
  } = promptController;
  return (
    <Stack direction={"row"} justifyContent={"space-between"}>
      <Typography
        variant="m3"
        fontWeight={"fontWeightSemiBold"}
        color={"text.primary"}
      >
        Generate prompt
      </Typography>
      <Stack direction={"row"} gap={theme.spacing(1.5)}>
        <Tooltip title="See previous result" arrow>
          <IconButton
            onClick={onPrevious}
            disabled={!hasPrevious || isImprovingPrompt}
            sx={{
              margin: 0,
              "&.Mui-disabled": {
                color: "divider",
              },
              color: "text.primary",
            }}
          >
            <SvgColor
              src="/assets/icons/ic_curve_arrow.svg"
              sx={{
                height: theme.spacing(2.5),
                width: theme.spacing(2.5),
              }}
            />
          </IconButton>
        </Tooltip>
        <Tooltip arrow title="See next result">
          <IconButton
            onClick={onNext}
            disabled={!hasNext || isImprovingPrompt}
            sx={{
              margin: 0,
              "&.Mui-disabled": {
                color: "divider",
              },
              color: "text.primary",
            }}
          >
            <SvgColor
              src="/assets/icons/ic_curve_arrow.svg"
              sx={{
                height: theme.spacing(2.5),
                width: theme.spacing(2.5),
                transform: "scaleX(-1)",
              }}
            />
          </IconButton>
        </Tooltip>
        <Tooltip title="Copy" arrow>
          <IconButton
            onClick={copyCurrent}
            disabled={isImprovingPrompt}
            sx={{
              margin: 0,
              "&.Mui-disabled": {
                color: "divider",
              },
              color: "text.primary",
            }}
          >
            <SvgColor
              src="/assets/icons/ic_copy.svg"
              sx={{
                height: theme.spacing(2.5),
                width: theme.spacing(2.5),
              }}
            />
          </IconButton>
        </Tooltip>
        <Button
          disabled={isImprovingPrompt}
          onClick={apply}
          variant="contained"
          color="primary"
          size="small"
        >
          Apply
        </Button>
        <IconButton
          onClick={handleClose}
          disabled={isImprovingPrompt}
          sx={{
            margin: 0,
            "&.Mui-disabled": {
              color: "divider",
            },
            color: "text.primary",
          }}
        >
          <SvgColor
            sx={{
              height: theme.spacing(2.5),
              width: theme.spacing(2.5),
            }}
            src="/assets/icons/ic_close.svg"
          />
        </IconButton>
      </Stack>
    </Stack>
  );
}

ImporvedPromptHeader.propTypes = {
  handleClose: PropTypes.func,
  promptController: PropTypes.shape({
    hasNext: PropTypes.bool,
    hasPrevious: PropTypes.bool,
    onNext: PropTypes.func,
    onPrevious: PropTypes.func,
    copyCurrent: PropTypes.func,
    apply: PropTypes.func,
    isImprovingPrompt: PropTypes.bool,
  }),
};
