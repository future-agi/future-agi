package mcp

// CredentialRequired is the existing MC.3 in-band authentication refusal.
// It carries identifiers and lifecycle reasons only, never a credential value.
func CredentialRequired(serverID, reason string) *Denial {
	// MC.3 deliberately has a bounded set of remediation reasons. Revoked,
	// disabled, superseded and unknown states all require reauthentication.
	switch reason {
	case "expired", "no_token", "no_user_identity", "account_ambiguous":
	default:
		reason = "needs_reauth"
	}
	return &Denial{
		Type: "auth_required", Code: "auth_required",
		Message:  "Connect your account for this server to use its tools.",
		Metadata: map[string]any{"server_id": serverID, "reason": reason},
	}
}
