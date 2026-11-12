import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useWatch } from "react-hook-form";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import PickedModelNotice from "src/components/custom-model-dropdown/PickedModelNotice";
import { MODEL_TYPES } from "../../../develop-detail/RunPrompt/common";

export default function AgentChatForm({ control }) {
  const model = useWatch({ control, name: "model" });
  return (
    <Box display="flex" flexDirection="column" gap={2}>
      <Box>
        <CustomModelDropdownControl
          control={control}
          fieldName="model"
          label="Model Used"
          fullWidth
          searchDropdown
          size="small"
          showIcon
          modelObjectKey={"modelDetails"}
          extraParams={{ model_type: MODEL_TYPES.LLM }}
          requireUserApiKey={false}
        />
        <PickedModelNotice modelName={model} />
      </Box>
    </Box>
  );
}

AgentChatForm.propTypes = {
  control: PropTypes.object.isRequired,
};
