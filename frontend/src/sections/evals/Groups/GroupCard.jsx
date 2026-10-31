import {
  Box,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useNavigate } from "react-router";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import { useEvaluationContext } from "src/sections/common/EvaluationDrawer/context/EvaluationContext";
import { EvalsButton } from "src/sections/common/EvaluationDrawer/StyleComponents.jsx";
import TooltipForEvals from "src/sections/common/EvaluationDrawer/TooltipForEvalPopover";
export default function GroupCard({
  name,
  description,
  evaluations,
  requiredInputs,
  onDelete,
  isSample,
  // onPlaygroundClick,
  id,
  isEvalsView,
  onClick,
}) {
  const { setVisibleSection, setSelectedEval } = useEvaluationContext();
  const [anchorEl, setAnchorEl] = useState(null);
  const open = Boolean(anchorEl);
  const theme = useTheme();
  const navigate = useNavigate();

  const handleClick = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleEdit = (e) => {
    e.stopPropagation();
    if (onClick) {
      onClick(id);
    } else {
      navigate(`/dashboard/evaluations/groups/${id}`);
      handleClose();
    }
  };

  const handleDelete = (e) => {
    e.stopPropagation();
    onDelete();
    handleClose();
  };

  const handleRunEvaluations = (e) => {
    e.stopPropagation();
    setVisibleSection("mapping");
    setSelectedEval({
      id: id,
      name: name,
      evalTemplateName: name,
      description: description,
      isGroupEvals: true,
    });
  };
  return (
    <Box
      sx={{
        padding: theme.spacing(1.5),
        border: "1px solid",
        borderColor: "border.default",
        borderRadius: "8px",
        display: "flex",
        flexDirection: "column",
        gap: theme.spacing(1.5),
        alignItems: "flex-start",
        position: "relative",
        ":hover": {
          cursor: "pointer",
        },
        minHeight: "160px",
      }}
      onClick={() => {
        if (open) return;
        if (onClick) {
          onClick(id);
        } else {
          navigate(`/dashboard/evaluations/groups/${id}`);
        }
      }}
    >
      <Stack gap={0.25}>
        <Typography
          typography={"s2_1"}
          fontWeight={"fontWeightSemiBold"}
          color={"text.primary"}
        >
          {name}
        </Typography>
        <Typography
          typography={"s2"}
          fontWeight={"fontWeightRegular"}
          color={"text.secondary"}
          sx={{
            textOverflow: "ellipsis",
            whiteSpace: "normal",
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            wordBreak: "break-word",
            WebkitLineClamp: 1,
            overflow: "hidden",
          }}
        >
          {description}
        </Typography>
      </Stack>
      <Stack direction={"row"} alignItems={"center"} gap={1}>
        <Stack
          sx={{
            padding: theme.spacing(0.5, 1.5),
            bgcolor: "background.neutral",
            flexShrink: 0,
          }}
          direction={"row"}
          alignItems={"center"}
          gap={0.5}
        >
          <SvgColor
            sx={{
              height: "16px",
              width: "16px",
              color: "text.primary",
            }}
            src="/assets/icons/ic_rounded_square.svg"
          />
          <Typography
            color={"text.primary"}
            typography={"s3"}
            fontWeight={"fontWeightRegular"}
          >
            Evaluations: {evaluations}
          </Typography>
        </Stack>
        <Stack
          sx={{
            padding: theme.spacing(0.5, 1.5),
            bgcolor: "background.neutral",
          }}
          direction={"row"}
          alignItems={"center"}
          gap={0.5}
        >
          <SvgColor
            sx={{
              height: "16px",
              width: "16px",
              color: "text.primary",
            }}
            src="/assets/prompt/slider-options.svg"
          />
          <TooltipForEvals
            heading="Required Columns"
            selectedEvalItem={requiredInputs}
          >
            <Typography
              color={"text.primary"}
              typography={"s3"}
              fontWeight={"fontWeightRegular"}
              sx={{
                textOverflow: "ellipsis",
                whiteSpace: "normal",
                display: "-webkit-box",
                WebkitBoxOrient: "vertical",
                WebkitLineClamp: 1,
                overflow: "hidden",
              }}
            >
              Required Columns:{" "}
              {requiredInputs?.length > 0 ? requiredInputs?.join(", ") : "None"}
            </Typography>
          </TooltipForEvals>
        </Stack>
      </Stack>
      {/* <EvalsButton
        size="small"
        onClick={onPlaygroundClick}
        sx={{
          mt: "auto",
        }}
        startIcon={
          <SvgColor
            sx={{
              width: "16px",
              height: "16px",
              color: "text.disabled",
            }}
            src="/assets/icons/navbar/ic_get_started.svg"
          />
        }
      >
        Playground
      </EvalsButton> */}
      {!isEvalsView && (
        <EvalsButton
          size="small"
          onClick={handleRunEvaluations}
          sx={{
            mt: "auto",
          }}
          startIcon={
            <SvgColor
              sx={{
                width: "16px",
                height: "16px",
                color: "text.disabled",
              }}
              src="/assets/icons/ic_completed.svg"
            />
          }
        >
          Run Evaluations
        </EvalsButton>
      )}
      <CustomTooltip
        show={isSample}
        placement="top"
        title="This evaluation group is pre-defined based on use case and cannot be customized"
      >
        <span
          style={{
            display: "inline-block",
            position: "absolute",
            top: "5px",
            right: "5px",
          }}
        >
          <IconButton disabled={isSample} onClick={handleClick} size="small">
            <SvgColor
              sx={{
                rotate: "90deg",
                height: "18px",
                width: "18px",
              }}
              src="/assets/icons/ic_ellipsis.svg"
            />
          </IconButton>
        </span>
      </CustomTooltip>
      <Menu
        anchorEl={anchorEl}
        open={open}
        onClose={handleClose}
        PaperProps={{
          sx: {
            mt: 1,
            minWidth: 200,
            p: 0.5,
          },
        }}
        transformOrigin={{ horizontal: "right", vertical: "top" }}
        anchorOrigin={{ horizontal: "right", vertical: "bottom" }}
      >
        <MenuItem onClick={handleEdit} sx={{ px: 1.25, py: 0.75 }}>
          <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
            <SvgColor
              src="/assets/icons/ic_pen.svg"
              sx={{
                width: 16,
                height: 16,
                ml: 0.5,
              }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Edit"
            primaryTypographyProps={{
              fontSize: 13,
              fontWeight: 400,
            }}
          />
        </MenuItem>
        <MenuItem
          onClick={handleDelete}
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ color: "status.danger.fg", px: 1.25, py: 0.75 }}
        >
          <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
            <SvgColor
              src="/assets/icons/ic_delete.svg"
              sx={{
                height: 20,
                width: 20,
              }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Delete"
            primaryTypographyProps={{
              fontSize: 13,
              fontWeight: 400,
            }}
          />
        </MenuItem>
      </Menu>
    </Box>
  );
}

GroupCard.propTypes = {
  name: PropTypes.string,
  description: PropTypes.string,
  evaluations: PropTypes.number,
  requiredInputs: PropTypes.array,
  onDelete: PropTypes.func,
  onEdit: PropTypes.func,
  onPlaygroundClick: PropTypes.func,
  id: PropTypes.string,
  isEvalsView: PropTypes.bool,
  onClick: PropTypes.func,
  isSample: PropTypes.bool,
};
