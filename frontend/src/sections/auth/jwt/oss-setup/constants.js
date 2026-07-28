export const OSS_SETUP_SEEN_KEY = "fai_oss_setup_seen";

export const OSS_SETUP_TABS = {
  CREATE: "create",
  RESET: "reset",
};

const INSTALL_GUIDE_BASE =
  "https://github.com/future-agi/future-agi/blob/main/INSTALLATION.md";

export const OSS_DOC_URL = {
  [OSS_SETUP_TABS.CREATE]: `${INSTALL_GUIDE_BASE}#create-your-first-account`,
  [OSS_SETUP_TABS.RESET]: `${INSTALL_GUIDE_BASE}#password-reset-without-email`,
};

export const CREATE_USER_CMD = `docker compose exec backend python manage.py create_user`;

export const CREATE_USER_CMD_NONINTERACTIVE = `docker compose exec backend python manage.py create_user \\
  --email you@example.com \\
  --name "Your Name" \\
  --password yourpassword`;

export const RESET_SHELL_CMD = `docker compose exec backend python manage.py shell`;

export const RESET_PYTHON_SNIPPET = `from django.utils import timezone
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.conf import settings
from accounts.models import User
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.authentication import generate_encrypted_message

user = User.objects.get(email="you@example.com")
access_token = AuthToken.objects.create(
    user=user,
    auth_type=AuthTokenType.ACCESS.value,
    is_active=True,
    last_used_at=timezone.now(),
)
token = generate_encrypted_message({"user_id": str(user.id), "id": str(access_token.id)})
uidb64 = urlsafe_base64_encode(force_bytes(user.id))
print(f"{settings.APP_URL or 'http://localhost:3000'}/auth/jwt/verify/{uidb64}/{token}")`;
