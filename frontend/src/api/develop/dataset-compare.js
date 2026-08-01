import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const useDeleteCompare = () => {
  return useMutation({
    mutationFn: async (compareId) => {
      const response = await axios.delete(
        endpoints.dataset.deleteCompareDataset(compareId),
      );
      return response.data;
    },
  });
};
