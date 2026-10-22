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
// It reads no environment: a credential or base_url is taken exactly as the
// organization set it, with no ${VAR} expansion. A value that contains "${"
// is a configuration fault: the rule is built with configErr set, so its
// Check refuses every request exactly as an incomplete rule does, and the
// error names the field, never the value. Only a config-file rule may write
// api_key: "${FI_API_KEY}" and have the gateway resolve it; an organization
// never needs to (for a futureagi-eval check that calls the platform, Django
// fills in the organization's own platform key), and the gateway resolves no
// environment reference in an org config.
//
// Its evaluation call goes through the gateway's guarded egress dialer
// (internal/mcp/egress), as an org webhook or external check does. After
// DNS, on every attempt and every redirect hop, a link-local or
// cloud-metadata address is always refused, and a loopback or private one
// unless the operator listed it in mcp.egress.private_allow; a redirect to
// another origin does not carry the org's keys. A nil dialer is the guard
// with no exceptions.
//
// The one destination that is not the org's choice is the operator's own
// backend, FI_BASE_URL: a rule that names no base_url or the built-in default
// (what the platform fills in on hosted) is sent there, and so is one whose
// base_url is exactly FI_BASE_URL (a trailing slash aside), which yields the
// same eval URL. That destination is operator configuration, normally an
// in-cluster address, so it keeps a plain client and needs no listing, and
// the FI_BASE_URL fallback holds no secret. Any other base_url, including
// another path, query or spelling of the same host, goes through the guard.
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
// does, whatever config reaches it.
func envRefField(cfg map[string]interface{}) string {
	for _, key := range []string{"api_key", "secret_key", "base_url"} {
		if v, ok := cfg[key].(string); ok && strings.Contains(v, "${") {
			return key
		}
	}
	return ""
}
