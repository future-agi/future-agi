import React from "react";
import PropType from "prop-types";
import { ModalComponent } from "src/components/ModalComponent";
import { Box, FormControl, Typography } from "@mui/material";
import { useForm } from "react-hook-form";
import { FormSelectField } from "src/components/FormSelectField";
import { CreateModelFormValidation } from "./validation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { useSnackbar } from "src/components/snackbar";
import { useNavigate } from "react-router";
import { ModelTypeOptions } from "src/utils/constant";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const CreateModelModal = ({ open, onClose }) => {
  const { enqueueSnackbar } = useSnackbar();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const { handleSubmit, control, reset } = useForm({
    resolver: zodResolver(CreateModelFormValidation),
    defaultValues: {
      modelName: "",
      modelTypeId: null,
    },
  });

  const closeAndClear = () => {
    reset();
    onClose();
  };

  const { mutate: createModel, isPending } = useMutation({
    mutationFn: (body) => axios.post(endpoints.model.create, body),
    onSuccess: (data) => {
      // trackEvent(Events.createModelComplete, {
      //   //@ts-ignore
      //   "Model Name": v?.modelName,
      //   //@ts-ignore
      //   "Model Type": ModelTypeOptions.find((o) => o.value === v.modelTypeId)
      //     ?.label,
      // });
      enqueueSnackbar("Model created successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["models"] });
      closeAndClear();
      // navigate to dataset tab
      navigate(`/dashboard/models/${data.data.data.id}/datasets`);
    },
  });

  const onSubmit = (formValues) => {
    createModel(formValues);
  };
  return (
    <ModalComponent open={open} onClose={closeAndClear}>
      <form onSubmit={handleSubmit(onSubmit)}>
        <Box
          sx={{
            padding: "20px",
            display: "flex",
            flexDirection: "column",
            gap: 3,
          }}
        >
          <Typography variant="h5">New model</Typography>
          <FormControl fullWidth sx={{ flex: 1 }}>
            <FormTextFieldV2
              label="Name of model"
              control={control}
              fieldName="modelName"
              placeholder="legal-docs-query-finetuned"
              autoFocus
            />
          </FormControl>
          <FormSelectField
            label="Model type"
            control={control}
            fieldName="modelTypeId"
            options={ModelTypeOptions}
            placeholder="Generative LLM"
            MenuProps={{
              PaperProps: {
                sx: {
                  maxHeight: 224,
                },
              },
            }}
          />
          <Box sx={{ display: "flex", justifyContent: "end", gap: "12px" }}>
            <LoadingButton
              loading={isPending}
              variant="contained"
              color="primary"
              type="submit"
            >
              Create Model
            </LoadingButton>
            {/* <Button onClick={() => closeAndClear()}>Cancel</Button> */}
          </Box>
        </Box>
      </form>
    </ModalComponent>
  );
};

CreateModelModal.propTypes = {
  open: PropType.bool,
  onClose: PropType.func,
};

export default CreateModelModal;
