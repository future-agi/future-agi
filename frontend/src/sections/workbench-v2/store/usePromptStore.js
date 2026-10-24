import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

export const usePromptStore = create((set) => ({
  searchQuery: "",
  pageLimit: 10,
  totalPages: 1,
  totalCount: null,
  page: 1,
  newPromptModal: false,
  selectTemplateDrawerOpen: false,
  selectedTemplate: {},
  openSaveTemplate: false,
  isMoreOpen: false,
  // for import prompt template
  selectedPromptIndex: 0,

  onSearchQueryChange: (query) => {
    set({ searchQuery: query });
  },
  setPageLimit: (limit) => set({ pageLimit: limit }),
  setTotalPages: (totalPages) => set({ totalPages }),
  setTotalCount: (totalCount) => set({ totalCount }),
  setPage: (page) => set({ page }),
  setNewPromptModal: (newPromptModal) => set({ newPromptModal }),
  setSelectTemplateDrawerOpen: (selectTemplateDrawerOpen) =>
    set({ selectTemplateDrawerOpen }),
  setSelectedTemplate: (selectedTemplate) => set({ selectedTemplate }),
  setOpenSaveTemplate: (openSaveTemplate) => set({ openSaveTemplate }),
  setMoreOpen: (value) => set({ isMoreOpen: value }),
  setSelectedPromptIndex: (value) => set({ selectedPromptIndex: value }),
}));

export const usePromptStoreShallow = (fun) => usePromptStore(useShallow(fun));

export const resetPromptActionState = () => {
  usePromptStore.setState({
    page: 1,
    pageLimit: 10,
    searchQuery: "",
  });
};

export const resetPromptState = () => {
  usePromptStore.setState({
    searchQuery: "",
    pageLimit: 10,
    totalPages: 1,
    totalCount: null,
    page: 1,
    newPromptModal: false,
    selectTemplateDrawerOpen: false,
    selectedTemplate: {},
    openSaveTemplate: false,
    isMoreOpen: false,
    selectedPromptIndex: 0,
  });
};
