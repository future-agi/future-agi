import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { RESPONSE_CODES } from "src/utils/constants";

export const useDeleteCompare = () => {
  return useMutation({
    mutationFn: async (compareId) => {
      try {
        const response = await axios.delete(
          endpoints.dataset.deleteCompareDataset(compareId),
        );
        return response.data;
      } catch (error) {
        // The comparison is already gone (cleaned up or deleted earlier),
        // which is what deleting it asked for.
        if (error?.statusCode === RESPONSE_CODES.NOT_FOUND) return null;
        throw error;
      }
    },
  });
};
