import { getAnnotationForSpanId } from "./prototype-observe-handlers/getAnnotaionForSpanId";
import { uploadFile } from "./model-hub-handlers/uploadFile";
import { createVersion } from "./agent-playground-handlers/createVersion";

export const handlers = [getAnnotationForSpanId, uploadFile, createVersion];
