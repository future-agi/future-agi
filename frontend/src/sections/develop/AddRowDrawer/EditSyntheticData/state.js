import { create } from "src/utils/zustand";

export const useEditSyntheticDataStore = create((set) => ({
  openEditDrawer: false,
  setOpenEditDrawer: (openEditDrawer) => set({ openEditDrawer }),
  openSummaryDrawer: false,
  setOpenSummaryDrawer: (openSummaryDrawer) => set({ openSummaryDrawer }),
  openConfirmEdit: false,
  setOpenConfirmEdit: (openConfirmEdit) => set({ openConfirmEdit }),
  openDatasetCreateOptions: false,
  setOpenDatasetCreateOptions: (openDatasetCreateOptions) =>
    set({ openDatasetCreateOptions }),
  failedToGenerateData: false,
  setFailedToGenerateData: (failedToGenerateData) =>
    set({ failedToGenerateData }),
}));

export const resetEditSyntheticStates = () => {
  useEditSyntheticDataStore.setState({
    openEditDrawer: false,
    openSummaryDrawer: false,
    openConfirmEdit: false,
    openDatasetCreateOptions: false,
    failedToGenerateData: false,
  });
};
