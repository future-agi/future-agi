from django.urls import path, re_path
from rest_framework.routers import DefaultRouter
from rest_framework.urlpatterns import format_suffix_patterns

from saml2_auth.views import (
    ACSView,
    Auth0CallbackView,
    Auth0LoginView,
    CompleteView,
    GithubCallbackView,
    IDPLoginView,
    MicrosoftCallbackView,
)

from . import views

router = DefaultRouter()
router.register(r"idp-uploads", views.IDPUploadViews)
urlpatterns = [
    re_path(r"^acs/$", ACSView.as_view(), name="acs"),
    re_path(r"^complete/$", CompleteView.as_view(), name="complete"),
    re_path(r"^idp-login/$", IDPLoginView.as_view(), name="idp_signin"),
    # re_path(r'^denied/$', denied, name="denied"),
    # re_path(r'^logout/$', LogoutView.as_view(), name='logout'),
    # path('idp-upload/', IDPUploadView.as_view(), name='idp_upload'),
    # path('home/', HomeView.as_view(), name='home'),
    path("login/", Auth0LoginView.as_view(), name="login"),
    # path("logout/", LogoutView.as_view(), name="logout"),
    path("auth/callback/", Auth0CallbackView.as_view(), name="auth0_callback"),
    path("github/callback/", GithubCallbackView.as_view(), name="github-callback"),
    path(
        "microsoft/callback/",
        MicrosoftCallbackView.as_view(),
        name="microsoft-callback",
    ),
    # path("get_available_idps/", AvailableIDPs.as_view(), name="available-idps"),
]
urlpatterns = format_suffix_patterns(urlpatterns) + router.urls
