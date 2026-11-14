import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { decisionModelsQueryKey } from "src/api/evaluations/decision-models";

export const DELETE_MODAL_TYPE = {
  CUSTOM: "custom",
  NORMAL: "normal",
};

export const useDeleteApiKey = () => {
  const queryClient = useQueryClient();
  const [openDeleteModal, setOpenDeleteModal] = useState(null);

  const { mutate: deleteApiKey, isPending: isDeletingApiKey } = useMutation({
    mutationFn: (id) => axios.delete(endpoints.develop.apiKey.delete(id)),
    onSuccess: () => {
      enqueueSnackbar("API key has been deleted", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: ["model-list"],
      });
      queryClient.invalidateQueries({ queryKey: ["api-key-status"] });
      queryClient.invalidateQueries({ queryKey: decisionModelsQueryKey });
      setOpenDeleteModal(null);
    },
  });

  const { mutate: deleteCustomModel, isPending: isDeletingCustomModel } =
    useMutation({
      mutationFn: (id) =>
        axios.delete(endpoints.settings.customModal.deleteModel, {
          data: { ids: [id] },
        }),
      onSuccess: () => {
        enqueueSnackbar("API key has been deleted", { variant: "success" });
        queryClient.invalidateQueries({ queryKey: ["customModals"] });
        setOpenDeleteModal(null);
      },
    });

  const handleDeleteApiKey = () => {
    if (!openDeleteModal) return;

    if (openDeleteModal.type === DELETE_MODAL_TYPE.CUSTOM) {
      deleteCustomModel(openDeleteModal.id);
    } else {
      deleteApiKey(openDeleteModal.id);
    }
  };

  return {
    openDeleteModal,
    setOpenDeleteModal,
    handleDeleteApiKey,
    isDeletingApiKey,
    isDeletingCustomModel,
    isDeleting: isDeletingApiKey || isDeletingCustomModel,
  };
};
