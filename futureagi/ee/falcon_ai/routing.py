from django.urls import re_path

from ee.falcon_ai.consumers import FalconAIConsumer

websocket_urlpatterns = [
    re_path(r"ws/falcon-ai/$", FalconAIConsumer.as_asgi()),
]
