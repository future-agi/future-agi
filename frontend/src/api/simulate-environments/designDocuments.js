import { uploadHarnessDesignDocument } from "src/api/harness/releases";

export const DESIGN_DOCUMENT_MAX_BYTES = 25 * 1024 * 1024;

const upload = async (file, options) => {
  if (file.size > DESIGN_DOCUMENT_MAX_BYTES) {
    throw Object.assign(new Error("The file is larger than 25 MB."), {
      code: "too_large",
    });
  }
  try {
    return await uploadHarnessDesignDocument(file, options);
  } catch (error) {
    const code = error?.response?.data?.code || error?.code || error?.error;
    const normalized = {
      document_too_large: "too_large",
      document_unsupported: "unsupported",
    };
    throw Object.assign(new Error(error?.message), {
      code: normalized[code] || "upload_failed",
    });
  }
};

export function useUploadDesignDocument() {
  return upload;
}
