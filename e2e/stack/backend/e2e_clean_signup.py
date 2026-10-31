"""Clean actor provisioning. This module is never copied into product images."""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.utils import first_signup


@api_view(["POST"])
@permission_classes([AllowAny])
def clean_signup(request):
    # Explicit endpoint, not an email convention or public signup parameter.
    data = {
        key: request.data[key]
        for key in ("email", "full_name", "password")
        if key in request.data
    }
    if not data.get("password"):
        return Response({"error": "A password is required for test actors"}, status=400)
    first_signup(data, seed_demo_data=False)
    return Response({"status": "success"})
