import React, { useEffect, useMemo } from "react";
import { Box, Divider, Drawer, IconButton, Skeleton } from "@mui/material";
import PropTypes from "prop-types";
import { ACTION_TYPES, OUTPUT_TYPES } from "./feedbackConstant";
import { useForm } from "react-hook-form";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import AddEvalsFeedbackForm from "./AddEvalsFeedbackForm";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";

const createValidationSchema = z.object({
  actionType: z.enum([ACTION_TYPES.RETUNE, ACTION_TYPES.RECALCULATE]),
  value: z.union([
    z.string().min(1, "Feedback value is required"),
    z.boolean(),
    z.number(),
    z.array(z.string()).min(1, "At least one choice must be selected"),
  ]),
  explanation: z.string().min(1, "Feedback improvement is required"),
});

const AddEvalsFormLoading = ({ handleClose }) => {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "16px",
        height: "100vh",
        padding: 2,
      }}
    >
      <Box
        sx={{ display: "flex", gap: "4px", justifyContent: "space-between" }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1, flex: 1 }}>
          <Skeleton height={20} width={250} />
          <Skeleton height={40} />
        </Box>
        <IconButton onClick={handleClose} size="small" sx={{ height: "32px" }}>
          {/* @ts-ignore */}
          <Iconify icon="akar-icons:cross" sx={{ color: "text.primary" }} />
        </IconButton>
      </Box>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <Skeleton height={20} width={250} />
        <Skeleton height={180} />
      </Box>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <Skeleton height={20} width={200} />
        <Skeleton height={250} />
      </Box>
      <Divider orientation="horizontal" />
      <Skeleton height={300} />
    </Box>
  );
};

AddEvalsFormLoading.propTypes = {
  handleClose: PropTypes.func,
};

const AddEvalsFeedbackDrawerChild = ({
  output,
  selectedAddFeedback,
  onClose,
  refreshGrid = (_option) => {},
  evalsId,
  existingFeedback,
}) => {
  const isEdit = !!existingFeedback;
  const handleClose = () => {
    onClose();
  };

  const {
    data: configData,
    error: feedbackError,
    isPending,
  } = useQuery({
    queryKey: ["evalsConfig", evalsId],
    queryFn: () => {
      return axios.get(endpoints.develop.eval.getEvalConfigs, {
        params: { eval_id: evalsId },
      });
    },
    enabled: !!evalsId,
    select: (data) => data?.data?.result?.eval,
    staleTime: 1000 * 10,
  });

  const outputType = useMemo(() => {
    if (configData?.output === "score") {
      return OUTPUT_TYPES.INT;
    }
    if (configData?.output === "Pass/Fail") {
      return OUTPUT_TYPES.BOOL;
    }
    if (configData?.output === "choices") {
      return OUTPUT_TYPES.STR_LIST;
    }
    return OUTPUT_TYPES.TEXT;
  }, [configData]);

  const { control, reset, handleSubmit, formState } = useForm({
    defaultValues: {
      actionType: "",
      value: outputType === OUTPUT_TYPES.STR_LIST ? [] : "",
      explanation: "",
    },
    resolver: zodResolver(createValidationSchema),
  });
  // Pre-fill form when editing existing feedback
  useEffect(() => {
    if (existingFeedback && !isPending) {
      let parsedValue = existingFeedback.value || "";
      if (outputType === OUTPUT_TYPES.STR_LIST) {
        try {
          parsedValue = JSON.parse(parsedValue);
        } catch {
          parsedValue = [];
        }
      }
      reset({
        value: parsedValue,
        explanation: existingFeedback.explanation || "",
        actionType: existingFeedback.actionType || "",
      });
    }
  }, [existingFeedback, outputType, isPending, reset]);

  const { mutate: submitFeedback, isPending: isSubmitting } = useMutation({
    mutationFn: (formData) =>
      axios.post(endpoints.develop.eval.addEvalsFeedback, formData),
    onSuccess: (data) => {
      // @ts-ignore
      enqueueSnackbar(data?.data?.result?.message, {
        variant: "success",
      });
      reset();
      onClose(true);
      refreshGrid({ purge: true });
    },
  });

  const onSubmit = (payload) => {
    const { actionType, ...rest } = payload;
    submitFeedback({
      ...rest,
      action_type: actionType,
      log_id: selectedAddFeedback?.id,
      ...(outputType === OUTPUT_TYPES.STR_LIST && {
        value: JSON.stringify(payload.value),
      }),
    });
  };

  if (isPending) {
    return <AddEvalsFormLoading handleClose={handleClose} />;
  }

  return (
    <AddEvalsFeedbackForm
      handleClose={handleClose}
      control={control}
      explanation={output.reason}
      isEdit={isEdit}
      outputType={outputType}
      feedbackError={Boolean(feedbackError)}
      choices={configData?.choices}
      handleSubmitForm={onSubmit}
      handleSubmit={handleSubmit}
      disabled={!formState.isValid}
      loading={isSubmitting}
      retuneOptions={[
        {
          title: "Re-tune",
          description:
            "A new version of this metric will be created and used in all future evaluations.",
          value: ACTION_TYPES.RETUNE,
        },
        {
          title: "Re-calculate and re-tune",
          description:
            "We will create a new version of this metric and use in future evaluations. We will also recalculate all past evaluation runs using this feedback. This may take some time.",
          value: ACTION_TYPES.RECALCULATE,
        },
      ]}
    />
  );
};

AddEvalsFeedbackDrawerChild.propTypes = {
  output: PropTypes.object,
  selectedAddFeedback: PropTypes.object,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  evalsId: PropTypes.string,
  existingFeedback: PropTypes.object,
};

const AddEvalsFeedbackDrawer = ({ open, onClose, ...rest }) => {
  return (
    <Drawer
      anchor="right"
      open={open}
      variant="temporary"
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "670px",
          position: "fixed",
          zIndex: 10,
          boxShadow: "-10px 0px 100px #00000035",
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <AddEvalsFeedbackDrawerChild onClose={onClose} {...rest} />
    </Drawer>
  );
};

export default AddEvalsFeedbackDrawer;

AddEvalsFeedbackDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  output: PropTypes.object,
  refreshGrid: PropTypes.func,
  selectedAddFeedback: PropTypes.object,
  evalsId: PropTypes.string,
  existingFeedback: PropTypes.object,
};
