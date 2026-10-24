import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  Radio,
  RadioGroup,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import { RerunTestOptions } from "./common";
import { useRerunTest } from "src/api/tests/testDetails";
import { AGENT_TYPES } from "../agents/constants";

const RerunModal = ({
  open,
  onClose,
  selectedNodes,
  selectAll,
  selectedCount,
  executionId,
  agentType,
}) => {
  const [rerunType, setRerunType] = useState(
    agentType === AGENT_TYPES.CHAT
      ? RerunTestOptions.EVAL_ONLY
      : RerunTestOptions.CALL_AND_EVAL,
  );

  const { mutate: rerunTest, isPending: isRerunPending } = useRerunTest(
    executionId,
    {
      onSuccess: () => {
        onClose();
      },
    },
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 560 } }}
      fullWidth
    >
      <DialogTitle
        sx={{
          padding: 2,
          borderBottom: "1px solid",
          borderColor: "action.hover",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography typography="m3" fontWeight="fontWeightMedium">
          Rerun Test
        </Typography>
        <IconButton
          onClick={onClose}
          sx={{
            color: "text.primary",
          }}
          size="small"
        >
          <Iconify icon="solar:close-linear" width={16} height={16} />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: "16px !important",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <Typography typography="m3" fontWeight="fontWeightMedium">
            {selectedCount} rows selected
          </Typography>
          <FormControl>
            {agentType === AGENT_TYPES.CHAT ? (
              <Typography typography="m3">
                Only evals can be re-run for selected chats
              </Typography>
            ) : (
              <RadioGroup
                name="radio-buttons-group"
                value={rerunType}
                onChange={(e) => setRerunType(e.target.value)}
              >
                <FormControlLabel
                  value={RerunTestOptions.EVAL_ONLY}
                  control={<Radio />}
                  label="Run Evals"
                />
                {agentType === AGENT_TYPES.VOICE && (
                  <FormControlLabel
                    value={RerunTestOptions.CALL_AND_EVAL}
                    control={<Radio />}
                    label="Run test + Evals"
                  />
                )}
              </RadioGroup>
            )}
          </FormControl>
        </Box>
        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            justifyContent: "flex-end",
            gap: 2,
            alignItems: "center",
          }}
        >
          <Button onClick={onClose} variant="outlined" size="small">
            Cancel
          </Button>
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            loading={isRerunPending}
            onClick={() => {
              rerunTest({
                select_all: selectAll,
                rerun_type: rerunType,
                call_execution_ids: selectedNodes,
              });
            }}
          >
            Run
          </LoadingButton>
        </Box>
      </DialogContent>
    </Dialog>
  );
};

RerunModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  selectedNodes: PropTypes.array.isRequired,
  selectAll: PropTypes.bool.isRequired,
  selectedCount: PropTypes.number.isRequired,
  executionId: PropTypes.string.isRequired,
  agentType: PropTypes.string,
};

export default RerunModal;
