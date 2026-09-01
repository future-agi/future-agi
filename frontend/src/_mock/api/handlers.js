import { getAnnotationForSpanId } from "./prototype-observe-handlers/getAnnotaionForSpanId";
import { uploadFile } from "./model-hub-handlers/uploadFile";
import { createVersion } from "./agent-playground-handlers/createVersion";
import { gcpMarketplaceSignup } from "./marketplace-handlers/gcpMarketplaceSignup";

export const handlers = [
  getAnnotationForSpanId,
  uploadFile,
  createVersion,
  gcpMarketplaceSignup,
];
