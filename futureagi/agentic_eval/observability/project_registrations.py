import os

from dotenv import load_dotenv
from fi_instrumentation import FITracer, Transport, register
from fi_instrumentation.fi_types import ProjectType
from opentelemetry.sdk.trace import TracerProvider
from traceai_litellm import LiteLLMInstrumentor

from accounts.models.user import User
import structlog

logger = structlog.get_logger(__name__)
from tracer.models.trace import Trace


def register_project(project_name: str, session_name: str | None = None):
    """
    Registers a project with Future AGI Observe and returns a trace_provider.

    Args:
        project_name (str): The name of the project to register.
        session_name (str, optional): The name of the session. Defaults to None.

    Returns:
        A trace_provider object if registration is successful, otherwise None.
    """
    load_dotenv()
    fi_api_key = os.getenv("FI_API_KEY")
    fi_secret_key = os.getenv("FI_SECRET_KEY")

    if not fi_api_key or not fi_secret_key:
        logger.warning(
            "FI_API_KEY and FI_SECRET_KEY environment variables are not set. Skipping FutureAGI Observe registration."
        )
        return None

    try:
        trace_provider = register(
            project_type=ProjectType.OBSERVE,
            project_name=project_name,
            transport=Transport.HTTP,
            verbose=True,
        )
        logger.info(
            f"Successfully registered project '{project_name}' with Future AGI Observe."
        )
        return trace_provider
    except Exception as e:
        logger.error(f"Failed to register project '{project_name}': {e}")
        return None


def get_user_attributes(user_id: str):
    if not user_id:
        return None
    try:
        user = User.objects.get(id=user_id)

        return {
            "user.id": user.id,
            "user.email": user.email,
            "user.name": user.name,
            "user.organization_role": user.organization_role,
            "user.organization": user.organization,
        }
    except User.DoesNotExist:
        logger.error(f"User with id {user_id} does not exist.")
        return None


def set_user_attributes(span: FITracer, user_attributes: dict):
    span.set_attribute("user.id", str(user_attributes["user.id"]))
    span.set_attribute("user.email", str(user_attributes["user.email"]))
    span.set_attribute("user.name", str(user_attributes["user.name"]))
    span.set_attribute(
        "user.organization_role", str(user_attributes["user.organization_role"])
    )


def get_user_id_from_trace(trace_id: str):
    try:
        # Use select_related to pre-fetch related project and user in a single query
        trace = Trace.objects.select_related("project__user").get(id=trace_id)

        # The user is on the project, not the trace directly
        if trace.project and trace.project.user:
            return str(trace.project.user.id)
        else:
            logger.warning(
                f"User not found for project {trace.project.id} on trace {trace_id}"
            )
            return None

    except Trace.DoesNotExist:
        logger.error(f"Trace with id {trace_id} does not exist.")
        return None
    except Exception as e:
        logger.error(f"An error occurred fetching user for trace {trace_id}: {e}")
        return None


def toggle_instrumentation(
    framework: str, toggle: bool, tracer_provider: TracerProvider
):
    """
    Toggles instrumentation for a given framework.
    """
    available_instrumentors = {
        "litellm": LiteLLMInstrumentor,
    }
    instrumentor_class = available_instrumentors.get(framework)
    if not instrumentor_class:
        logger.error(f"No instrumentor found for framework: {framework}")
        return

    instrumentor = instrumentor_class()
    if toggle:
        instrumentor.instrument(tracer_provider=tracer_provider)
    else:
        # uninstrument typically does not require a trace_provider
        instrumentor.uninstrument()
