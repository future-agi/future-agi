import { Box, Link, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useId, useState, useRef } from "react";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import NodeSelectionPopper from "../components/NodeSelectionPopper";
import useCanEditAgent from "../hooks/useCanEditAgent";

const NOT_IN_RELEASE = "Not available in this release";

const Action = ({
  anchorRef,
  iconSrc,
  label,
  ariaLabel,
  disabled = false,
  hasPopup = false,
  expanded = false,
  sx = {},
  onClick,
  onClose,
}) => {
  // The tile is a div, so Enter and Space have to be wired by hand for
  // keyboard users; they run the same handler as a click. Escape closes the
  // menu the tile opened, and only while it is open.
  const handleKeyDown = (event) => {
    if (event.key === "Escape") {
      if (!expanded) return;
      event.preventDefault();
      onClose?.();
      return;
    }
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onClick?.(event);
  };

  return (
    <Stack
      gap={1.5}
      alignItems={"center"}
      sx={{
        "&:hover": {
          opacity: 0.8,
        },
        "&:active": {
          opacity: 0.8,
        },
        ...sx,
      }}
    >
      <Box
        ref={anchorRef}
        role="button"
        tabIndex={0}
        aria-label={ariaLabel}
        aria-disabled={disabled || undefined}
        aria-haspopup={hasPopup ? "true" : undefined}
        aria-expanded={hasPopup ? expanded : undefined}
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          height: "66px",
          width: "66px",
          borderStyle: "dashed",
          cursor: "pointer",
        }}
        onClick={onClick}
        onKeyDown={handleKeyDown}
      >
        <SvgColor
          src={iconSrc}
          sx={{ width: 20, height: 20, bgcolor: "text.primary" }}
        />
      </Box>
      {label}
    </Stack>
  );
};

Action.propTypes = {
  iconSrc: PropTypes.string.isRequired,
  label: PropTypes.node,
  ariaLabel: PropTypes.string,
  disabled: PropTypes.bool,
  hasPopup: PropTypes.bool,
  expanded: PropTypes.bool,
  sx: PropTypes.object,
  onClick: PropTypes.func,
  onClose: PropTypes.func,
  anchorRef: PropTypes.object,
};

export default function EmptyView() {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);
  const templateReasonId = useId();
  const { canEditAgent } = useCanEditAgent();

  const handleToggle = () => {
    if (!canEditAgent) return;
    setOpen((prevOpen) => !prevOpen);
  };

  const handleClose = () => {
    setOpen(false);
  };

  return (
    <Box
      sx={{
        position: "absolute",
        inset: 0,
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        bgcolor: "background.paper",
        pointerEvents: "none",
        zIndex: 5,
      }}
    >
      <Stack
        direction={"row"}
        gap={2}
        alignItems={"flex-start"}
        sx={{ pointerEvents: "auto" }}
      >
        <CustomTooltip
          show={!canEditAgent}
          type=""
          size="small"
          title="You don't have permission to create agents."
          arrow
        >
          <Action
            anchorRef={anchorRef}
            onClick={handleToggle}
            onClose={handleClose}
            iconSrc="/assets/icons/ic_add.svg"
            ariaLabel="Add first node"
            disabled={!canEditAgent}
            hasPopup
            expanded={open}
            sx={
              !canEditAgent
                ? { opacity: 0.5, cursor: "not-allowed" }
                : undefined
            }
            label={
              <Stack gap={0} alignItems={"center"}>
                <Typography
                  typography={"s1_2"}
                  fontWeight={"fontWeightMedium"}
                  color={"text.primary"}
                >
                  Add first node
                </Typography>
                {/* The template gallery only had mock templates, and the graph
                    it loaded could not be saved or run. The link stays,
                    turned off, with the reason under it; nothing opens. */}
                {canEditAgent && (
                  <>
                    <Link
                      component="button"
                      type="button"
                      disabled
                      aria-describedby={templateReasonId}
                      typography={"s2_1"}
                      fontWeight={"fontWeightMedium"}
                      underline="none"
                      sx={{ color: "text.disabled", cursor: "not-allowed" }}
                    >
                      or start from a template
                    </Link>
                    <Typography
                      id={templateReasonId}
                      typography={"s2_1"}
                      fontWeight={"fontWeightRegular"}
                      color={"text.secondary"}
                    >
                      {NOT_IN_RELEASE}
                    </Typography>
                  </>
                )}
              </Stack>
            }
          />
        </CustomTooltip>
        <NodeSelectionPopper
          open={open}
          anchorEl={anchorRef.current}
          onClose={handleClose}
        />
        {/* <Typography
          sx={{
            my: "30px",
          }}
          typography={"s2_1"}
          fontWeight={"fontWeightMedium"}
          color={"text.secondary"}
        >
          Or
        </Typography>
        <Action
          sx={{
            ml: 4,
          }}
          label={
            <Typography
              typography={"s1_2"}
              fontWeight={"fontWeightMedium"}
              color={"text.primary"}
            >
              Build with AI
            </Typography>
          }
          iconSrc="/assets/icons/components/ic_improve_prompt.svg"
        /> */}
      </Stack>
    </Box>
  );
}
