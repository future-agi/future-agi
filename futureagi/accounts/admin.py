from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html, format_html_join

from accounts.models.auth_token import AuthToken
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership

from .models import (
    Organization,
    OrgApiKey,
    User,
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "display_name",
        "is_new",
        "created_at",
        "member_count",
        "workspace_count",
        "ws_enabled",
        "id",
    ]
    list_filter = ["is_new", "created_at", "ws_enabled", "id"]
    search_fields = ["name", "display_name", "id"]
    readonly_fields = ["id", "created_at"]
    ordering = ["-created_at"]

    def member_count(self, obj):
        return obj.members.count()

    member_count.short_description = "Members"

    def workspace_count(self, obj):
        return obj.workspaces.count()

    workspace_count.short_description = "Workspaces"


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = [
        "email",
        "name",
        "organization",
        "organization_role",
        "is_active",
        "is_staff",
        "created_at",
    ]
    list_filter = [
        "is_active",
        "is_staff",
        "organization_role",
        "created_at",
        "organization",
    ]
    search_fields = ["email", "name"]
    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Personal info",
            {"fields": ("name", "organization", "organization_role", "invited_by")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "created_at")}),
        ("Additional", {"fields": ("config",)}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "name",
                    "password1",
                    "password2",
                    "organization",
                    "organization_role",
                ),
            },
        ),
    )

    readonly_fields = ["id", "created_at", "last_login"]

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("organization", "invited_by")
        )


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "display_name",
        "organization",
        "is_active",
        "is_default",
        "created_by",
        "created_at",
    ]
    list_filter = ["is_active", "is_default", "created_at", "organization"]
    search_fields = ["name", "display_name", "description"]
    readonly_fields = ["id", "created_at"]
    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("name", "display_name", "description")}),
        ("Organization", {"fields": ("organization", "created_by")}),
        ("Status", {"fields": ("is_active", "is_default")}),
        ("Metadata", {"fields": ("id", "created_at")}),
    )

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("organization", "created_by")
        )


@admin.register(WorkspaceMembership)
class WorkspaceMembershipAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "workspace",
        "role",
        "is_active",
        "invited_by",
        "created_at",
    ]
    list_filter = ["role", "is_active", "created_at", "workspace__organization"]
    search_fields = ["user__email", "user__name", "workspace__name"]
    readonly_fields = ["id", "created_at"]
    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("user", "workspace", "role")}),
        ("Status", {"fields": ("is_active", "invited_by")}),
        ("Metadata", {"fields": ("id", "created_at")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user", "workspace", "invited_by")
        )


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "organization",
        "role",
        "is_active",
        "invited_by",
        "joined_at",
        "created_at",
    ]
    list_filter = ["role", "is_active", "joined_at", "created_at"]
    search_fields = ["user__email", "user__name", "organization__name"]
    readonly_fields = ["id", "joined_at", "created_at"]
    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("user", "organization", "role")}),
        ("Status", {"fields": ("is_active", "invited_by")}),
        ("Timestamps", {"fields": ("joined_at", "created_at")}),
        ("Metadata", {"fields": ("id",)}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user", "organization", "invited_by")
        )


@admin.register(OrgApiKey)
class OrgApiKeyAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "organization",
        "type",
        "enabled",
        "expires_at",
        "user",
        "workspace",
        "created_at",
    ]
    list_filter = ["type", "enabled", "created_at", "organization"]
    search_fields = ["name", "organization__name", "user__email"]
    readonly_fields = ["id", "api_key", "secret_key", "created_at"]
    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("name", "organization", "type")}),
        ("Access Control", {"fields": ("user", "workspace")}),
        ("Status", {"fields": ("enabled", "expires_at")}),
        ("Keys", {"fields": ("api_key", "secret_key")}),
        ("Metadata", {"fields": ("id", "created_at")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("organization", "user", "workspace")
        )


@admin.register(AuthToken)
class AuthTokenAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "user",
        "auth_type",
        "is_active",
        "last_used_at",
        "created_at",
    ]
    list_filter = ["auth_type", "is_active", "created_at", "last_used_at"]
    search_fields = ["user__email", "user__name"]
    readonly_fields = ["id", "created_at"]
    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("user", "auth_type")}),
        ("Status", {"fields": ("is_active", "last_used_at")}),
        ("Metadata", {"fields": ("id", "created_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user")


class BlockedKeysAdmin(admin.ModelAdmin):
    """Custom admin view to manage blocked IPs and users in Redis.

    This is not backed by a Django model — it reads/deletes Redis keys directly.
    """

    KEY_PREFIXES = [
        # IP blocking (authentication.py - login/signup/token endpoints)
        ("blocked_ip_", "Blocked IP (Login/Signup)"),
        ("ip_requests_", "IP Request Log (Login/Signup)"),
        # IP rate limiting (authentication.py - password reset)
        ("rate_limit_", "Rate Limited IP (Password Reset)"),
        ("rate_limit_requests_", "Rate Limit Request Log (Password Reset)"),
        # User account blocking (views/user.py - failed login)
        ("user_blocked_", "Blocked User Account"),
        ("login_attempts_", "Failed Login Attempts"),
        # Auth monitoring middleware (tfc/middleware/auth_monitoring.py)
        ("failed_auth_", "Failed Auth Attempts (Middleware)"),
        # 2FA challenge rate limiting (services/two_factor_challenge.py)
        ("2fa_challenge:", "2FA Challenge"),
        ("2fa_rate:", "2FA Rate Limit"),
        # Signup rate limiting (views/signup.py)
        ("activate_account_rate:", "Account Activation Rate Limit"),
    ]

    def _get_all_blocked_keys(self):
        """Scan Redis for all blocked/rate-limit keys."""
        from django_redis import get_redis_connection

        conn = get_redis_connection("default")
        results = []
        seen_keys = set()
        for prefix, label in self.KEY_PREFIXES:
            # Use SCAN instead of KEYS for safety on large Redis instances
            cursor = 0
            while True:
                cursor, keys = conn.scan(cursor, match=f"*:{prefix}*", count=200)
                for key in keys:
                    key_str = key.decode() if isinstance(key, bytes) else key
                    if key_str in seen_keys:
                        continue
                    seen_keys.add(key_str)
                    # Extract identifier by finding the prefix in the full key
                    # Keys look like "futureagi:1:blocked_ip_192.168.1.1"
                    # or "futureagi:1:2fa_challenge:abc123"
                    idx = key_str.find(prefix)
                    if idx >= 0:
                        identifier = key_str[idx + len(prefix) :]
                    else:
                        identifier = key_str
                    ttl = conn.ttl(key)
                    results.append(
                        {
                            "raw_key": key_str,
                            "type": label,
                            "identifier": identifier,
                            "ttl": ttl if ttl > 0 else "No expiry",
                        }
                    )
                if cursor == 0:
                    break
        return results

    def get_urls(self):
        urls = [
            path(
                "",
                self.admin_site.admin_view(self.blocked_keys_view),
                name="accounts_blockedkeyproxy_changelist",
            ),
            path(
                "delete/",
                self.admin_site.admin_view(self.delete_key_view),
                name="accounts_blockedkeyproxy_delete",
            ),
        ]
        return urls

    def blocked_keys_view(self, request):
        from django.middleware.csrf import get_token

        keys = self._get_all_blocked_keys()
        csrf_token = get_token(request)

        # Build table rows using format_html for XSS safety
        badge_class_map = {
            "Blocked IP (Login/Signup)": "badge-ip",
            "IP Request Log (Login/Signup)": "badge-rate",
            "Rate Limited IP (Password Reset)": "badge-rate",
            "Rate Limit Request Log (Password Reset)": "badge-rate",
            "Blocked User Account": "badge-user",
            "Failed Login Attempts": "badge-attempts",
            "Failed Auth Attempts (Middleware)": "badge-attempts",
            "2FA Challenge": "badge-user",
            "2FA Rate Limit": "badge-attempts",
            "Account Activation Rate Limit": "badge-rate",
        }
        row_fragments = []
        for entry in keys:
            badge_cls = badge_class_map.get(entry["type"], "badge-ip")
            ttl_display = (
                entry["ttl"] if entry["ttl"] == "No expiry" else f"{entry['ttl']}s"
            )
            row_fragments.append(
                format_html(
                    '<tr><td><span class="badge {}">{}</span></td><td><strong>{}</strong></td><td>{}</td>'
                    '<td><form method="post" action="delete/" style="display:inline" '
                    "onsubmit=\"return confirm('Delete this key?')\">"
                    '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
                    '<input type="hidden" name="key" value="{}">'
                    '<button type="submit" class="delete-btn" style="cursor:pointer;background:none;border:1px solid #e74c3c;'
                    'color:#e74c3c;font-weight:bold;padding:4px 12px;border-radius:4px;">Delete</button>'
                    "</form></td></tr>",
                    badge_cls,
                    entry["type"],
                    entry["identifier"],
                    ttl_display,
                    csrf_token,
                    entry["raw_key"],
                )
            )

        if not row_fragments:
            rows_html = format_html(
                '<tr><td colspan="4" style="text-align:center;padding:20px;">'
                "No blocked keys found.</td></tr>"
            )
        else:
            rows_html = format_html_join(
                "\n", "{}", ((frag,) for frag in row_fragments)
            )

        content = format_html(
            """
            <style>
                .blocked-keys-table {{ width:100%; border-collapse:collapse; border-radius:4px; overflow:hidden; }}
                .blocked-keys-table th, .blocked-keys-table td {{ padding:10px 16px; border-bottom:1px solid var(--border-color, #333); text-align:left; }}
                .blocked-keys-table th {{ background:var(--darkened-bg, #1a1a2e); color:var(--header-color, #a0a0b0); text-transform:uppercase; font-size:0.75rem; letter-spacing:0.05em; }}
                .blocked-keys-table td {{ color:var(--body-fg, #e0e0e0); }}
                .blocked-keys-table tr:hover td {{ background:var(--darkened-bg, #1a1a2e); }}
                .blocked-keys-table .delete-btn {{ color:#e74c3c; text-decoration:none; font-weight:bold; padding:4px 12px; border:1px solid #e74c3c; border-radius:4px; }}
                .blocked-keys-table .delete-btn:hover {{ background:#e74c3c; color:#fff; }}
                .blocked-keys-table .badge {{ display:inline-block; padding:3px 8px; border-radius:3px; font-size:0.8rem; font-weight:500; }}
                .blocked-keys-table .badge-ip {{ background:#e74c3c33; color:#e74c3c; }}
                .blocked-keys-table .badge-user {{ background:#f39c1233; color:#f39c12; }}
                .blocked-keys-table .badge-rate {{ background:#3498db33; color:#3498db; }}
                .blocked-keys-table .badge-attempts {{ background:#9b59b633; color:#9b59b6; }}
            </style>
            <table class="blocked-keys-table">
                <thead>
                    <tr>
                        <th>Type</th>
                        <th>Identifier (IP / Email)</th>
                        <th>TTL</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>{}</tbody>
            </table>
            """,
            rows_html,
        )

        context = {
            **self.admin_site.each_context(request),
            "title": "Blocked IPs & Users (Redis)",
            "content": content,
            "opts": type(
                "Opts", (), {"app_label": "accounts", "model_name": "blocked_keys"}
            )(),
        }
        return TemplateResponse(request, "admin/blocked_keys.html", context)

    def delete_key_view(self, request):
        from django.http import HttpResponseBadRequest, HttpResponseNotAllowed
        from django_redis import get_redis_connection

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        raw_key = request.POST.get("key", "")
        if not raw_key:
            return HttpResponseBadRequest("Missing key")

        # Validate the key contains a known prefix to prevent arbitrary key deletion
        if not any(prefix in raw_key for prefix, _ in self.KEY_PREFIXES):
            return HttpResponseBadRequest("Invalid key")

        conn = get_redis_connection("default")
        conn.delete(raw_key)
        self.message_user(request, f"Deleted key: {raw_key}")
        return HttpResponseRedirect("../")

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


# Register the custom Redis admin view using a proxy model placeholder
class BlockedKeyProxy(User):
    class Meta:
        proxy = True
        verbose_name = "Blocked Key"
        verbose_name_plural = "Blocked IPs & Users (Redis)"


admin.site.register(BlockedKeyProxy, BlockedKeysAdmin)
