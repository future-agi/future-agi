import RepoForm from "./RepoForm";
import UploadForm from "./UploadForm";
import HostedForm from "./HostedForm";
import LiveKitForm from "./LiveKitForm";
import PhoneForm from "./PhoneForm";
import { UI_ONLY_FORMS } from "./uiOnly";
import ScratchForm from "../starts/ScratchForm";
import FromCodeForm from "../starts/FromCodeForm";
import McpForm from "../starts/McpForm";
import SandboxForm from "../starts/SandboxForm";
import WorldForm from "../starts/WorldForm";

// Each way in's setup form, by its `setup.panel`. The Build environment
// drawer and the New agent page open the same ones.
export const FORMS_BY_PANEL = {
  repo: RepoForm,
  upload: UploadForm,
  hosted: HostedForm,
  livekit: LiveKitForm,
  phone: PhoneForm,
  scratch: ScratchForm,
  fromCode: FromCodeForm,
  mcpWorld: McpForm,
  sandbox: SandboxForm,
  world: WorldForm,
  ...UI_ONLY_FORMS,
};
