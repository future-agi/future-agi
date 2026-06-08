import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  onboardingHomeQueryKeys,
  saveOnboardingGoal,
} from "../api/onboarding-home-api";

export const useSaveOnboardingGoal = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: saveOnboardingGoal,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: onboardingHomeQueryKeys.all,
      });
    },
  });
};
