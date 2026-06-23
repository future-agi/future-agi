import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

export const usePdfPreviewStore = create((set, get) => ({
  openPdfPreviewDrawer: null,

  setOpenPreviewPdfDrawer: ({ url, type, name }) => {
    set({
      openPdfPreviewDrawer: {
        isPublic: true,
        url,
        name: name || url.split("/").pop(),
        type: type || getFileType(url),
      },
    });
  },

  closePreview: () => {
    const prev = get().openPdfPreviewDrawer;
    if (prev?.url?.startsWith("blob:")) {
      URL.revokeObjectURL(prev.url);
    }
    set({ openPdfPreviewDrawer: null });
  },
}));

const getFileType = (name = "") => {
  const ext = name.split(".").pop()?.toLowerCase();
  if (["pdf", "txt", "doc", "docx", "png", "jpg", "jpeg", "webp", "gif", "svg", "bmp"].includes(ext)) return ext;
  return "unknown";
};

export const usePdfPreviewStoreShallow = (selector) =>
  usePdfPreviewStore(useShallow(selector));
