package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

// testStateKey is a fixed 32-byte test key in EnvMCPStateKeys' format.
const testStateKey = "mcp-test-state-key-0123456789abc"

// loadYAML writes body to a temp config file and loads it.
func loadYAML(t *testing.T, body string) (*Config, error) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return Load(path)
}

// checkDefaults compares each named value with its expected default.
func checkDefaults(t *testing.T, checks map[string][2]any) {
	t.Helper()
	for key, gw := range checks {
		if !reflect.DeepEqual(gw[0], gw[1]) {
			t.Errorf("%s = %#v, want %#v", key, gw[0], gw[1])
		}
	}
}

func TestToolMarkupInheritsMarkup(t *testing.T) {
	c := DefaultConfig()
	if c.Managed.ToolMarkupPercent != -1 || c.Managed.ToolMarkupBps() != 500 {
		t.Fatalf("default tool markup = %v (%d bps), want -1 inheriting 500 bps", c.Managed.ToolMarkupPercent, c.Managed.ToolMarkupBps())
	}
	for _, tc := range []struct {
		markup, tool float64
		want         int
	}{
		{7.5, -1, 750},
		{5, 0, 0},
		{5, 12, 1200},
		{0, -1, 0},
		{5, 2.345, 235},
	} {
		m := ManagedConfig{MarkupPercent: tc.markup, ToolMarkupPercent: tc.tool}
		if got := m.ToolMarkupBps(); got != tc.want {
			t.Errorf("markup %v, tool %v: ToolMarkupBps = %d, want %d", tc.markup, tc.tool, got, tc.want)
		}
	}
	c, err := loadYAML(t, "managed:\n  markup_percent: 8\n")
	if err != nil {
		t.Fatal(err)
	}
	if c.Managed.ToolMarkupBps() != 800 {
		t.Errorf("an unset tool_markup_percent did not inherit markup_percent: %d bps", c.Managed.ToolMarkupBps())
	}
	for _, bad := range []float64{-2, -0.5, 100.5} {
		c := DefaultConfig()
		c.Managed.ToolMarkupPercent = bad
		if err := c.Validate(); err == nil || !strings.Contains(err.Error(), "tool_markup_percent") {
			t.Errorf("tool_markup_percent %v: Validate = %v", bad, err)
		}
	}
}

func TestMCPConfigDefaultsAdditive(t *testing.T) {
	m := DefaultConfig().MCP
	checkDefaults(t, map[string][2]any{
		"mcp.max_body_bytes":                   {m.MaxBodyBytes, int64(4 << 20)},
		"mcp.max_result_bytes":                 {m.MaxResultBytes, int64(8 << 20)},
		"mcp.protocol.versions":                {m.Protocol.Versions, []string{"2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}},
		"mcp.protocol.legacy_sessions":         {m.Protocol.LegacySessions, true},
		"mcp.protocol.http_sse":                {m.Protocol.HTTPSSE, true},
		"mcp.state_keys":                       {len(m.StateKeys), 0},
		"mcp.dev_insecure_state":               {m.DevInsecureState, false},
		"mcp.stdio.enabled":                    {m.Stdio.Enabled, true},
		"mcp.stdio.launcher":                   {m.Stdio.Launcher, "local"},
		"mcp.stdio.base_env":                   {m.Stdio.BaseEnv, []string{"PATH", "HOME", "LANG", "TMPDIR"}},
		"mcp.stdio.grace":                      {m.Stdio.Grace, 5 * time.Second},
		"mcp.stdio.max_message_bytes":          {m.Stdio.MaxMessageBytes, int64(16 << 20)},
		"mcp.stdio.stderr_max_line_bytes":      {m.Stdio.StderrMaxLineBytes, 1024},
		"mcp.egress.private_allow":             {len(m.Egress.PrivateAllow), 0},
		"mcp.oauth":                            {m.OAuth, MCPOAuthConfig{}},
		"mcp.django":                           {m.Django, MCPDjangoConfig{}},
		"mcp.approvals.default_ttl":            {m.Approvals.DefaultTTL, 15 * time.Minute},
		"mcp.metrics.tool_labels":              {m.Metrics.ToolLabels, false},
		"mcp.allow_anonymous_dev":              {m.AllowAnonymousDev, false},
		"mcp.static.org_id":                    {m.Static.OrgID, ""},
		"mcp.ratelimit.replicas":               {m.RateLimit.Replicas, 1},
		"mcp.instance_id":                      {m.InstanceID, ""},
		"mcp.public_url":                       {m.PublicURL, ""},
		"mcp.callback_max_calls":               {m.CallbackMaxCalls, 20},
		"mcp.responses_passthrough.managed":    {m.ResponsesPassthrough.Managed, "deny"},
		"mcp.responses_passthrough.byok":       {m.ResponsesPassthrough.BYOK, "allow"},
		"mcp.oauth_adapters.refresh_skew":      {m.OAuthAdapters.RefreshSkew, 120 * time.Second},
		"mcp.oauth_adapters.retry_once_on_401": {m.OAuthAdapters.RetryOnceOn401, true},
		"mcp.managed_servers":                  {len(m.ManagedServers), 0},
		// The base MCP keys keep their defaults.
		"mcp.enabled":           {m.Enabled, false},
		"mcp.endpoint":          {m.Endpoint, "/mcp"},
		"mcp.max_agent_depth":   {m.MaxAgentDepth, 10},
		"mcp.tool_call_timeout": {m.ToolCallTimeout, 30 * time.Second},
		"mcp.session_ttl":       {m.SessionTTL, 30 * time.Minute},
		"mcp.separator":         {m.Separator, "_"},
	})

	// An MCP block with only the base keys parses unchanged and keeps every other default.
	c, err := loadYAML(t, `
mcp:
  enabled: false
  endpoint: /tools
  max_agent_depth: 3
  separator: "__"
  servers:
    files:
      transport: stdio
      command: npx
      args: ["-y", "server-filesystem"]
      tools_cache_ttl: 5m
    search:
      transport: http
      url: https://mcp.example.com
      auth: {type: bearer, token: plain-token}
  guardrails:
    enabled: true
    blocked_tools: [shell_exec]
auth:
  keys:
    - name: k
      key: sk-test
      allowed_tools: [a]
      mcp_allowed_tools: ["files_*"]
      mcp_denied_tools: ["files_delete"]
`)
	if err != nil {
		t.Fatal(err)
	}
	if c.MCP.Endpoint != "/tools" || c.MCP.MaxAgentDepth != 3 || c.MCP.Separator != "__" || !c.MCP.Guardrails.Enabled {
		t.Errorf("existing mcp keys changed: %+v", c.MCP)
	}
	files, search := c.MCP.Servers["files"], c.MCP.Servers["search"]
	if files.Command != "npx" || files.ToolsCacheTTL != 5*time.Minute || search.Auth.Token != "plain-token" || search.URL != "https://mcp.example.com" {
		t.Errorf("existing server keys changed: files %+v, search %+v", files, search)
	}
	if c.MCP.MaxResultBytes != 8<<20 || !c.MCP.Protocol.LegacySessions || c.MCP.Stdio.Grace != 5*time.Second || c.MCP.FairUse.OrgRPM != 600 {
		t.Errorf("a partial mcp block lost new defaults: %+v", c.MCP)
	}
	if k := c.Auth.Keys[0]; !reflect.DeepEqual(k.MCPAllowedTools, []string{"files_*"}) || !reflect.DeepEqual(k.MCPDeniedTools, []string{"files_delete"}) {
		t.Errorf("key mcp tool lists = %v / %v", k.MCPAllowedTools, k.MCPDeniedTools)
	}

	// The session store resolves at load: redis when Redis is configured.
	if c.MCP.SessionStore != MCPSessionStoreMemory {
		t.Errorf("session_store without Redis = %q, want memory", c.MCP.SessionStore)
	}
	c, err = loadYAML(t, "redis:\n  enabled: true\n  address: 127.0.0.1:16379\n")
	if err != nil {
		t.Fatal(err)
	}
	if c.MCP.SessionStore != MCPSessionStoreRedis {
		t.Errorf("session_store with Redis = %q, want redis", c.MCP.SessionStore)
	}
	if _, err := loadYAML(t, "mcp:\n  session_store: redis\n"); err == nil {
		t.Error("session_store: redis without Redis was accepted")
	}
	for name, body := range map[string]string{
		"unknown version":   "mcp:\n  protocol:\n    versions: [\"2099-01-01\"]\n",
		"no version on":     "mcp:\n  enabled: true\n  dev_insecure_state: true\n  protocol:\n    versions: []\n",
		"launcher":          "mcp:\n  stdio:\n    launcher: docker\n",
		"zero result bytes": "mcp:\n  max_result_bytes: 0\n",
		"replicas":          "mcp:\n  ratelimit:\n    replicas: 0\n",
		"passthrough":       "mcp:\n  responses_passthrough:\n    managed: maybe\n",
		"server era":        "mcp:\n  servers:\n    s:\n      url: https://x.example\n      era: \"2023-01-01\"\n",
		"public url":        "mcp:\n  public_url: gateway.local\n",
	} {
		if _, err := loadYAML(t, body); err == nil {
			t.Errorf("%s: Load accepted it", name)
		}
	}
}

// fullSecretConfig sets every secret-bearing MCP field.
func fullSecretConfig() (*Config, []string) {
	c := DefaultConfig()
	c.MCP.Servers = map[string]MCPServerConfig{
		"s": {
			Auth:        MCPAuthConfig{Type: "bearer", Token: "mcp-secret-token", Header: "X-Key", Key: "mcp-secret-key"},
			Env:         map[string]string{"UPSTREAM_TOKEN": "mcp-secret-env-value"},
			EnvFromHost: []string{"HOST_VAR_NAME"},
		},
	}
	c.MCP.StateKeys = []string{testStateKey, "mcp-previous-state-key-0123456789"}
	c.MCP.OAuth.CallbackSigningKey = "mcp-secret-callback-signing-key"
	c.MCP.Django.InternalTokenSecret = "mcp-secret-django-internal-token"
	return c, []string{
		"mcp-secret-token", "mcp-secret-key", "mcp-secret-env-value", "UPSTREAM_TOKEN",
		testStateKey, "mcp-previous-state-key-0123456789",
		"mcp-secret-callback-signing-key", "mcp-secret-django-internal-token",
	}
}

func TestMCPSecretsNotJSONMarshalled(t *testing.T) {
	c, secrets := fullSecretConfig()
	raw, err := json.Marshal(c)
	if err != nil {
		t.Fatal(err)
	}
	for _, secret := range secrets {
		if strings.Contains(string(raw), secret) {
			t.Errorf("json.Marshal(config) contains %q", secret)
		}
	}
	// The non-secret neighbours are still there.
	for _, kept := range []string{`"type":"bearer"`, `"header":"X-Key"`, `"env_from_host":["HOST_VAR_NAME"]`} {
		if !strings.Contains(string(raw), kept) {
			t.Errorf("json.Marshal(config) lost %s", kept)
		}
	}
}

func TestMCPSecretsStillParseFromYAML(t *testing.T) {
	t.Setenv(EnvMCPStateKeys, "")
	t.Setenv(EnvMCPDjangoSecret, "")
	c, err := loadYAML(t, `
mcp:
  state_keys: ["`+testStateKey+`"]
  servers:
    s:
      command: srv
      transport: stdio
      auth: {type: api_key, header: X-Key, key: yaml-key, token: yaml-token}
      env: {UPSTREAM_TOKEN: yaml-env-value}
      env_from_host: [HOST_VAR]
  oauth:
    callback_signing_key: yaml-signing-key
  django:
    base_url: http://django.local
    internal_token_secret: yaml-django-secret
`)
	if err != nil {
		t.Fatal(err)
	}
	s := c.MCP.Servers["s"]
	if s.Auth.Key != "yaml-key" || s.Auth.Token != "yaml-token" || s.Env["UPSTREAM_TOKEN"] != "yaml-env-value" || !reflect.DeepEqual(s.EnvFromHost, []string{"HOST_VAR"}) {
		t.Errorf("server secrets did not parse: %+v", s)
	}
	if !reflect.DeepEqual(c.MCP.StateKeys, []string{testStateKey}) || c.MCP.OAuth.CallbackSigningKey != "yaml-signing-key" || c.MCP.Django.InternalTokenSecret != "yaml-django-secret" {
		t.Errorf("mcp secrets did not parse: state keys %d, oauth %q, django %q", len(c.MCP.StateKeys), c.MCP.OAuth.CallbackSigningKey, c.MCP.Django.InternalTokenSecret)
	}
	t.Setenv(EnvMCPDjangoSecret, "env-django-secret")
	c, err = loadYAML(t, "mcp:\n  django:\n    internal_token_secret: yaml-django-secret\n")
	if err != nil {
		t.Fatal(err)
	}
	if c.MCP.Django.InternalTokenSecret != "env-django-secret" {
		t.Errorf("%s did not set the django secret", EnvMCPDjangoSecret)
	}
}

func TestForwardHeadersRefusedNamesAtLoad(t *testing.T) {
	for _, h := range []string{
		"authorization", "Authorization", "cookie", "X-API-Key", "proxy-authorization",
		"Mcp-Session-Id", "MCP-Protocol-Version", "x-agentcc-org-id", "X-Agentcc-Run-Id", "x-omega-agent", " x-omega-step ",
	} {
		if !ForwardHeaderRefused(h) {
			t.Errorf("ForwardHeaderRefused(%q) = false", h)
		}
		_, err := loadYAML(t, "mcp:\n  servers:\n    crm:\n      url: https://crm.example\n      forward_headers: [x-tenant, \""+h+"\"]\n")
		if err == nil || !strings.Contains(err.Error(), "mcp.servers.crm.forward_headers") || !strings.Contains(err.Error(), h) {
			t.Errorf("forward_headers %q: Load = %v, want a refusal naming the server and header", h, err)
		}
	}
	for _, h := range []string{"x-tenant", "x-request-source", "accept-language", "x-agentccc", "x-omegaa"} {
		if ForwardHeaderRefused(h) {
			t.Errorf("ForwardHeaderRefused(%q) = true", h)
		}
	}
	c, err := loadYAML(t, "mcp:\n  servers:\n    crm:\n      url: https://crm.example\n      forward_headers: [x-tenant, x-request-source]\n      propagate_depth: true\n      era: \"2025-06-18\"\n      timeout_ms: 5000\n      trusted_annotations: true\n")
	if err != nil {
		t.Fatalf("allowed forward headers refused: %v", err)
	}
	if s := c.MCP.Servers["crm"]; !s.PropagateDepth || s.Era != "2025-06-18" || s.TimeoutMS != 5000 || !s.TrustedAnnotations || len(s.ForwardHeaders) != 2 {
		t.Errorf("server keys = %+v", s)
	}
	if _, err := loadYAML(t, "mcp:\n  servers:\n    crm:\n      url: https://crm.example\n      forward_headers: [\"\"]\n"); err == nil {
		t.Error("an empty forward header name was accepted")
	}
}

func TestPrivateAllowRefusesMetadata(t *testing.T) {
	for _, entry := range []string{
		"169.254.169.254", "169.254.169.254:80", "169.254.0.0/16", "169.254.169.254/32", "169.254.170.0/24",
		"169.0.0.0/8", "0.0.0.0/0", "::/0", "::/1", "::ffff:0:0/96", "::ffff:169.254.169.254", "::ffff:169.254.0.0/112",
		"fd00:ec2::254", "[fd00:ec2::254]:80", "fd00:ec2::254/128",
		"100.100.100.200", "100.100.100.200/32", "168.63.129.16",
		"metadata.google.internal", "Metadata.Google.Internal.", "metadata.google.internal:80", "metadata.goog",
		"metadata", "instance-data", "instance-data.ec2.internal:8080",
	} {
		c := DefaultConfig()
		c.MCP.Egress.PrivateAllow = []string{"10.0.0.0/8", entry}
		err := c.Validate()
		if err == nil || !strings.Contains(err.Error(), "mcp.egress.private_allow[1]") || !strings.Contains(err.Error(), "metadata") {
			t.Errorf("private_allow %q: Validate = %v, want a metadata refusal", entry, err)
		}
	}
	for _, entry := range []string{"", "10.0.0.0/33", "mock.local:0", "mock.local:http", "[]:80"} {
		c := DefaultConfig()
		c.MCP.Egress.PrivateAllow = []string{entry}
		if err := c.Validate(); err == nil {
			t.Errorf("private_allow %q: malformed entry accepted", entry)
		}
	}
	c, err := loadYAML(t, `
mcp:
  egress:
    private_allow:
      - 10.0.0.0/8
      - 192.168.1.0/24
      - 100.64.0.0/10
      - fc00::/7
      - 127.0.0.1:9100
      - mcp.internal
      - mcp.internal:8443
      - 10.1.2.3
`)
	if err != nil {
		t.Fatalf("private ranges and hosts refused: %v", err)
	}
	if len(c.MCP.Egress.PrivateAllow) != 8 {
		t.Errorf("private_allow = %v", c.MCP.Egress.PrivateAllow)
	}
}

func TestFairUseDefaults(t *testing.T) {
	f := DefaultConfig().MCP.FairUse
	if f != (MCPFairUseConfig{OrgRPM: 600, KeyRPM: 300, MaxConcurrent: 32}) {
		t.Errorf("fair_use defaults = %+v", f)
	}
	c, err := loadYAML(t, "mcp:\n  fair_use:\n    org_rpm: 120\n")
	if err != nil {
		t.Fatal(err)
	}
	if c.MCP.FairUse != (MCPFairUseConfig{OrgRPM: 120, KeyRPM: 300, MaxConcurrent: 32}) {
		t.Errorf("a partial fair_use block = %+v", c.MCP.FairUse)
	}
	if _, err := loadYAML(t, "mcp:\n  fair_use:\n    key_rpm: -1\n"); err == nil {
		t.Error("a negative fair_use limit was accepted")
	}
}

func TestMaxResultBytesDefault(t *testing.T) {
	c := DefaultConfig()
	if c.MCP.MaxResultBytes != 8*1024*1024 || c.MCP.MaxBodyBytes != 4*1024*1024 {
		t.Errorf("max_result_bytes = %d, max_body_bytes = %d", c.MCP.MaxResultBytes, c.MCP.MaxBodyBytes)
	}
	c, err := loadYAML(t, "mcp:\n  max_result_bytes: 1048576\n  shaping:\n    default_budget_bytes: 4096\n    spill_max_bytes: 1048576\n")
	if err != nil {
		t.Fatal(err)
	}
	if c.MCP.MaxResultBytes != 1<<20 {
		t.Errorf("max_result_bytes = %d", c.MCP.MaxResultBytes)
	}
	if _, err := loadYAML(t, "mcp:\n  max_result_bytes: -1\n"); err == nil {
		t.Error("a negative max_result_bytes was accepted")
	}
}

func managedServersConfig(servers map[string]ManagedMCPServerConfig) *Config {
	c := DefaultConfig()
	c.MCP.ManagedServers = servers
	return c
}

var testPriceCard = ToolPriceCardRef{Capability: "search", Vendor: "exa", SKU: "search_auto"}

func TestManagedServerKindBillingPriceCardShape(t *testing.T) {
	c, err := loadYAML(t, `
mcp:
  managed_servers:
    exa:
      url: https://mcp.exa.example
      transport: http
      era: auto
      credential_env: EXA_MANAGED_KEY
      header: x-api-key
      price_card: {capability: search, vendor: exa, sku: search_auto}
      title: Exa
      description: Web search
    fagi.web:
      kind: builtin
      billing: delegated
`)
	if err != nil {
		t.Fatal(err)
	}
	exa, web := c.MCP.ManagedServers["exa"], c.MCP.ManagedServers["fagi.web"]
	if exa.Kind != ManagedServerKindRemote || exa.Billing != ManagedServerBillingReserve {
		t.Errorf("kind and billing did not default to remote and reserve: %+v", exa)
	}
	if exa.PriceCard != testPriceCard || exa.CredentialEnv != "EXA_MANAGED_KEY" || exa.Title != "Exa" {
		t.Errorf("exa = %+v", exa)
	}
	if web.Kind != ManagedServerKindBuiltin || web.Billing != ManagedServerBillingDelegated {
		t.Errorf("fagi.web = %+v", web)
	}

	for name, tc := range map[string]struct {
		servers map[string]ManagedMCPServerConfig
		want    string
	}{
		"builtin with a url": {map[string]ManagedMCPServerConfig{
			"web": {Kind: "builtin", Billing: "delegated", URL: "https://x.example"},
		}, "no url"},
		"builtin with credential_env": {map[string]ManagedMCPServerConfig{
			"web": {Kind: "builtin", Billing: "delegated", CredentialEnv: "KEY"},
		}, "no url"},
		"price card without capability": {map[string]ManagedMCPServerConfig{
			"exa": {URL: "https://x.example", PriceCard: ToolPriceCardRef{Vendor: "exa", SKU: "s"}},
		}, "capability is missing"},
		"price card without vendor": {map[string]ManagedMCPServerConfig{
			"exa": {URL: "https://x.example", PriceCard: ToolPriceCardRef{Capability: "search", SKU: "s"}},
		}, "vendor is missing"},
		"price card without sku": {map[string]ManagedMCPServerConfig{
			"exa": {URL: "https://x.example", PriceCard: ToolPriceCardRef{Capability: "search", Vendor: "exa"}},
		}, "sku is missing"},
		"reserve server without a card": {map[string]ManagedMCPServerConfig{
			"exa": {URL: "https://x.example"},
		}, "price_card"},
		"delegated server with half a card": {map[string]ManagedMCPServerConfig{
			"web": {Kind: "builtin", Billing: "delegated", PriceCard: ToolPriceCardRef{Capability: "search"}},
		}, "vendor is missing"},
		"remote and builtin sharing an id": {map[string]ManagedMCPServerConfig{
			"web":      {URL: "https://x.example", PriceCard: testPriceCard},
			"fagi.web": {Kind: "builtin", Billing: "delegated"},
		}, "one namespace"},
		"remote without a url": {map[string]ManagedMCPServerConfig{
			"exa": {PriceCard: testPriceCard},
		}, "url"},
		"unknown kind": {map[string]ManagedMCPServerConfig{
			"exa": {Kind: "hosted", URL: "https://x.example", PriceCard: testPriceCard},
		}, "kind"},
		"unknown billing": {map[string]ManagedMCPServerConfig{
			"exa": {Billing: "metered", URL: "https://x.example", PriceCard: testPriceCard},
		}, "billing"},
		"bad id": {map[string]ManagedMCPServerConfig{
			"Exa Search": {URL: "https://x.example", PriceCard: testPriceCard},
		}, "id must match"},
	} {
		err := managedServersConfig(tc.servers).Validate()
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("%s: Validate = %v, want an error containing %q", name, err, tc.want)
		}
	}
	// Built in code with no kind or billing, a server takes the defaults.
	if err := managedServersConfig(map[string]ManagedMCPServerConfig{"exa": {URL: "https://x.example", PriceCard: testPriceCard}}).Validate(); err != nil {
		t.Errorf("a remote reserve server with a card was refused: %v", err)
	}
}

func TestRunToolAndSecurityDefaultsAdditive(t *testing.T) {
	c := DefaultConfig()
	checkDefaults(t, map[string][2]any{
		"budgets.hold.enabled":                        {c.Budgets.Hold.Enabled, true},
		"budgets.hold.unpriced":                       {c.Budgets.Hold.Unpriced, "reserve_default"},
		"budgets.hold.unpriced_reserve_usd":           {c.Budgets.Hold.UnpricedReserveUSD, 0.10},
		"runs.envelopes.max_tree_depth":               {c.Runs.Envelopes.MaxTreeDepth, 8},
		"runs.envelopes.loop_window":                  {c.Runs.Envelopes.LoopWindow, 50},
		"runs.envelopes.loop_threshold":               {c.Runs.Envelopes.LoopThreshold, 5},
		"runs.envelopes.fanout_window_s":              {c.Runs.Envelopes.FanoutWindowS, 60},
		"identity.chain_max_hops":                     {c.Identity.ChainMaxHops, 8},
		"identity.grant_cache_ttl":                    {c.Identity.GrantCacheTTL, 60 * time.Second},
		"mcp.oauth_adapters.refresh_skew":             {c.MCP.OAuthAdapters.RefreshSkew, 120 * time.Second},
		"mcp.oauth_adapters.retry_once_on_401":        {c.MCP.OAuthAdapters.RetryOnceOn401, true},
		"security.admin_listen_addr":                  {c.Security.AdminListenAddr, ""},
		"security.trusted_hosts":                      {c.Security.TrustedHosts, []string{}},
		"security.scrub_response_headers":             {c.Security.ScrubResponseHeaders, true},
		"security.request_shield":                     {c.Security.RequestShield, true},
		"mcp.shaping.default_budget_bytes":            {c.MCP.Shaping.DefaultBudgetBytes, int64(65536)},
		"mcp.shaping.preview_bytes":                   {c.MCP.Shaping.PreviewBytes, int64(2048)},
		"mcp.shaping.spill_ttl":                       {c.MCP.Shaping.SpillTTL, 24 * time.Hour},
		"mcp.shaping.spill_max_bytes":                 {c.MCP.Shaping.SpillMaxBytes, int64(8 << 20)},
		"responses.hollow.mode":                       {c.Responses.Hollow.Mode, "shadow"},
		"responses.hollow.max_hollow_retries":         {c.Responses.Hollow.MaxHollowRetries, 1},
		"responses.hollow.byok":                       {c.Responses.Hollow.BYOK, "flag"},
		"responses.hollow.absorb_cap_usd_per_org_day": {c.Responses.Hollow.AbsorbCapUSDPerOrgDay, 1.00},
		"telemetry.overhead_header":                   {c.Telemetry.OverheadHeader, true},
	})
	if err := c.Validate(); err != nil {
		t.Fatalf("the default config does not validate: %v", err)
	}

	// YAML without these keys parses unchanged and keeps the defaults.
	c, err := loadYAML(t, `
budgets:
  enabled: true
  default_period: monthly
  org: {limit: 100}
routing:
  default_strategy: round-robin
otel:
  enabled: false
`)
	if err != nil {
		t.Fatal(err)
	}
	if !c.Budgets.Enabled || c.Budgets.DefaultPeriod != "monthly" || c.Budgets.Org == nil || c.Budgets.Org.Limit != 100 {
		t.Errorf("existing budget keys changed: %+v", c.Budgets)
	}
	if !c.Budgets.Hold.Enabled || c.Responses.Hollow.Mode != "shadow" || c.Runs.Envelopes.MaxTreeDepth != 8 || !c.Telemetry.OverheadHeader {
		t.Errorf("a file without these keys lost their defaults: hold %+v hollow %+v", c.Budgets.Hold, c.Responses.Hollow)
	}
	c, err = loadYAML(t, "budgets:\n  hold:\n    unpriced: refuse\nresponses:\n  hollow:\n    mode: retry\nsecurity:\n  admin_listen_addr: 127.0.0.1:9091\n  trusted_hosts: [gw.example]\n")
	if err != nil {
		t.Fatal(err)
	}
	if c.Budgets.Hold.Unpriced != "refuse" || !c.Budgets.Hold.Enabled || c.Responses.Hollow.Mode != "retry" || c.Responses.Hollow.BYOK != "flag" ||
		c.Security.AdminListenAddr != "127.0.0.1:9091" || !c.Security.RequestShield {
		t.Errorf("run, tool and security keys from YAML: hold %+v hollow %+v security %+v", c.Budgets.Hold, c.Responses.Hollow, c.Security)
	}

	// Shaping above the transport ceiling is a load error; at it is fine.
	for name, body := range map[string]string{
		"budget above ceiling": "mcp:\n  max_result_bytes: 100000\n  shaping:\n    default_budget_bytes: 100001\n    spill_max_bytes: 100000\n",
		"spill above ceiling":  "mcp:\n  shaping:\n    spill_max_bytes: 8388609\n",
		"hold unpriced":        "budgets:\n  hold:\n    unpriced: allow\n",
		"hollow mode":          "responses:\n  hollow:\n    mode: always\n",
		"hollow byok":          "responses:\n  hollow:\n    byok: absorb\n",
		"admin listen addr":    "security:\n  admin_listen_addr: localhost\n",
		"negative hops":        "identity:\n  chain_max_hops: -1\n",
	} {
		_, err := loadYAML(t, body)
		if err == nil {
			t.Errorf("%s: Load accepted it", name)
		} else if strings.Contains(name, "ceiling") && !strings.Contains(err.Error(), "max_result_bytes") {
			t.Errorf("%s: error %q does not name the ceiling", name, err)
		}
	}
	if _, err := loadYAML(t, "mcp:\n  shaping:\n    spill_max_bytes: 8388608\n    default_budget_bytes: 8388608\n"); err != nil {
		t.Errorf("shaping equal to the ceiling refused: %v", err)
	}
}

func TestStateKeysFromEnvBeforeValidate(t *testing.T) {
	previous := "mcp-previous-state-key-0123456789"
	t.Setenv(EnvMCPStateKeys, " "+testStateKey+" , "+previous+",")
	c, err := loadYAML(t, "mcp:\n  enabled: true\n")
	if err != nil {
		t.Fatalf("%s alone did not satisfy the state-key check: %v", EnvMCPStateKeys, err)
	}
	if !reflect.DeepEqual(c.MCP.StateKeys, []string{testStateKey, previous}) {
		t.Errorf("state keys from env: %d keys, want the current one then the previous one", len(c.MCP.StateKeys))
	}
	// The env replaces YAML keys.
	c, err = loadYAML(t, "mcp:\n  enabled: true\n  state_keys: [\"yaml-state-key-0123456789abcdefghij\"]\n")
	if err != nil {
		t.Fatal(err)
	}
	if len(c.MCP.StateKeys) != 2 || c.MCP.StateKeys[0] != testStateKey {
		t.Error("the env did not replace the YAML state keys")
	}

	unsetEnv(t, EnvMCPStateKeys)
	_, err = loadYAML(t, "mcp:\n  enabled: true\n")
	if err == nil || !strings.Contains(err.Error(), EnvMCPStateKeys) {
		t.Fatalf("mcp.enabled with no key: Load = %v, want a refusal naming %s", err, EnvMCPStateKeys)
	}
	t.Setenv(EnvMCPStateKeys, "too-short")
	_, err = loadYAML(t, "mcp:\n  enabled: true\n")
	if err == nil || !strings.Contains(err.Error(), "at least 32 bytes") || strings.Contains(err.Error(), "too-short") {
		t.Fatalf("a short key: Load = %v, want a length refusal that does not print the key", err)
	}
	// With MCP off, no key is needed.
	unsetEnv(t, EnvMCPStateKeys)
	if _, err := loadYAML(t, "mcp:\n  enabled: false\n"); err != nil {
		t.Errorf("mcp off without a key refused: %v", err)
	}
}

// mcp.StateCodec.Seal refuses ttl <= 0, so mcp.approvals.default_ttl: 0 with
// MCP on is a load error.
func TestMCPApprovalsDefaultTTLMustBePositive(t *testing.T) {
	c := DefaultConfig()
	c.MCP.Enabled = true
	c.MCP.StateKeys = []string{strings.Repeat("k", MinMCPStateKeyBytes)}
	if err := c.Validate(); err != nil {
		t.Fatalf("the default MCP config with a state key: %v", err)
	}
	c.MCP.Approvals.DefaultTTL = 0
	if err := c.Validate(); err == nil || !strings.Contains(err.Error(), "approvals.default_ttl") {
		t.Fatalf("default_ttl 0 with MCP on: %v, want a load error", err)
	}
	c.MCP.Enabled = false
	if err := c.Validate(); err != nil {
		t.Fatalf("default_ttl 0 with MCP off: %v", err)
	}
}
