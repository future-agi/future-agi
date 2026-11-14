import { create } from "src/utils/zustand";

export const useTraceDrawerStore = create((set) => ({
  viewType: "markdown",
  setViewType: (value) => set({ viewType: value }),
}));

export const resetTraceDrawerStore = () => {
  useTraceDrawerStore.setState({
    viewType: "markdown",
  });
};
