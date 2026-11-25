import { create } from "src/utils/zustand";

export const useCreateNewAgentStore = create((set) => ({
  currentStep: 0,
  validatedSteps: [false, false, false],

  setCurrentStep: (step) => set({ currentStep: step }),
  setStepValidated: (stepIndex, value) =>
    set((state) => {
      const validatedSteps = [...state.validatedSteps];
      validatedSteps[stepIndex] = value;
      return { validatedSteps };
    }),

  nextStep: () =>
    set((state) => ({
      currentStep: Math.min(state.currentStep + 1, 2),
    })),

  prevStep: () =>
    set((state) => ({
      currentStep: Math.max(state.currentStep - 1, 0),
    })),

  reset: () => set({ currentStep: 0, validatedSteps: [false, false, false] }),
}));
