import { Typography } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React from "react";
import axios, { endpoints } from "../../../utils/axios";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { useEvaluationContext } from "src/sections/common/EvaluationDrawer/context/EvaluationContext";
import { enqueueSnackbar } from "notistack";
import { useNavigate } from "react-router";

const DeleteEvalFromGroupDialog = ({
  open,
  id,
  onClose,
  handleRefresh,
  groupId,
  rowCount = 0,
}) => {
  const { selectedGroup, setSelectedGroup } = useEvaluationContext();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const { mutate: deleteGroup, isPending: isDeletingGroup } = useMutation({
    mutationFn: async () => {
      return axios.delete(`${endpoints.develop.eval.groupEvals}${groupId}/`);
    },
    onSuccess: () => {
      enqueueSnackbar("Group has been deleted", {
        variant: "success",
      });
      queryClient.invalidateQueries({
        queryKey: ["eval-groups"],
      });
      if (selectedGroup) {
        setSelectedGroup(null);
      } else {
        navigate("/dashboard/evaluations/groups");
      }
    },
  });

  const { mutate: updateEvalList, isPending: isUpdatingGroupEvalList } =
    useMutation({
      mutationFn: async (payload) => {
        return axios.post(endpoints.develop.eval.editGroupEvalList, payload);
      },
      onSuccess: () => {
        handleRefresh();
        onClose();
      },
    });

  const handleDelete = () => {
    if (!groupId && !id) {
      return;
    }
    const payload = {
      deleted_template_ids: [id],
      eval_group_id: groupId || selectedGroup,
    };
    if (rowCount === 1) {
      deleteGroup();
    } else {
      updateEvalList(payload);
    }
  };
  return (
    <ModalWrapper
      title="Delete Evaluation"
      open={open}
      onClose={onClose}
      onSubmit={handleDelete}
      modalWidth="560px"
      isLoading={isUpdatingGroupEvalList || isDeletingGroup}
      isValid={true}
      actionBtnTitle="Delete Evaluation"
      cancelBtnTitle="Cancel"
      actionBtnSx={{
        backgroundColor: "status.danger.solid",
        ":hover": {
          backgroundColor: "status.danger.solid",
        },
      }}
      cancelBtnSx={{
        minWidth: "90px",
        borderColor: "text.disabled",
        color: "text.primary !important",
      }}
      actionBtnProps={{
        color: "error",
      }}
    >
      <Typography
        fontSize="13px"
        color="text.primary"
        fontWeight="fontWeightRegular"
        sx={{ lineHeight: 1.5 }}
      >
        Are you sure you want to delete evaluations from this group?
        <br />
        <strong>Note:</strong> The evaluation will be deleted only from this
        group. The evaluation applied will still be visible.
      </Typography>

      {/* No children needed here unless you want to add more UI */}
    </ModalWrapper>
  );
};

export default DeleteEvalFromGroupDialog;

DeleteEvalFromGroupDialog.propTypes = {
  open: PropTypes.bool,
  id: PropTypes.string,
  onClose: PropTypes.func,
  handleRefresh: PropTypes.func,
  groupId: PropTypes.string,
  rowCount: PropTypes.number,
};
