import { Box, Link, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useRef } from "react";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import NodeSelectionPopper from "../components/NodeSelectionPopper";
import ChooseAgentTemplateDrawer from "../components/ChooseAgentTemplateDrawer";
import useCanEditAgent from "../hooks/useCanEditAgent";

const Action = ({ anchorRef, iconSrc, label, sx = {}, onClick }) => {
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
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          height: "66px",
          width: "66px",
          borderStyle: "dashed",
          cursor: "pointer",
        }}
        onClick={onClick}
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
  sx: PropTypes.object,
  onClick: PropTypes.func,
  anchorRef: PropTypes.object,
};

export default function EmptyView() {
  const [open, setOpen] = useState(false);
  const [templateDrawerOpen, setTemplateDrawerOpen] = useState(false);
  const anchorRef = useRef(null);
  const { canEditAgent } = useCanEditAgent();

  const handleToggle = () => {
    if (!canEditAgent) return;
    setOpen((prevOpen) => !prevOpen);
  };

  const handleClose = () => {
    setOpen(false);
  };

  const handleOpenTemplateDrawer = (e) => {
    e.stopPropagation();
    if (!canEditAgent) return;
    setTemplateDrawerOpen(true);
  };

  const handleCloseTemplateDrawer = () => {
    setTemplateDrawerOpen(false);
  };

  const handleSelectTemplate = () => {
    // Template loading is handled by the ChooseAgentTemplateDrawer
    // which updates the store with the template data
    setTemplateDrawerOpen(false);
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
            iconSrc="/assets/icons/ic_add.svg"
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
                {canEditAgent && (
                  <Link
                    typography={"s2_1"}
                    fontWeight={"fontWeightMedium"}
                    onClick={handleOpenTemplateDrawer}
                    sx={{ cursor: "pointer" }}
                  >
                    or start from a template
                  </Link>
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
          color={"black.500"}
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
              color={"black.1000"}
            >
              Build with AI
            </Typography>
          }
          iconSrc="/assets/icons/components/ic_improve_prompt.svg"
        /> */}
      </Stack>

      <ChooseAgentTemplateDrawer
        open={templateDrawerOpen}
        onClose={handleCloseTemplateDrawer}
        onSelectTemplate={handleSelectTemplate}
      />
    </Box>
  );
}
