import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  IconButton,
  Box,
  useTheme,
  Divider,
  Stack,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";

const ConfirmRunEvaluations = ({
  open,
  onClose,
  onConfirm,
  selectedUserEvalList,
  loading,
}) => {
  const theme = useTheme();
  const [evalsToRun, setEvalsToRun] = useState([]);

  useEffect(() => {
    if (open) {
      setEvalsToRun(selectedUserEvalList || []);
    }
  }, [selectedUserEvalList, open]);

  const handleRemoveEval = (id) => {
    const updated = evalsToRun.filter((e) => e.id !== id);
    setEvalsToRun(updated);
    if (updated.length === 0) {
      onClose();
    }
  };

  const handleConfirm = () => {
    onConfirm(evalsToRun);
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      PaperProps={{
        sx: {
          maxWidth: 533,
          maxHeight: 400,
          display: "flex",
          flexDirection: "column",
          paddingX: theme.spacing(2),
        },
      }}
    >
      <DialogTitle
        sx={{
          paddingTop: theme.spacing(1.5),
          paddingBottom: theme.spacing(0.5),
          paddingX: theme.spacing(0),
        }}
      >
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography fontSize={16} fontWeight={700} color="text.primary">
            Are you sure you want to run the following evaluation
            {evalsToRun.length !== 1 && "s"}?
          </Typography>
          <IconButton onClick={onClose} sx={{ p: 0 }}>
            <Iconify icon="line-md:close" color="text.primary" />
          </IconButton>
        </Box>
        <Typography variant="body2" color="text.secondary">
          This will overwrite previous evaluation results.
        </Typography>
      </DialogTitle>

      <DialogContent
        sx={{
          overflow: "hidden",
          flexGrow: 1,
          px: theme.spacing(0),
          mb: theme.spacing(1),
        }}
      >
        <Divider />
        <Stack
          spacing={theme.spacing(1)}
          sx={{
            maxHeight: 160,
            overflowY: "auto",
            mt: theme.spacing(1.5),
            "&::-webkit-scrollbar": {
              width: "4px",
            },
            "&::-webkit-scrollbar-thumb": {
              backgroundColor: "action.disabledBackground",
              borderRadius: "4px",
            },
          }}
        >
          {evalsToRun.map((evalItem) => (
            <Box
              key={evalItem.id}
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              height={46}
              border={1}
              borderColor="divider"
              p={theme.spacing(1.5)}
              borderRadius="8px"
            >
              <Typography fontSize={14} fontWeight={500}>
                {evalItem.name}
              </Typography>
              {evalsToRun.length > 1 && (
                <IconButton
                  aria-label="remove-eval"
                  onClick={() => handleRemoveEval(evalItem.id)}
                  sx={{ width: 16, height: 16, p: 0 }}
                >
                  <Iconify
                    icon="line-md:close"
                    color="text.primary"
                    width={16}
                    height={16}
                  />
                </IconButton>
              )}
            </Box>
          ))}
        </Stack>
      </DialogContent>

      <DialogActions
        sx={{
          px: theme.spacing(0),
          pt: theme.spacing(2),
          pb: theme.spacing(2),
        }}
      >
        <Button
          variant="outlined"
          color="inherit"
          onClick={onClose}
          size="small"
          sx={{
            fontSize: "12px",
            fontWeight: 500,
            lineHeight: "18px",
            paddingX: "24px",
            paddingY: "6px",
          }}
        >
          Cancel
        </Button>
        <LoadingButton
          loading={loading}
          variant="contained"
          color="primary"
          size="small"
          onClick={handleConfirm}
          disabled={evalsToRun.length === 0}
          sx={{
            fontSize: "12px",
            fontWeight: 500,
            lineHeight: "18px",
            paddingX: "24px",
            paddingY: "6px",
          }}
        >
          Run Evaluations
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

ConfirmRunEvaluations.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
  selectedUserEvalList: PropTypes.array,
  loading: PropTypes.bool,
};

export default ConfirmRunEvaluations;
