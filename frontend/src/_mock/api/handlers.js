import { getAnnotationForSpanId } from "./prototype-observe-handlers/getAnnotaionForSpanId";
import { uploadFile } from "./model-hub-handlers/uploadFile";
import { createVersion } from "./agent-playground-handlers/createVersion";
import { setupChecks } from "./oss-setup-handlers/setupChecks";
import {
  createInviteLinks,
  cancelInviteLink,
} from "./invite-handlers/inviteLinks";
import { deploymentInfo } from "./deployment-handlers/deploymentInfo";

export const handlers = [
  getAnnotationForSpanId,
  uploadFile,
  createVersion,
  setupChecks,
  createInviteLinks,
  cancelInviteLink,
  deploymentInfo,
];
