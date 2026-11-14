import { create } from "src/utils/zustand";
import { useShallow } from "zustand/react/shallow";

export const useSelectedScenariosStore = create((set, get) => ({
  selectedScenarios: [],
  setSelectedScenarios: (selectedScenarios) =>
    set({
      selectedScenarios:
        typeof selectedScenarios === "function"
          ? selectedScenarios(get().selectedScenarios)
          : selectedScenarios,
    }),
}));

// export const useSelectedScenariosStore = create((set, get) => ({
//   toggledScenarios: [],
//   selectAll: false,
//   setToggledScenarios: (toggledScenarios) => set({ toggledScenarios }),
//   setSelectAll: (selectAll) => set({ selectAll }),

//   // Toggle individual scenario when selectAll is true
//   toggleScenario: (scenarioId, totalCount) =>
//     set((state) => {
//       if (state.selectAll) {
//         const isCurrentlyToggled = state.toggledScenarios.includes(scenarioId);
//         let finalToggledScenarios = state.toggledScenarios;

//         if (isCurrentlyToggled) {
//           // If scenario is in toggledScenarios (unchecked), remove it (check it)
//           finalToggledScenarios = state.toggledScenarios.filter(
//             (id) => id !== scenarioId,
//           );
//         } else {
//           // If scenario is not in toggledScenarios (checked), add it (uncheck it)
//           finalToggledScenarios = [...state.toggledScenarios, scenarioId];
//         }

//         if (finalToggledScenarios.length === totalCount) {
//           return { selectAll: false, toggledScenarios: [] };
//         }

//         return {
//           toggledScenarios: finalToggledScenarios,
//           selectAll: true,
//         };
//       } else {
//         const isCurrentlyToggled = state.toggledScenarios.includes(scenarioId);
//         let finalToggledScenarios = state.toggledScenarios;

//         if (isCurrentlyToggled) {
//           finalToggledScenarios = state.toggledScenarios.filter(
//             (id) => id !== scenarioId,
//           );
//         } else {
//           finalToggledScenarios = [...state.toggledScenarios, scenarioId];
//         }

//         if (finalToggledScenarios.length === totalCount) {
//           return { selectAll: true, toggledScenarios: [] };
//         }

//         return {
//           toggledScenarios: finalToggledScenarios,
//         };
//       }
//     }),

//   // Helper function to check if a scenario is selected
//   isScenarioSelected: (scenarioId) => {
//     const state = get();
//     if (state.selectAll) {
//       // When selectAll is true, scenario is selected if it's NOT in toggledScenarios
//       return !state.toggledScenarios.includes(scenarioId);
//     } else {
//       return state.toggledScenarios.includes(scenarioId);
//     }
//   },
// }));

export const useTestRunsSearchStore = create((set) => ({
  search: "",
  setSearch: (search) => set({ search }),
}));

export const useSelectedSimulatorAgentsStore = create((set) => ({
  selectedSimulatorAgent: null,
  setSelectedSimulatorAgent: (selectedSimulatorAgent) =>
    set({ selectedSimulatorAgent }),
}));

export const useSelectedAgentDefinitionStore = create((set) => ({
  selectedAgentDefinition: null,
  setSelectedAgentDefinition: (selectedAgentDefinition) =>
    set({ selectedAgentDefinition }),
  selectedAgentDefinitionVersion: null,
  setSelectedAgentDefinitionVersion: (selectedAgentDefinitionVersion) =>
    set({ selectedAgentDefinitionVersion }),
}));

export const useSelectedScenariosStoreShallow = (fun) =>
  useSelectedScenariosStore(useShallow(fun));

export const useTestRunsSearchStoreShallow = (fun) =>
  useTestRunsSearchStore(useShallow(fun));

export const resetState = () => {
  useSelectedScenariosStore.setState({
    selectedScenarios: [],
  });
  useTestRunsSearchStore.setState({
    search: "",
  });
};
