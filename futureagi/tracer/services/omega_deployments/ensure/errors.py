"""The refusals of an Observe link ensure, each tied to its message in error_codes.py."""

from tfc.utils.error_codes import get_error_message


class ObserveLinkError(Exception):
    """A refusal the caller can act on. ``code`` names the message in ``err_dict``."""

    code = "OMEGA_DEPLOYMENT_OBSERVE_FAILED"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class DeploymentNotInWorkspace(ObserveLinkError):
    """The deployment id is linked in another workspace (404, never the other workspace's data)."""

    code = "OMEGA_DEPLOYMENT_OBSERVE_NOT_FOUND"

    def __init__(self):
        super().__init__(get_error_message(self.code))


class ProjectNameTaken(ObserveLinkError):
    """``deploy/<name>`` exists in the workspace as a project that is not an Observe project (409)."""

    code = "OMEGA_DEPLOYMENT_OBSERVE_NAME_TAKEN"

    def __init__(self, project_name: str):
        super().__init__(get_error_message(self.code).format(project_name))
        self.project_name = project_name


class AgentNotInWorkspace(ObserveLinkError):
    """The Console agent id is linked in another workspace (404, never the other workspace's data)."""

    code = "OMEGA_AGENT_OBSERVE_NOT_FOUND"

    def __init__(self):
        super().__init__(get_error_message(self.code))


class AgentProjectNameTaken(ObserveLinkError):
    """The agent's project name is taken in the workspace (409): by a project that is not an
    Observe project, or by an Observe project another live agent link holds. Never adopted."""

    code = "OMEGA_AGENT_OBSERVE_NAME_TAKEN"

    def __init__(self, project_name: str):
        super().__init__(get_error_message(self.code).format(project_name))
        self.project_name = project_name
