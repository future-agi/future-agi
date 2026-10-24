import { Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import SvgColor from "src/components/svg-color";
import { REPLAY_ITEMS } from "./ReplaySessions/constants";
import { useReplayConfiguration } from "./ReplaySessions/useReplayConfiguration";

const ScenarioItem = ({ title, description, iconSrc, onClick, disabled }) => {
  return (
    <Stack
      direction="row"
      gap={1}
      padding={2}
      border="1px solid"
      borderColor="divider"
      borderRadius="4px"
      sx={{
        cursor: "pointer",
        "&:hover": {
          backgroundColor: "action.hover",
        },
        transition: "background-color 200ms ease",
        "&:active": {
          backgroundColor: "action.hover",
        },
        userSelect: "none",
        opacity: disabled ? 0.5 : 1,
      }}
      onClick={onClick}
    >
      <SvgColor
        src={iconSrc}
        sx={{
          width: 24,
          height: 24,
          flexShrink: 0,
          color: "text.primary",
        }}
      />
      <Stack gap={0.5}>
        <Typography
          typography={"s1_2"}
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
        >
          {title}
        </Typography>
        <Typography typography={"s1"} color={"text.primary"}>
          {description}
        </Typography>
      </Stack>
    </Stack>
  );
};

ScenarioItem.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  iconSrc: PropTypes.string.isRequired,
  onClick: PropTypes.func.isRequired,
  disabled: PropTypes.bool.isRequired,
};

const CreateScenarios = ({
  open,
  onClose,
  onScenarioItemClick,
  isCreatingScenario,
}) => {
  const config = useReplayConfiguration();

  // Fallback to defaults if config is not provided
  const scenarioItems = config?.scenarioItems || REPLAY_ITEMS;
  const modalTitle = config?.modalTitle || "Create Scenarios";

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={modalTitle}
      hideCancelBtn
      dialogActionSx={{
        display: "none",
        mb: 1,
      }}
      isLoading={isCreatingScenario}
    >
      <Stack direction="column" gap={1}>
        {scenarioItems.map((item) => (
          <ScenarioItem
            disabled={isCreatingScenario}
            key={item.id}
            {...item}
            onClick={() => onScenarioItemClick(item?.id)}
          />
        ))}
      </Stack>
    </ModalWrapper>
  );
};

export default CreateScenarios;

CreateScenarios.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onScenarioItemClick: PropTypes.func.isRequired,
  isCreatingScenario: PropTypes.bool,
};

CreateScenarios.defaultProps = {
  isCreatingScenario: false,
};
