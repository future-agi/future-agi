import { styled, useTheme } from "@mui/material";
import React from "react";
import StepConnector, {
  stepConnectorClasses,
} from "@mui/material/StepConnector";
import StepLabel, { stepLabelClasses } from "@mui/material/StepLabel";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

export const WizardConnector = styled(StepConnector)(({ theme }) => ({
  [`&.${stepConnectorClasses.alternativeLabel}`]: {
    top: 10,
    left: "calc(-50% + 16px)",
    right: "calc(50% + 16px)",
  },
  [`&.${stepConnectorClasses.active}`]: {
    [`& .${stepConnectorClasses.line}`]: {
      borderColor: theme.palette.success.main,
    },
  },
  [`&.${stepConnectorClasses.completed}`]: {
    [`& .${stepConnectorClasses.line}`]: {
      borderColor: theme.palette.success.main,
    },
  },
  [`& .${stepConnectorClasses.line}`]: {
    borderColor: theme.palette.divider,
    borderTopWidth: 3,
    borderRadius: 1,
  },
}));

export const WizardStepLabel = styled(StepLabel)(({ theme }) => ({
  [`& .${stepLabelClasses.label}`]: {
    [`&.${stepLabelClasses.active}`]: {
      color: theme.palette.text.primary,
    },
    color: theme.palette.text.disabled,
    fontWeight: 600,
  },
}));

export const WizardStepIconRoot = styled("div")(({ theme, ownerState }) => ({
  color: theme.palette.text.secondary,
  display: "flex",
  height: 24,
  alignItems: "center",
  ...(ownerState.active && {
    color: theme.palette.primary.main,
  }),
  "& .WizardStepIcon-completedIcon": {
    color: theme.palette.success.main,
    zIndex: 1,
    fontSize: 18,
  },
  "& .WizardStepIcon-circle": {
    width: 8,
    height: 8,
    borderRadius: "50%",
    backgroundColor: "currentColor",
  },
}));

export function WizardStepIcon(props) {
  const { active, completed, className } = props;
  const theme = useTheme();

  return (
    <WizardStepIconRoot ownerState={{ active }} className={className}>
      {completed ? (
        <Iconify
          icon="solar:check-linear"
          className="WizardStepIcon-completedIcon"
          width={24}
          height={24}
        />
      ) : (
        <div
          className="WizardStepIcon-circle"
          style={{ color: !active ? theme.palette.divider : undefined }}
        />
      )}
    </WizardStepIconRoot>
  );
}

WizardStepIcon.propTypes = {
  active: PropTypes.bool,
  className: PropTypes.string,
  completed: PropTypes.bool,
};
