"""Published auto-reload authorization, versioned independently of API releases.

The request serializer publishes both this text and its exact version. A client
must present the terms and the chosen USD threshold/amount before sending the
version; an omitted value is never filled in on the client's behalf. A terms
change must bump the version and requires new consent from existing accounts.
"""

AUTO_RELOAD_CONSENT_VERSION = "auto-reload-v1"
AUTO_RELOAD_CONSENT_TERMS = (
    "I authorize Future AGI to charge my saved payment method for the selected "
    "auto-reload amount whenever my credit balance is at or below the selected "
    "threshold, subject to configured limits. These charges may occur without "
    "me being present. I can stop future auto-reloads by disabling auto-reload "
    "in my credit settings; a payment already initiated may still complete."
)


class AutoReloadConsentError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require_current_consent_version(version):
    if not version:
        raise AutoReloadConsentError(
            "auto_reload_consent_required",
            "Enabling auto-reload requires acknowledgement of the current terms.",
        )
    if version != AUTO_RELOAD_CONSENT_VERSION:
        raise AutoReloadConsentError(
            "auto_reload_consent_outdated",
            "Auto-reload terms changed. Review and acknowledge the current version.",
        )
