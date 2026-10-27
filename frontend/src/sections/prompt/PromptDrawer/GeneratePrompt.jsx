import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import { Box, Button, TextField, Typography, Tooltip } from "@mui/material";
import styled from "@emotion/styled";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import Divider from "@mui/material/Divider";
import { action } from "src/theme/palette";
import axiosInstance, { endpoints } from "src/utils/axios";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { m } from "framer-motion";

import DrawerHeaderbar from "../NewPrompt/TopMenuOptions/DrawerHeaderbar";

import GenerateResult from "./GenerateResult";
import logger from "src/utils/logger";

const StyledBox = styled(Box)(() => ({
  display: "flex",
  width: "100%",
}));

const buttons = [
  { icon: <Iconify icon="ph:pen-nib-light" />, label: "Write me an email" },
  {
    icon: <Iconify icon="solar:document-outline" />,
    label: "Summarize a document",
  },
  { icon: <Iconify icon="solar:code-bold" />, label: "Translate code" },
  { icon: <Iconify icon="carbon:product" />, label: "Recommend a product" },
  { icon: <Iconify icon="basil:copy-outline" />, label: "Content moderation" },
];

const GeneratePrompt = (props) => {
  const { onClose, onApply, setIsDirty } = props;
  const [inputs, setInputs] = useState({
    prompt: "",
    followUpPrompt: "",
  });
  const [output, setOutput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversation, setConversation] = useState([]);
  const [showButtons, setShowButtons] = useState(true);
  const [editingIndex, setEditingIndex] = useState(null);
  const [followup, setFollowup] = useState(false);
  const [originalPrompts, setOriginalPrompts] = useState({});
  const [showUtils] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [isApplyButtonEnabled, setIsApplyButtonEnabled] = useState(false);
  const [hasApplied] = useState(false);

  useEffect(() => {
    if (!hasApplied) {
      const isDirty = inputs.prompt !== "" || conversation.length > 0;
      if (setIsDirty) {
        setIsDirty(isDirty);
      }
    } else {
      if (setIsDirty) {
        setIsDirty(false);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputs, conversation, hasApplied]);

  const handleInputChange = (field) => (e) => {
    setInputs((prev) => ({ ...prev, [field]: e.target.value }));
    trackEvent(Events.userPromptEntered, {
      [PropertyName.formFields]: inputs,
    });
  };

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

  const handleImprove = async () => {
    trackEvent(Events.followUpPromptClicked);
    setLoading(true);
    setGenerating(true);
    setIsApplyButtonEnabled(false);
    setConversation((prev) => [
      ...prev,
      {
        prompt: inputs.prompt,
      },
    ]);
    setInputs((prev) => ({ ...prev, prompt: "" }));
    try {
      const res = await axiosInstance.post(
        endpoints.develop.runPrompt.updatePrompt,
        {
          existingPrompt: output,
          improvementRequirements: inputs.prompt,
        },
      );
      setOutput(res.data.result.prompt);
      setOriginalPrompts((prev) => ({
        ...prev,
        [conversation.length]: inputs.prompt,
      }));
      setInputs((prev) => ({ ...prev, prompt: "" }));
      setIsApplyButtonEnabled(true);
    } catch (error) {
      logger.error("Error generating prompt:", error);
      setOutput("");
    } finally {
      setGenerating(false);
      setLoading(false);
    }
  };

  const handleGenerate = async () => {
    setShowButtons(false);
    setLoading(true);
    setIsApplyButtonEnabled(false);
    setGenerating(true);
    setConversation((prev) => [
      ...prev,
      {
        prompt: inputs.prompt,
      },
    ]);
    setInputs((prev) => ({ ...prev, prompt: "" }));
    trackEvent(Events.GeneratePromptClicked, {
      [PropertyName.formFields]: {
        statement: inputs.prompt,
      },
    });

    try {
      const res = await axiosInstance.post(
        endpoints.develop.runPrompt.generatePrompt,
        {
          statement: inputs.prompt,
        },
      );
      setOutput(res.data.result.prompt);
      setIsApplyButtonEnabled(true);
      setOriginalPrompts((prev) => ({
        ...prev,
        [conversation.length]: inputs.prompt,
      }));
      // setInputs((prev) => ({ ...prev, prompt: "" }));
      setFollowup(true);
    } catch (error) {
      logger.error("Error generating prompt:", error);
      setOutput("");
    } finally {
      setLoading(false);
      setGenerating(false);
    }
  };

  const handleButtonClick = (label) => {
    trackEvent(Events.samplePromptSelected);
    setInputs({
      ...inputs,
      prompt: label,
    });
  };
  const handleEditClick = (index) => {
    setEditingIndex(index);
  };

  const handleCancelEdit = () => {
    setEditingIndex(null);
    setConversation((prev) => {
      const updatedConversation = [...prev];
      updatedConversation[editingIndex].prompt =
        originalPrompts[editingIndex] ||
        updatedConversation[editingIndex].prompt;
      return updatedConversation;
    });
  };

  const handleUpdatePrompt = async (index, followUpPrompt) => {
    setLoading(true);
    setEditingIndex(null);
    setGenerating(true);
    setIsApplyButtonEnabled(false);
    setInputs((prev) => ({ ...prev, prompt: "" }));
    setConversation((prev) => {
      const updatedConversation = [...prev];
      updatedConversation[editingIndex].prompt =
        originalPrompts[editingIndex] ||
        updatedConversation[editingIndex].prompt;
      return updatedConversation;
    });
    const previousPrompt =
      originalPrompts[index] || conversation[index]?.prompt || ""; // Get previous prompt
    setConversation((prev) => [
      ...prev,
      {
        prompt: followUpPrompt,
      },
    ]);
    setOriginalPrompts((prev) => ({
      ...prev,
      [conversation.length]: followUpPrompt,
    }));

    trackEvent(Events.promptEdited, {
      previousPrompt: previousPrompt,
      newPrompt: followUpPrompt,
    });
    try {
      const res = await axiosInstance.post(
        endpoints.develop.runPrompt.updatePrompt,
        {
          existingPrompt: output,
          improvementRequirements: followUpPrompt,
        },
      );
      setOutput(res.data.result.prompt);
      setInputs((prev) => ({ ...prev, prompt: "" }));
      setIsApplyButtonEnabled(true);
    } catch (error) {
      logger.error("Error updating prompt:", error);
      setOutput("");
    } finally {
      setLoading(false);
      setEditingIndex(null);
      setGenerating(false);
    }
  };
  const handleOnApply = (data) => {
    trackEvent(Events.generatedPromptApplied, {
      [PropertyName.formFields]: {
        generatedPrompt: output,
      },
    });
    onApply(data);
  };

  return (
    <Box
      sx={{ display: "flex", flex: 1, paddingRight: "0px", overflow: "hidden" }}
      padding="15px"
      maxHeight={"100vh"}
    >
      {/* left side */}
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          width: "105%",
          marginRight: 1.5,
          marginTop: "5px",
        }}
      >
        <DrawerHeaderbar
          title="Generate a prompt"
          onClose={onClose}
          showCloseIcon={false}
        />
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "15px",
            flex: 1,
            justifyContent: "space-between	",
          }}
        >
          {/* sub title */}
          <Box sx={{ display: "flex", gap: 0.6, alignItems: "center" }}>
            {/* <Iconify icon="solar:info-circle-bold" color="#637381" width="15px" /> */}
            <Typography color={"text.secondary"} variant="caption">
              You can generate a structured prompt by sharing basic details
              about your task
            </Typography>
          </Box>
          <Divider />
          {/* Buttons */}
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              flex: 1,
              overflowY: "auto",
            }}
          >
            <Box
              sx={{
                flexGrow: 1,
                display: "flex",
                justifyContent: "right",
                padding: 1,
                flexDirection: "column",
                overflowY: "auto",
              }}
            >
              {conversation.map((item, index) => (
                <Box
                  key={index}
                  sx={{
                    marginBottom: 1,
                    display: "flex",
                    justifyContent: "right",
                  }}
                >
                  {editingIndex === index ? (
                    <Box
                      sx={{
                        position: "relative",
                        width: "100%",
                        height: "auto",
                      }}
                    >
                      <TextField
                        multiline
                        rows={2}
                        sx={{
                          background: action.hover,
                          borderRadius: 1.4,
                          width: "100%",
                          marginBottom: 1,
                          paddingBottom: "25px",
                          height: "auto",

                          "& .MuiOutlinedInput-root": {
                            "& fieldset": {
                              border: "none", // Remove the border
                            },
                            "&:hover fieldset": {
                              border: "none", // Remove the border on hover
                            },
                            "&.Mui-focused fieldset": {
                              border: "none", // Remove the border when focused
                            },
                          },
                          "& .MuiInputBase-input": {
                            // paddingY: 'px'
                          },
                        }}
                        value={item.prompt}
                        onChange={(e) => {
                          const updatedConversation = [...conversation];
                          updatedConversation[index].prompt = e.target.value;
                          setConversation(updatedConversation);
                        }}
                        fullWidth
                      />
                      <Box
                        sx={{
                          position: "absolute",
                          bottom: 3,
                          right: 6,
                          fontSize: "0.75rem",
                          padding: 0.6,
                          paddingBottom: 1.3,
                        }}
                      >
                        <Button
                          onClick={handleCancelEdit}
                          size="small"
                          sx={{
                            fontSize: "0.7rem",
                            minWidth: "50px",
                            margin: 0.5,
                            paddingX: "12px",
                            color: "text.primary",
                            background: "action.selected",
                            borderRadius: "10px",
                          }}
                        >
                          Cancel
                        </Button>
                        <Button
                          variant="contained"
                          color="primary"
                          onClick={() => handleUpdatePrompt(index, item.prompt)}
                          size="small"
                          sx={{
                            fontSize: "0.7rem",
                            minWidth: "45px",

                            borderRadius: "10px",
                            paddingX: "12px",
                          }}
                        >
                          Update
                        </Button>
                      </Box>
                    </Box>
                  ) : (
                    <Typography
                      variant="subtitle2"
                      sx={{
                        color: "text.primary",
                        background: "action.hover",
                        padding: 1.6,
                        paddingBottom: 4,
                        paddingRight: 4,
                        borderRadius: 1.4,
                        fontWeight: "400",
                        wordWrap: "break-word",
                        position: "relative",
                      }}
                    >
                      {item.prompt}
                      <Box
                        sx={{
                          position: "absolute",
                          bottom: 0,
                          right: 0,
                          padding: 0.4,
                          paddingRight: 1,
                          cursor: loading ? "not-allowed" : "pointer",
                        }}
                      >
                        <Tooltip title={"Edit this Prompt"}>
                          <span
                            onClick={
                              loading ? () => {} : () => handleEditClick(index)
                            }
                          >
                            <Iconify
                              icon="material-symbols:edit-outline"
                              color="text.disabled"
                              style={{
                                pointerEvents: loading ? "none" : "auto",
                              }}
                            />
                          </span>
                        </Tooltip>
                      </Box>
                    </Typography>
                  )}
                </Box>
              ))}
              {loading ? <LoadingIndicator /> : null}
            </Box>

            {showButtons ? (
              <>
                <Divider />

                <Box
                  sx={{
                    display: "flex",
                    flexWrap: "wrap",
                    gapX: "4px",
                    gapY: "2px",
                    marginTop: "10px",
                    marginLeft: "-11px",
                  }}
                >
                  {buttons.map(({ icon, label }) => (
                    <Button
                      key={label}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: "4px",
                        marginY: "3px",
                        color: "text.secondary",
                        fontSize: "12px",
                        // padding: '6px 10px',
                        minWidth: "100px",
                      }}
                      onClick={() => handleButtonClick(label)}
                    >
                      {React.cloneElement(icon, {
                        fontSize: "12px",
                        padding: "1px",
                      })}
                      <Typography
                        variant="caption"
                        fontWeight="fontWeightRegular"
                        sx={{
                          color: "text.secondary",
                          fontSize: "13px",
                        }}
                      >
                        {label}
                      </Typography>
                    </Button>
                  ))}
                </Box>
              </>
            ) : null}
          </Box>

          {/* Follow up prompt */}
          <Box
            display={"flex"}
            flexDirection={"column"}
            gap={"12px"}
            mb={"3px"}
          >
            <Typography
              variant="subtitle2"
              fontWeight="fontWeightBold"
              sx={{ color: "text.disabled" }}
            >
              {followup ? "Follow-up prompt" : "Describe your prompt"}
            </Typography>
            <TextField
              multiline
              rows={3}
              sx={{
                background: action.hover,
                borderRadius: "10px",
                padding: "0px",
              }}
              placeholder={
                followup
                  ? "Improve the Tone and grammer of the prompt"
                  : "Write your Prompt here"
              }
              value={inputs.prompt}
              onChange={handleInputChange("prompt")}
              fullWidth
            />
            <LoadingButton
              loading={loading}
              variant="contained"
              color="primary"
              type="submit"
              disabled={!inputs.prompt}
              fullWidth
              onClick={followup ? handleImprove : handleGenerate}
            >
              {followup ? "Follow-up prompt" : "Generate"}
            </LoadingButton>
          </Box>
        </Box>
      </Box>

      {/* right side result */}
      <Divider
        orientation="vertical"
        sx={{
          marginX: "10px",
        }}
      />

      <StyledBox>
        <GenerateResult
          hideInitialText
          output={generating ? "Generating..." : output}
          showUtils={showUtils}
          onClose={onClose}
          enabled={generating}
          loading={loading}
          isApplyButtonEnabled={isApplyButtonEnabled}
          onApply={handleOnApply}
        />
      </StyledBox>
    </Box>
  );
};

GeneratePrompt.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
  setIsDirty: PropTypes.func,
};

export default GeneratePrompt;
