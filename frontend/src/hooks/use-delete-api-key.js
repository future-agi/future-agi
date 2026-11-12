import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { RESPONSE_CODES } from "src/utils/constants";
import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";

export const DELETE_MODAL_TYPE = {
  CUSTOM: "custom",
  NORMAL: "normal",
};

const DELETE_FAILED_MESSAGE = "We couldn't delete this API key. Try again.";

// Mirrors the app-level handler (handleError in src/utils/queryErrorHandler.js,
// the MutationCache onError in src/app.jsx): it shows a message only for a
// gateway authentication error or an error that carries the API's `result`,
// and never for a 429. A delete that fails any other way, such as a request
// that got no response, would otherwise end with no message at all.
const appHandlerShowsMessage = (error) =>
  error?.statusCode != RESPONSE_CODES.LIMIT_REACHED &&
  Boolean(getGatewayAuthenticationErrorMessage(error) || error?.result);

const showFallbackWhenAppIsSilent = (error) => {
  if (appHandlerShowsMessage(error)) return;
  enqueueSnackbar(DELETE_FAILED_MESSAGE, { variant: "error" });
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
      setOpenDeleteModal(null);
    },
    onError: showFallbackWhenAppIsSilent,
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
      onError: showFallbackWhenAppIsSilent,
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
