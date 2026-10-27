import ClosedForm from "./ClosedForm";
import DirectApiForm from "./DirectApiForm";
import EndpointForm from "./EndpointForm";
import FrameworkForm from "./FrameworkForm";
import GitHostForm from "./GitHostForm";
import HostedSoonForm from "./HostedSoonForm";

// The UI-only setup forms, by `setup.panel`: Vel's design for each way in
// whose API isn't there yet. Each runs on sample data and builds nothing.
export const UI_ONLY_FORMS = {
  framework: FrameworkForm,
  gitHost: GitHostForm,
  directApi: DirectApiForm,
  hostedSoon: HostedSoonForm,
  closed: ClosedForm,
  endpoint: EndpointForm,
};
