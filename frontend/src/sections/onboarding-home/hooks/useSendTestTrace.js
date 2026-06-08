import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  onboardingHomeQueryKeys,
  sendTestTrace,
} from "../api/onboarding-home-api";

export const useSendTestTrace = () => {
  const queryClient = useQueryClient();
  const invalidateActivationState = () =>
    queryClient.invalidateQueries({
      queryKey: onboardingHomeQueryKeys.all,
    });

  const sendMutation = useMutation({
    mutationFn: sendTestTrace,
    onSuccess: invalidateActivationState,
  });

  return {
    sendTestTrace: sendMutation,
  };
};
