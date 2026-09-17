import React, { useState } from "react";
import {
  Box,
  Button,
  Modal,
  Typography,
  TextField,
  Paper,
  IconButton,
  Radio,
  RadioGroup,
  FormControlLabel,
  FormControl,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const style = {
  position: "absolute",
  top: "50%",
  left: "50%",
  transform: "translate(-50%, -50%)",
  width: "616px",
  bgcolor: "background.paper",
  borderRadius: 1,
  boxShadow: 24,
  px: 1.5,
  py: 0.5,
};

function AddToFeedBackModal({ open, handleClose, setOpenSubmitFeedback }) {
  const [rightValue, setRightValue] = useState("");
  const [improvement, setImprovement] = useState("");

  const handleSubmit = () => {
    // Process feedback submission
    setOpenSubmitFeedback(true);
    handleClose();
  };

  return (
    <Modal
      open={open}
      onClose={handleClose}
      aria-labelledby="feedback-modal-title"
      aria-describedby="feedback-modal-description"
    >
      <Paper sx={style}>
        {/* Header with close button */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            mt: 0.5,
          }}
        >
          <Typography
            sx={{ fontWeight: 700, fontSize: "16px", color: "text.primary" }}
          >
            Feedbacks for Auto Learning
          </Typography>
          <IconButton onClick={handleClose} size="small">
            <Iconify icon="mdi:close" color="text.primary" width={22} />
          </IconButton>
        </Box>

        <Typography variant="s1" sx={{ color: "text.secondary", width: "90%" }}>
          Help us refined evals. Share any issues and we&apos;ll use your
          feedback to improve it automatically
        </Typography>

        {/* Explanation Section */}
        <Box sx={{ pb: 1, pt: 1.5 }}>
          <Typography
            variant="s1"
            sx={{ color: "text.primary", fontWeight: 500 }}
          >
            Explanation
          </Typography>

          <Box
            sx={{
              mt: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 2,
            }}
          >
            <Typography variant="body2" sx={{ color: "text.primary" }}>
              <Iconify
                icon="mdi:circle-medium"
                width={10}
                color="text.primary"
              />
              Lack of actual content in question, context, and answer fields;
              provide real, substantive information for proper evaluation.
            </Typography>
            <Typography variant="body2" sx={{ color: "text.primary" }}>
              <Iconify
                icon="mdi:circle-medium"
                width={10}
                color="text.primary"
              />
              Inability to assess relevance, accuracy, or alignment with
              criteria; ensure all input fields contain meaningful content
              related to the task.
            </Typography>
          </Box>
        </Box>

        {/* Right Value Section */}
        <Box sx={{ pb: 2, pt: 1 }}>
          <Typography
            sx={{
              fontSize: "14px",
              fontWeight: 500,
              color: "text.primary",
              mb: 1,
            }}
          >
            Write a right value
          </Typography>
          <TextField
            fullWidth
            placeholder="Write a right value here"
            variant="outlined"
            value={rightValue}
            onChange={(e) => setRightValue(e.target.value)}
            multiline
            rows={3}
            sx={{
              bgcolor: "background.neutral",
              borderRadius: 1,
              "& .MuiInputLabel-root": {
                color: "text.disabled",
              },
            }}
          />
        </Box>

        {/* Improvement Section */}
        <Box>
          <Typography
            variant="s1"
            sx={{ fontWeight: 500, color: "text.primary", mb: 1 }}
          >
            What would you like to improve
          </Typography>
          <TextField
            fullWidth
            placeholder="What is wrong with the original explanation? Please be specific as possible in your argument."
            variant="outlined"
            value={improvement}
            onChange={(e) => setImprovement(e.target.value)}
            multiline
            rows={7}
            sx={{
              bgcolor: "background.neutral",
              borderRadius: 1,
              "& .MuiInputLabel-root": {
                color: "text.disabled",
              },
            }}
          />
        </Box>

        {/* Action Buttons */}
        <Box
          sx={{ display: "flex", justifyContent: "space-evenly", mt: 4, mb: 2 }}
        >
          <Button
            variant="outlined"
            onClick={handleClose}
            sx={{
              width: "48%",
              fontSize: "12px",
              fontWeight: 500,
              color: "text.secondary",
              height: "28px",
            }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleSubmit}
            sx={{
              width: "48%",
              fontSize: "12px",
              fontWeight: 600,
              color: "primary.contrastText",
              backgroundColor: "primary.main",
              height: "28px",
              "&:hover": { backgroundColor: "primary.dark" },
            }}
          >
            Submit Feedback
          </Button>
        </Box>
      </Paper>
    </Modal>
  );
}

export function SubmitFeedBackModal({
  openSubmitFeedback,
  handleSubmitFeedBackClose,
}) {
  const [selectedOption, setSelectedOption] = useState("re-tune");

  const handleOptionChange = (event) => {
    setSelectedOption(event.target.value);
  };

  return (
    <Modal
      open={openSubmitFeedback}
      onClose={handleSubmitFeedBackClose}
      aria-labelledby="feedback-modal-title"
      aria-describedby="feedback-modal-description"
    >
      <Paper sx={style}>
        {/* Header with close button */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            mt: 0.5,
          }}
        >
          <Typography
            sx={{ fontWeight: 700, fontSize: "16px", color: "text.primary" }}
          >
            Your feedback is submitted
          </Typography>
          <IconButton onClick={handleSubmitFeedBackClose} size="small">
            <Iconify icon="mdi:close" color="text.primary" width={22} />
          </IconButton>
        </Box>

        {/* QA Correctness Section */}
        <Box
          sx={{
            mt: 1.5,
            bgcolor: "background.default",
            borderRadius: 1,
            py: 1.3,
            px: 1,
          }}
        >
          <Box sx={{ width: "87%" }}>
            <Typography
              variant="s1"
              sx={{ fontWeight: 500, color: "text.secondary", mb: 0.4 }}
            >
              QA_Correctness
            </Typography>
            <Typography
              variant="s3"
              sx={{
                fontSize: "12px",
                color: "text.primary",
                mb: 1,
                lineHeight: "22px",
              }}
            >
              Measure whether the LLM’s response is supported by (or baked in)
              the context provided. Context Adherence can be auto improved via
              continuous learning from human feedback. Give feedback via
              hovering over any metric value that seems to be incorrect. Future
              AGI will automatically incorporate your feedback into the metric.
            </Typography>
          </Box>
        </Box>

        {/* Options Section */}
        <Box sx={{ my: 1, pl: 0.5 }}>
          <Typography
            variant="s1"
            sx={{ fontWeight: 500, color: "text.primary", mb: 2 }}
          >
            Select one of the options
          </Typography>

          <FormControl component="fieldset">
            <RadioGroup
              aria-label="feedback-options"
              name="feedback-options"
              value={selectedOption}
              onChange={handleOptionChange}
            >
              <FormControlLabel
                value="re-tune"
                control={<Radio />}
                label={
                  <Box>
                    <Typography
                      variant="s1"
                      sx={{ fontWeight: 500, color: "text.secondary" }}
                    >
                      Re-tune
                    </Typography>
                    <Typography
                      variant="s1"
                      sx={{ color: "text.secondary", mt: 0.5 }}
                    >
                      We&apos;ll create a new version of this metric and use it
                      in all future invocations
                    </Typography>
                  </Box>
                }
                sx={{ mb: 2, alignItems: "flex-start" }}
              />

              <FormControlLabel
                value="re-calculate"
                control={<Radio />}
                label={
                  <Box>
                    <Typography variant="s1" sx={{ color: "text.secondary" }}>
                      Re-calculate for this trace
                    </Typography>
                    <Typography
                      variant="s1"
                      sx={{ color: "text.secondary", mt: 0.5 }}
                    >
                      We&apos;ll create a new version of this metric and use it
                      in all future invocations. We&apos;ll also recalculate it
                      on this span. This might take a while.
                    </Typography>
                  </Box>
                }
                sx={{ alignItems: "flex-start" }}
              />
            </RadioGroup>
          </FormControl>
        </Box>

        {/* Action Buttons */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            mt: 4,
            mb: 2,
            px: 0.5,
          }}
        >
          <Button
            variant="outlined"
            onClick={handleSubmitFeedBackClose}
            sx={{
              height: "30px",
              width: "49%",
              fontSize: "12px",
              fontWeight: 500,
              color: "text.secondary",
              borderColor: "divider",
              "&:hover": {
                borderColor: "divider",
                bgcolor: "background.neutral",
              },
            }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleSubmitFeedBackClose}
            sx={{
              height: "30px",
              width: "49%",
              fontSize: "12px",
              fontWeight: 600,
              color: "primary.contrastText",
              backgroundColor: "primary.main",
              "&:hover": {
                backgroundColor: "primary.dark",
              },
            }}
          >
            Apply
          </Button>
        </Box>
      </Paper>
    </Modal>
  );
}

// Fix for prop-types validation error by adding prop-types for SubmitFeedBackModal
SubmitFeedBackModal.propTypes = {
  openSubmitFeedback: PropTypes.bool,
  handleSubmitFeedBackClose: PropTypes.func,
};

AddToFeedBackModal.propTypes = {
  open: PropTypes.bool,
  handleClose: PropTypes.func,
  setOpenSubmitFeedback: PropTypes.func,
};

export default AddToFeedBackModal;
