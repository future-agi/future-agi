import React, { useState, useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, TextField, Typography, useTheme } from "@mui/material";
import styled from "@emotion/styled";
import { LoadingButton } from "@mui/lab";
import Divider from "@mui/material/Divider";
import { action } from "src/theme/palette";
import axiosInstance, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import debounce from "lodash/debounce";
import { m } from "framer-motion";

import GenerateResult from "../../PromptDrawer/GenerateResult";

import ImprovePromptBox from "./ImprovePromptBox";
import EditablePromptItem from "./EditablePromptItem";
import PromptItemDisplay from "./PromptItemDisplay";

const StyledBox = styled(Box)(() => ({
  display: "flex",
  width: "50%",
}));

const ConversationBox = styled(Box)(({ theme }) => ({
  marginBottom: theme.spacing(1),
  display: "flex",
  justifyContent: "flex-end",
}));

const dotAnimation = (delay) => ({
  y: [0, -5, 0],
  transition: {
    duration: 1,
    repeat: Infinity,
    ease: "easeInOut",
    delay,
    repeatDelay: 0.8,
  },
});

const LoadingIndicator = () => (
  <Box
    sx={{
      display: "flex",
      justifyContent: "flex-start",
      alignItems: "flex-start",
      marginTop: 1,
      marginLeft: 1,
    }}
  >
    <Typography
      variant="subtitle2"
      sx={{
        color: "text.disabled",
        fontSize: "2rem",
        display: "flex",
        gap: "4px",
      }}
    >
      <m.span animate={dotAnimation(0)}>.</m.span>
      <m.span animate={dotAnimation(0.2)}>.</m.span>
      <m.span animate={dotAnimation(0.4)}>.</m.span>
    </Typography>
  </Box>
);

const ImprovePromptDrawer = (props) => {
  const { onClose, onApply, setIsDirty } = props;
  const [inputs, setInputs] = useState({
    prompt: "",
    followUpPrompt: "",
  });
  const [output, setOutput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversation, setConversation] = useState([]);
  const [editingIndex, setEditingIndex] = useState(null);
  const [, setOriginalPrompts] = useState({});
  const [tempPrompt, setTempPrompt] = useState(""); // New state for temporary prompt
  const [showOutputControls] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [existingPrompt, setExistingPrompt] = React.useState("");
  const [improvementRequirements, setImprovementRequirements] =
    React.useState("");
  const [hasInitialPrompt, setHasInitialPrompt] = useState(false);
  const [isApplyButtonEnabled, setIsApplyButtonEnabled] = useState(false);
  const [hasApplied, setHasApplied] = useState(false);
  const theme = useTheme();

  useEffect(() => {
    if (!hasApplied) {
      const isDirty =
        inputs.prompt !== "" ||
        conversation.length > 0 ||
        improvementRequirements !== "";
      setIsDirty(isDirty);
    } else {
      setIsDirty(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    inputs,
    conversation,
    existingPrompt,
    improvementRequirements,
    hasApplied,
  ]);

  const handleApply = () => {
    setHasApplied(true);
    trackEvent(Events.improvePromptApplied, {
      [PropertyName.formFields]: {
        ImprovedPrompt: output,
      },
    });
    onApply(output);
  };

  const debounceTrackEvent = useMemo(() => {
    return debounce((newValue) => {
      trackEvent(Events.improvementScopeEntered, {
        [PropertyName.formFields]: {
          query: newValue,
        },
      });
    }, 500);
  }, []);

  const handleExistingPromptChange = (value) => {
    setExistingPrompt(value);
    trackEvent(Events.userEnteredPrompt);
  };

  const handleImprovementRequirementsChange = (e) => {
    debounceTrackEvent(e.target.value);

    setImprovementRequirements(e.target.value);
  };

  const handleGenerate = () => {
    trackEvent(Events.improvePromptClicked, {
      [PropertyName.formFields]: {
        existing_prompt: existingPrompt,
        improvementRequirements: improvementRequirements,
      },
    });
    setImprovementRequirements("");
    if (!existingPrompt || !improvementRequirements) {
      return;
    }
    setLoading(true);
    setGenerating(true);
    setIsApplyButtonEnabled(false);

    if (!hasInitialPrompt) {
      setConversation((prev) => [
        {
          prompt: existingPrompt,
          editable: false,
        },
        ...prev,
      ]);
      setOriginalPrompts((prev) => ({
        ...prev,
        [conversation.length]: existingPrompt,
      }));
      setHasInitialPrompt(true);
    }
    setConversation((prev) => [
      ...prev,
      {
        prompt: improvementRequirements,
        editable: true,
      },
    ]);
  };

  const handleEditClick = (index) => {
    setEditingIndex(index);
    setTempPrompt(conversation[index].prompt);
  };

  const handleCancelEdit = () => {
    setEditingIndex(null);
    setConversation((prev) => {
      const updatedConversation = [...prev];
      updatedConversation[editingIndex].prompt = tempPrompt;
      return updatedConversation;
    });
  };

  const handleUpdatePrompt = async (index, followUpPrompt) => {
    setLoading(true);
    setEditingIndex(null);
    setGenerating(true);
    setIsApplyButtonEnabled(false);
    setInputs((prev) => ({ ...prev, prompt: "" }));

    try {
      setConversation((prev) => {
        const updatedConversation = [...prev];
        updatedConversation[editingIndex].prompt = tempPrompt;
        return updatedConversation;
      });
      setConversation((prev) => [
        ...prev,
        {
          prompt: followUpPrompt,
          editable: true,
        },
      ]);

      const res = await axiosInstance.post(
        endpoints.develop.runPrompt.updatePrompt,
        {
          existingPrompt: output,
          improvementRequirements: followUpPrompt,
        },
      );
      setOutput(res.data.result.prompt);

      setOriginalPrompts((prev) => ({
        ...prev,
        [conversation.length]: followUpPrompt,
      }));

      setInputs((prev) => ({ ...prev, prompt: "" }));
      setExistingPrompt(res.data.result.prompt);

      setIsApplyButtonEnabled(true);
    } catch (error) {
      enqueueSnackbar("Failed to update prompt. Please try again.", {
        variant: "error",
      });
      setOutput("");
    } finally {
      setLoading(false);
      setEditingIndex(null);
      setGenerating(false);
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        flex: 1,
        overflowY: "hidden",
        padding: theme.spacing(2),
      }}
    >
      {/* left side */}
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          width: "50%",
          marginRight: theme.spacing(1.5),
          gap: theme.spacing(2),
        }}
      >
        <Typography variant="subtitle2" color="text.primary" fontSize="0.8rem">
          Improve Existing Prompt
        </Typography>
        <Box sx={{ display: "flex", gap: 0.8, alignItems: "center" }}>
          {/* <Iconify icon="solar:info-circle-bold" color="#637381" width="15px" /> */}
          <Typography variant="caption" color="text.secondary">
            Enhance your prompt with nuanced refinements for more precise and
            effective results.
          </Typography>
        </Box>
        {!hasInitialPrompt && (
          <>
            <ImprovePromptBox
              placeholder="Enter User Prompt Here"
              minRows={5}
              maxRows={5}
              onChange={handleExistingPromptChange}
              value={existingPrompt}
            />
            <Box flexGrow={1} />
          </>
        )}
        <Box
          sx={{
            display: "flex",
            flex: 1,
            justifyContent: "right",
            alignSelf: "flex-end",
            flexDirection: "column",
            overflowY: "auto",
            width: "100%",
          }}
        >
          {conversation.map((item, index) => (
            <ConversationBox key={index}>
              {editingIndex === index ? (
                <EditablePromptItem
                  item={item}
                  index={index}
                  conversation={conversation}
                  setConversation={setConversation}
                  handleCancelEdit={handleCancelEdit}
                  handleUpdatePrompt={handleUpdatePrompt}
                />
              ) : (
                <PromptItemDisplay
                  item={item}
                  index={index}
                  loading={loading}
                  handleEditClick={handleEditClick}
                />
              )}
            </ConversationBox>
          ))}
          {loading ? <LoadingIndicator /> : null}
        </Box>
        {/* Follow up prompt */}
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: theme.spacing(1.5),
          }}
        >
          <Typography
            variant="subtitle2"
            fontWeight="fontWeightBold"
            sx={{ color: "text.disabled", paddingLeft: theme.spacing(0.25) }}
          >
            What would you like to Improve?
          </Typography>

          <TextField
            multiline
            rows={3}
            sx={{
              background: action.hover,
              borderRadius: "10px",
              padding: "0px",
            }}
            placeholder="Enter what would you like to improve in the prompt"
            value={improvementRequirements}
            onChange={handleImprovementRequirementsChange}
            fullWidth
          />
          <LoadingButton
            loading={loading}
            variant="contained"
            color="primary"
            type="submit"
            disabled={!existingPrompt || !improvementRequirements}
            fullWidth
            onClick={handleGenerate}
          >
            Submit
          </LoadingButton>
        </Box>
      </Box>

      {/* right side result */}
      <Divider
        orientation="vertical"
        sx={{
          marginX: theme.spacing(0.75),
        }}
      />
      <StyledBox>
        <GenerateResult
          hideInitialText
          output={generating ? "Generating..." : output}
          showUtils={showOutputControls}
          onClose={onClose}
          enabled={generating}
          loading={loading}
          isApplyButtonEnabled={isApplyButtonEnabled}
          onApply={handleApply}
        />
      </StyledBox>
    </Box>
  );
};

ImprovePromptDrawer.propTypes = {
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
  resetFields: PropTypes.func,
  setIsDirty: PropTypes.func,
};

export default ImprovePromptDrawer;
