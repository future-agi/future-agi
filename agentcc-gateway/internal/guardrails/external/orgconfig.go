package external

import (
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// New creates an ExternalGuardrail from a rule's config without reading the
// gateway's environment. It is the default, and the constructor for anything
// an organization configures (internal/guardrails/orgfactory): an org config
// is caller-controlled, so every adapter field (endpoint, api_key, region,
// client_secret, token, …) is taken exactly as the organization set it.
//
// The adapters read each top-level string through expandEnv
// (getStringConfig), which turns a whole-string ${NAME} into
// os.Getenv("NAME") of the gateway process. So, before building the adapter,
// New refuses a config whose any top-level string contains "${": the rule is
// built with no adapter and configErr set, so its Check fails closed and the
// error names the field, never the value. With no "${" present, expandEnv is
// a no-op (its ${ prefix check fails before os.Getenv), so building the
// adapter reads no environment. Only a rule in the gateway's own config file
// may expand ${VAR}, through NewConfigFileRule.
//
// So for every external provider an org points a check at its own endpoint
// with its own literal key, and the gateway never resolves a ${VAR} in an
// org config against its own environment.
//
// A provider endpoint, base_url or region is caller-controlled too, so the
// call goes through the gateway's guarded egress dialer (internal/mcp/egress):
// a link-local or cloud-metadata address is always refused, a loopback or
// private one unless the operator listed it in mcp.egress.private_allow, and
// redirects are re-checked. A nil dialer is the guard with no exceptions.
func New(name string, cfg map[string]interface{}, dialer *egress.Dialer) *ExternalGuardrail {
	if field := envRefField(cfg); field != "" {
		return &ExternalGuardrail{
			name: name,
			configErr: fmt.Errorf(
				"external guardrail %q cannot run: %s is an environment reference (contains ${...}), which an organization config may not use; set the value itself",
				name, field),
		}
	}
	return build(name, cfg, func(timeout time.Duration) *http.Client {
		return dialer.CallbackClient(timeout)
	})
}

// envRefField names the first config field whose string value contains "${",
// or "" when none does. It scans every top-level string (every value an
// adapter can pass to expandEnv), so no adapter field slips an environment
// reference past the gateway. A nested value is never read by expandEnv, so
// it is not scanned, and Django leaves it free too.
func envRefField(cfg map[string]interface{}) string {
	keys := make([]string, 0, len(cfg))
	for key := range cfg {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		if v, ok := cfg[key].(string); ok && strings.Contains(v, "${") {
			return key
		}
	}
	return ""
}
