import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  hideSampleProject,
  onboardingHomeQueryKeys,
  openSampleProject,
} from "../api/onboarding-home-api";

export const useSampleProject = () => {
  const queryClient = useQueryClient();
  const invalidateActivationState = () =>
    queryClient.invalidateQueries({
      queryKey: onboardingHomeQueryKeys.all,
    });

  const openMutation = useMutation({
    mutationFn: openSampleProject,
    onSuccess: invalidateActivationState,
  });

  const hideMutation = useMutation({
    mutationFn: hideSampleProject,
    onSuccess: invalidateActivationState,
  });

  return {
    openSampleProject: openMutation,
    hideSampleProject: hideMutation,
  };
};
