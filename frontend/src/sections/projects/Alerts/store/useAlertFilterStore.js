import { useMemo } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { alertTypes } from "../common";
import { CLEARED_FILTER_STATE, createFilterSlice } from "./alertFilterState";

// Fields the alerts list can be filtered on, in FilterPanel's `filterFields`
// shape. `choices` hold the values the API expects; `choiceLabels` map them to
// what the user sees. Everything is single-select except Project, matching what
// the monitor-list endpoint accepts.
const buildAlertTypeField = () => {
  const options = alertTypes.flatMap((group) => group.options);
  return {
    value: "metric_type",
    label: "Alert Type",
    type: "enum",
    operators: ["is"],
    single: true,
    choices: options.map((o) => o.value),
    choiceLabels: Object.fromEntries(options.map((o) => [o.value, o.label])),
  };
};

const STATUS_FIELD = {
  value: "status",
  label: "Status",
  type: "enum",
  operators: ["is"],
  single: true,
  choices: ["triggered", "healthy"],
  choiceLabels: { triggered: "Triggered", healthy: "Healthy" },
};

const buildProjectField = (projectOptions) => ({
  value: "project_id",
  label: "Project",
  type: "enum",
  operators: ["is"],
  choices: projectOptions.map((p) => p.value),
  choiceLabels: Object.fromEntries(
    projectOptions.map((p) => [p.value, p.label]),
  ),
});

export const useAlertFilterStore = create((set) => ({
  ...createFilterSlice(set),
  projectOptions: [],

  setProjectOptions: (options) =>
    set({
      projectOptions:
        options?.map(({ id, name }) => ({ label: name, value: id })) || [],
    }),
}));

export const useAlertFilterShallow = () =>
  useAlertFilterStore(
    useShallow((state) => ({
      activeFilters: state.activeFilters,
      hasValidFilters: state.hasValidFilters,
      projectOptions: state.projectOptions,

      setProjectOptions: state.setProjectOptions,
      setActiveFilters: state.setActiveFilters,
    })),
  );

// Built with useMemo rather than a store getter: FilterPanel compares
// `filterFields` by identity, and a selector that rebuilt the array on every
// read would re-render on each store change.
export const useAlertFilterFields = (mainPage) => {
  const projectOptions = useAlertFilterStore((state) => state.projectOptions);
  return useMemo(
    () => [
      buildAlertTypeField(),
      STATUS_FIELD,
      ...(mainPage ? [buildProjectField(projectOptions)] : []),
    ],
    [mainPage, projectOptions],
  );
};

// Also clears projectOptions, unlike the sheet store's reset.
export const resetAlertFilterStoreState = () => {
  useAlertFilterStore.setState({ ...CLEARED_FILTER_STATE, projectOptions: [] });
};
