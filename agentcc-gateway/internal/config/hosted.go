package config

import (
	"fmt"
	"log/slog"
	"net"
	"net/netip"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// EnvDeploymentProfile is operator-owned, not a YAML/org/request setting.
// Hosted activation remains default-OFF and requires a separately qualified
// release. Neither the profile nor activation can be selected by YAML/org data.
const EnvDeploymentProfile = "AGENTCC_DEPLOYMENT_PROFILE"

// EnvHostedOneKeyActivation permits only the qualified hosted one-key shape.
// It never weakens transport, billing-chain, MCP or org opt-in policy.
const EnvHostedOneKeyActivation = "AGENTCC_HOSTED_ONEKEY_ACTIVATION"

func (c *Config) IsHosted() bool { return c.deploymentProfile == "hosted" }

// Read before loadFromEnv: that legacy path tolerates malformed overrides and
// logs their values. Hosted config must refuse them without echoing values.
func (c *Config) loadDeploymentProfile() error {
	c.deploymentProfile = os.Getenv(EnvDeploymentProfile)
	activation := os.Getenv(EnvHostedOneKeyActivation)
	switch c.deploymentProfile {
	case "", "self-hosted":
		if activation != "" {
			slog.Warn("AGENTCC_HOSTED_ONEKEY_ACTIVATION ignored without AGENTCC_DEPLOYMENT_PROFILE=hosted")
		}
		return nil
	case "hosted":
	default:
		return fmt.Errorf("AGENTCC_DEPLOYMENT_PROFILE must be self-hosted or hosted")
	}
	switch activation {
	case "", "false":
		c.hostedOneKeyActivation = false
	case "true":
		c.hostedOneKeyActivation = true
		// Activated hosted operators must explicitly choose velocity limits.
		// Clear only these inherited defaults before decoding the config file;
		// preparation and self-hosted retain DefaultSpendGuard unchanged.
		c.Managed.SpendGuard.MaxUSDPerMinute = 0
		c.Managed.SpendGuard.MaxUSDPerHour = 0
	default:
		return fmt.Errorf("AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false")
	}
	c.internalBackendKey = os.Getenv("AGENTCC_INTERNAL_API_KEY")
	for _, entry := range os.Environ() {
		name, value, _ := strings.Cut(entry, "=")
		if value != "" && (strings.HasPrefix(name, "AGENTCC_TEST_") || strings.HasPrefix(name, "INTEGRATIONS_TEST_")) {
			return fmt.Errorf("hosted profile refuses test endpoint overrides")
		}
	}
	for _, name := range []string{EnvAllowInsecureAdminToken, EnvAllowPrivateProviderURLs} {
		if v := os.Getenv(name); v != "" && v != "false" && v != "0" {
			return fmt.Errorf("hosted profile refuses %s", name)
		}
	}
	for _, name := range []string{"AGENTCC_AUTH_ENABLED", "AGENTCC_SYNC_ON_STARTUP"} {
		if v, present := os.LookupEnv(name); present {
			enabled, err := strconv.ParseBool(v)
			if err != nil || !enabled {
				return fmt.Errorf("hosted profile requires %s=true", name)
			}
		}
	}
	for _, name := range []string{"AGENTCC_SYNC_INTERVAL", "AGENTCC_DEFAULT_REQUEST_TIMEOUT", "AGENTCC_MAX_REQUEST_TIMEOUT", "AGENTCC_SHUTDOWN_TIMEOUT"} {
		if v, present := os.LookupEnv(name); present {
			d, err := time.ParseDuration(v)
			if err != nil || d <= 0 {
				return fmt.Errorf("hosted profile requires positive %s", name)
			}
		}
	}
	return nil
}

func (c *Config) validateHosted() error {
	if !c.IsHosted() {
		return nil
	}
	require := func(ok bool, field string) error {
		if !ok {
			return fmt.Errorf("hosted profile requires %s", field)
		}
		return nil
	}
	for _, item := range []struct {
		ok    bool
		field string
	}{
		{c.Auth.Enabled, "auth.enabled=true"},
		{!c.Admin.AllowInsecureToken, "development admin override disabled"},
		{adminTokenProblem(c.Admin.Token) == "", "valid admin.token"},
		{adminTokenProblem(c.ControlPlane.AdminToken) == "", "valid control_plane.admin_token"},
		{c.ControlPlane.AdminToken == c.Admin.Token, "matching gateway/backend admin authentication"},
		{adminTokenProblem(c.internalBackendKey) == "", "valid AGENTCC_INTERNAL_API_KEY"},
		{adminTokenProblem(c.ControlPlane.WebhookSecret) == "", "valid control_plane.webhook_secret"},
		{validServiceURL(c.ControlPlane.URL), "control_plane.url without embedded credentials"},
		{c.ControlPlane.SyncOnStartup, "control_plane.sync_on_startup=true"},
		{c.ControlPlane.SyncInterval > 0 && c.ControlPlane.SyncInterval <= 5*time.Minute, "control_plane.sync_interval in (0,5m]"},
		{c.Guardrails.Enabled, "guardrails.enabled=true"},
		{c.Server.DefaultRequestTimeout > 0 && c.Server.MaxRequestTimeout > 0 && c.Server.MaxRequestTimeout <= 10*time.Minute && c.Server.DefaultRequestTimeout <= c.Server.MaxRequestTimeout, "bounded request timeouts at most 10m"},
		{c.Server.TrustedHops == 0 && len(c.Server.TrustedProxies) > 0, "explicit server.trusted_proxies and no trusted_hops"},
		{c.Redis.Enabled && strings.TrimSpace(c.Redis.Address) != "" && c.Redis.Timeout > 0 && c.Redis.Timeout <= 2*time.Second, "shared redis with timeout in (0,2s]"},
		{!c.OrgProviders.AllowPrivateURLs, "private provider override disabled"},
		{!c.MCP.DevInsecureState && !c.MCP.AllowAnonymousDev, "MCP development overrides disabled"},
		{c.hostedControlPlaneEgress(), "private egress limited to the exact operator control-plane host:port"},
		{!c.MCP.Enabled && len(c.MCP.ManagedServers) == 0, "MCP disabled until the paired release is qualified"},
		{!c.Tools.Enabled, "tools.enabled=false until separately qualified"},
	} {
		if err := require(item.ok, item.field); err != nil {
			return err
		}
	}
	host, port, err := net.SplitHostPort(c.Redis.Address)
	number, portErr := strconv.Atoi(port)
	if err != nil || portErr != nil || number < 1 || number > 65535 || host == "" || strings.EqualFold(host, "localhost") {
		return fmt.Errorf("hosted profile requires a shared redis host:port")
	}
	if ip, err := netip.ParseAddr(host); err == nil && (ip.IsLoopback() || ip.IsUnspecified()) {
		return fmt.Errorf("hosted profile refuses per-replica loopback redis")
	}
	for _, proxy := range c.Server.TrustedProxies {
		// ParseTrustedProxies checks syntax too; never echo an operator value.
		if p, err := netip.ParsePrefix(proxy); err == nil && p.Bits() == 0 {
			return fmt.Errorf("hosted profile refuses wildcard trusted proxies")
		}
	}
	if _, err := ParseTrustedProxies(c.Server.TrustedProxies, c.Server.TrustedHops); err != nil {
		return fmt.Errorf("hosted profile requires valid server.trusted_proxies")
	}
	for _, p := range c.Providers {
		if p.SkipTLS || (p.Local != nil && *p.Local) {
			return fmt.Errorf("hosted profile refuses insecure or local provider overrides")
		}
		if p.Managed {
			if !c.hostedOneKeyActivation {
				return fmt.Errorf("hosted preparation refuses providers.*.managed=true")
			}
			// This hosted allowlist is deliberately narrower than the generic
			// usage-presence allowlist. Native formats are not activated here.
			if p.APIFormat != "openai" {
				return fmt.Errorf("hosted one-key requires managed providers.*.api_format=openai")
			}
			switch p.Type {
			case "", "openrouter": // explicit OpenAI-compatible endpoint or aggregator preset
			default:
				return fmt.Errorf("hosted one-key requires an allowed managed provider kind (unset or openrouter)")
			}
		}
		u, err := url.Parse(p.BaseURL)
		if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
			return fmt.Errorf("hosted profile requires credential-free HTTPS provider URLs")
		}
		host := strings.ToLower(strings.TrimSuffix(u.Hostname(), "."))
		if ip, err := netip.ParseAddr(host); err == nil {
			if hostedNonPublicAddr(ip) {
				return fmt.Errorf("hosted profile refuses private provider URLs")
			}
			continue
		}
		if host == "localhost" || strings.HasSuffix(host, ".localhost") || strings.HasSuffix(host, ".local") {
			return fmt.Errorf("hosted profile refuses development provider URLs")
		}
		if hostedNumericHost(host) {
			return fmt.Errorf("hosted profile refuses numeric provider hostnames")
		}
		if hostedNonPublicName(host) {
			return fmt.Errorf("hosted profile refuses non-public provider hostnames")
		}
	}
	if c.hostedOneKeyActivation {
		// These are startup invariants, not org/request policy. Keep preparation
		// and self-hosted settings unchanged; report names, never operator values.
		var relaxed []string
		if !c.Managed.RequireCredits {
			relaxed = append(relaxed, "managed.require_credits=false")
		}
		if !c.Managed.RequireRedis {
			relaxed = append(relaxed, "managed.require_redis=false")
		}
		sg := c.Managed.SpendGuard
		// Invalid nonzero caps are refused even when another cap enforces.
		// Redis reads micros (zero means no cap), not the original USD value.
		velocity := false
		for _, cap := range []struct {
			field string
			usd   float64
		}{
			{"max_usd_per_minute", sg.MaxUSDPerMinute},
			{"max_usd_per_hour", sg.MaxUSDPerHour},
			{"new_org_daily_cap_usd", sg.NewOrgDailyCapUSD},
		} {
			micros, problem := billing.SpendCapMicros(cap.usd)
			if problem != "" {
				relaxed = append(relaxed, "managed.spend_guard."+cap.field+" is "+problem)
			}
			if cap.field != "new_org_daily_cap_usd" && micros > 0 {
				velocity = true
			}
		}
		// The new-org cap expires after seven days. Neither it, alerts nor
		// post-settlement auto_pause bounds established organizations' velocity.
		if !velocity {
			relaxed = append(relaxed, "managed.spend_guard requires an explicitly configured enforcing velocity cap (max_usd_per_minute or max_usd_per_hour)")
		}
		if c.Managed.GrantOnlyCredits {
			relaxed = append(relaxed, "managed.grant_only_credits=true")
		}
		if len(relaxed) != 0 {
			return fmt.Errorf("hosted one-key refuses %s", strings.Join(relaxed, ", "))
		}
		if c.Managed.MixedBillingChains {
			return fmt.Errorf("hosted one-key refuses managed.mixed_billing_chains=true")
		}
	}
	// Activation does not qualify a global fallback profile. Runtime org/request
	// hops require the separate mixed-hop guard on the converged release head.
	// Leave preparation-only behavior unchanged when the operator flag is OFF.
	if c.hostedOneKeyActivation && (len(c.Routing.ModelFallbacks) != 0 || c.Routing.Failover.Enabled || c.Routing.Retry.Enabled) {
		return fmt.Errorf("hosted profile refuses model fallback chains, failover and retries")
	}
	return nil
}

var hostedLocalSuffixes = []string{"internal", "lan", "home.arpa", "corp", "localdomain", "svc", "intranet", "private", "home"}

// hostedNumericHost reports whether a host that is not an IP literal is
// still numeric: its last label is all digits (2130706433, 0177.0.0.1,
// 127.1) or hexadecimal (0x7f000001). No public top-level domain is, and a
// resolver may read such a name as an IPv4 address.
func hostedNumericHost(host string) bool {
	last := host[strings.LastIndexByte(host, '.')+1:]
	if last == "" {
		return false
	}
	if strings.HasPrefix(last, "0x") {
		return true
	}
	for i := 0; i < len(last); i++ {
		if last[i] < '0' || last[i] > '9' {
			return false
		}
	}
	return true
}

// hostedNonPublicName reports whether host is a single-label name or under
// a suffix that is never public DNS (hostedLocalSuffixes; localhost and
// .local are refused before this as development hosts). Two-label Kubernetes
// short names are recognized only for the four built-in namespaces. Arbitrary
// service.namespace names cannot be distinguished from public domains without
// DNS; do not reject every two-label name. Managed endpoints still require
// trusted TLS verification at startup, including for custom namespace names.
func hostedNonPublicName(host string) bool {
	labels := strings.Split(host, ".")
	if len(labels) < 2 {
		return true
	}
	for _, label := range labels {
		if label == "" {
			return true
		}
	}
	if len(labels) == 2 {
		switch labels[1] {
		case "default", "kube-system", "kube-public", "kube-node-lease":
			return true
		}
	}
	for _, suffix := range hostedLocalSuffixes {
		if host == suffix || strings.HasSuffix(host, "."+suffix) {
			return true
		}
	}
	return false
}

func validServiceURL(value string) bool {
	u, err := url.Parse(value)
	return err == nil && (u.Scheme == "http" || u.Scheme == "https") && u.Hostname() != "" && u.User == nil && u.RawQuery == "" && u.Fragment == ""
}

// The same guarded dialer serves saved Future AGI evaluation rules and alerts,
// even when MCP is off. Preserve those policies without admitting a subnet,
// arbitrary private host, K2 endpoint or paid server. The backend is already an
// operator-owned authenticated destination, never an organization input.
func (c *Config) hostedControlPlaneEgress() bool {
	u, err := url.Parse(c.ControlPlane.URL)
	if err != nil || u.Hostname() == "" {
		return false
	}
	port := u.Port()
	if port == "" {
		if u.Scheme == "https" {
			port = "443"
		} else {
			port = "80"
		}
	}
	exact := net.JoinHostPort(u.Hostname(), port)
	for _, entry := range c.MCP.Egress.PrivateAllow {
		if entry != exact || checkPrivateAllowEntry(entry) != nil {
			return false
		}
	}
	return true
}

// IsCloudDeployment mirrors tfc.ee_loader.is_cloud_env. The private-provider
// opt-in is self-hosted only; CLOUD_DEPLOYMENT identifies the hosted regions.
func IsCloudDeployment() bool {
	switch strings.ToUpper(strings.TrimSpace(os.Getenv("CLOUD_DEPLOYMENT"))) {
	case "US", "EU", "DEV":
		return true
	default:
		return false
	}
}
