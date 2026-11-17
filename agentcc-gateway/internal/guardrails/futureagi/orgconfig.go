package futureagi

import (
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// New creates a FutureAGIGuardrail from an organization's config. It is the
// default, and the constructor for anything an organization configures
// (internal/guardrails/orgfactory). An org config is caller-controlled, so New
// does two things a config-file rule (NewConfigFileRule) does not.
//
// It never expands organization strings from the environment: a credential
// or base_url stays literal. A value containing "${" is a configuration
// fault: Check refuses every request and the error names only the field.
// Pair mode takes the operator-supplied personal pair as-is. gateway_service
// instead reads deployment signing material internally, never from a rule,
// and only signs for the operator's FI_BASE_URL. No OrgApiKey is injected.
//
// Its evaluation call goes through the gateway's guarded egress dialer
// (internal/mcp/egress), as an org webhook or external check does: a
// loopback, private, link-local or cloud-metadata address is refused after
// DNS, on every attempt and every redirect hop, unless the operator listed it
// in mcp.egress.private_allow, and a redirect to another origin does not
// carry the org's keys. A nil
// dialer is the guard with no exceptions.
//
// The one destination that is not the org's choice is the operator's own
// backend, FI_BASE_URL: a rule that names no base_url or the built-in default
// (what the platform fills in on hosted) is sent there, and so is one whose
// base_url is exactly FI_BASE_URL (a trailing slash aside), which yields the
// same eval URL. That destination is operator configuration, normally an
// in-cluster address, so it keeps a plain client and needs no listing, and
// the FI_BASE_URL fallback holds no secret. Any other base_url, including
// another path, query or spelling of the same host, goes through the guard
// in pair mode. gateway_service is stricter: only an omitted base_url or a
// normalized match to FI_BASE_URL is accepted, and redirects are refused.
func New(name string, cfg map[string]interface{}, dialer *egress.Dialer) *FutureAGIGuardrail {
	g := build(name, cfg, keepLiteral, func(timeout time.Duration, operatorBackend bool) *http.Client {
		if operatorBackend {
			return plainClient(timeout, operatorBackend)
		}
		return dialer.CallbackClient(timeout)
	})
	if field := envRefField(cfg); field != "" {
		g.configErr = fmt.Errorf(
			"futureagi guardrail %q cannot run: %s is an environment reference (contains ${...}), which an organization config may not use; set the value itself",
			name, field)
	}
	return g
}

// keepLiteral is New's expander: it reads no environment, so a value passes
// through unchanged.
func keepLiteral(s string) string { return s }

// envRefField names the first credential or base_url field of a rule whose
// value contains "${", or "" when none does. It matches any "${", not
// only a whole-string ${VAR}, so the gateway refuses the same values Django
// does (a legacy stored value, or a push that bypassed Django's check).
func envRefField(cfg map[string]interface{}) string {
	for _, key := range []string{"api_key", "secret_key", "base_url"} {
		if v, ok := cfg[key].(string); ok && strings.Contains(v, "${") {
			return key
		}
	}
	return ""
}
