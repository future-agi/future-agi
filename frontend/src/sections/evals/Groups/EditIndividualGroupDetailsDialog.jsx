import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";

const EditIndividualGroupDetailsDialog = ({
  open,
  onClose,
  name: prevName,
  description: prevDescription,
  selectDrawerType,
  handleRefresh,
}) => {
  const {
    control,
    handleSubmit,
    formState: { isValid },
  } = useForm({
    mode: "onChange",
    defaultValues: {
      name: prevName,
      description: prevDescription,
    },
  });

  const { mutate: updateGroupMutation, isPending } = useMutation({
    mutationFn: async (data) => {
      return axios.patch(
        `${endpoints.develop.eval.groupEvals}${selectDrawerType?.id}/`,
        {
          name: data?.name,
          description: data?.description,
        },
      );
    },

    onSuccess: () => {
      handleRefresh();
      onClose(); // close the dialog
    },
  });

  const onSubmit = (data) => {
    if (!data?.name) {
      return;
    }
    updateGroupMutation(data);
  };

  return (
    <ModalWrapper
      isValid={isValid}
      actionBtnTitle="Save Changes"
      modalWidth="560px"
      title="Edit Group Info"
      open={open}
      onClose={onClose}
      cancelBtnTitle="Cancel"
      isLoading={isPending}
      onSubmit={handleSubmit(onSubmit)}
      cancelBtnSx={{
        minWidth: "90px",
        borderColor: "text.disabled",
        color: "text.primary !important",
      }}
    >
      <FormTextFieldV2
        fullWidth
        label={"Group Name"}
        fieldName={"name"}
        required
        control={control}
        defaultValue={prevName}
        size="small"
        onKeyDown={(e) => {
          if (e.key === "Enter" && isValid) {
            e.preventDefault();
            handleSubmit(onSubmit)();
          }
        }}
      />

      <FormTextFieldV2
        control={control}
        fieldName="description"
        label="Description"
        defaultValue={prevDescription}
        multiline
        minRows={4}
        maxRows={10}
        inputProps={{
          style: {
            minHeight: "82px",
            overflowY: "auto",
            whiteSpace: "pre-wrap",
          },
        }}
        fullWidth
      />
    </ModalWrapper>
  );
};

export default EditIndividualGroupDetailsDialog;

EditIndividualGroupDetailsDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  name: PropTypes.string,
  description: PropTypes.string,
  selectDrawerType: PropTypes.object,
  handleRefresh: PropTypes.func,
};
