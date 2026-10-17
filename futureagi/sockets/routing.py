import importlib

from django.urls import re_path

from sockets.consumer import DataConsumer
from sockets.prompt_stream_consumer import PromptStreamConsumer
from sockets.simulation_consumer import SimulationUpdateConsumer
from tfc.ee_loader import ee_feature_enabled
from tracer.socket import GraphDataConsumer

websocket_urlpatterns = [
    re_path(r"ws/graphs/", GraphDataConsumer.as_asgi()),
    re_path(r"ws/connect/", DataConsumer.as_asgi()),
    re_path(r"ws/prompt-stream/", PromptStreamConsumer.as_asgi()),
    re_path(r"ws/simulation-updates/", SimulationUpdateConsumer.as_asgi()),
]

# Falcon AI WebSocket routes — wire the real consumer only when the EE code
# is present AND the deployment isn't OSS (EE_LICENSE_KEY or CLOUD_DEPLOYMENT
# set). Otherwise fall back to a stub that closes the socket with an
# upgrade-required error frame.
if ee_feature_enabled("ee.falcon_ai"):
    websocket_urlpatterns += importlib.import_module(
        "ee.falcon_ai.routing"
    ).websocket_urlpatterns
else:
    from sockets.ee_stub_consumer import EEUpgradeConsumer

    websocket_urlpatterns += [
        re_path(r"ws/falcon-ai/$", EEUpgradeConsumer.as_asgi()),
    ]
