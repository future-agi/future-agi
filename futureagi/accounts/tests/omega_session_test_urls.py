from django.urls import path

from accounts.views.omega_observe_target import OmegaObserveTargetView
from accounts.views.omega_session import OmegaSessionExchangeView

urlpatterns = [
    path("accounts/omega/observe-target/", OmegaObserveTargetView.as_view()),
    path(
        "accounts/omega/session-exchange/",
        OmegaSessionExchangeView.as_view(),
        name="omega-session-exchange",
    ),
]
