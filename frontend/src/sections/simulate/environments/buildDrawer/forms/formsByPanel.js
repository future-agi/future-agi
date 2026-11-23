import { GitHubCodeForm, UploadCodeForm } from "./ConnectCodeForm";
import HostedPlatformForm from "./HostedPlatformForm";
import { UI_ONLY_FORMS } from "./uiOnly";
import ScratchForm from "../starts/ScratchForm";
import FromCodeForm from "../starts/FromCodeForm";
import McpForm from "../starts/McpForm";
import SandboxForm from "../starts/SandboxForm";
import ReadyEnvironmentPanel from "../starts/ReadyEnvironmentPanel";

// Each way in's setup form, by its `setup.panel`.
export const FORMS_BY_PANEL = {
  repo: GitHubCodeForm,
  upload: UploadCodeForm,
  hosted: HostedPlatformForm,
  livekit: HostedPlatformForm,
  phone: HostedPlatformForm,
  scratch: ScratchForm,
  fromCode: FromCodeForm,
  mcpWorld: McpForm,
  sandbox: SandboxForm,
  ready: ReadyEnvironmentPanel,
  ...UI_ONLY_FORMS,
};
