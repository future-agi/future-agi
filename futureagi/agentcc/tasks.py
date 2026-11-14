import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(name="agentcc.check_gateway_health")
def check_gateway_health():
    from agentcc.services.gateway_client import GatewayClientError, get_gateway_client

    try:
        client = get_gateway_client()
        health = client.health_check()
        provider_health = client.provider_health()
        logger.info(
            "gateway_health_check_ok",
            status="connected",
            providers=len(provider_health.get("providers", [])),
        )
        return {"status": "connected", "health": health}
    except GatewayClientError as e:
        logger.warning("gateway_health_check_failed", error=str(e))
        return {"status": "unreachable", "error": str(e)}
    except Exception as e:
        logger.exception("gateway_health_check_unexpected_error", error=str(e))
        return {"status": "error", "error": str(e)}
