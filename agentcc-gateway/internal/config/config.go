package config

import (
	"context"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"fmt"
	"log/slog"
	"maps"
	"math"
	"net"
	"net/url"
	"os"
	"path"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// Config is the top-level gateway configuration.
type Config struct {
	Server        ServerConfig              `yaml:"server" json:"server"`
	Providers     map[string]ProviderConfig `yaml:"providers" json:"providers"`
	ModelMap      map[string]string         `yaml:"model_map" json:"model_map"`
	Auth          AuthConfig                `yaml:"auth" json:"auth"`
	LicenseAuth   LicenseAuthConfig         `yaml:"license_auth" json:"license_auth"`
	CostTracking  CostTrackingConfig        `yaml:"cost_tracking" json:"cost_tracking"`
	RateLimiting  RateLimitConfig           `yaml:"rate_limiting" json:"rate_limiting"`
	Cache         CacheConfig               `yaml:"cache" json:"cache"`
	Logging       LoggingConfig             `yaml:"logging" json:"logging"`
	Guardrails    GuardrailsConfig          `yaml:"guardrails" json:"guardrails"`
	Routing       RoutingConfig             `yaml:"routing" json:"routing"`
	RBAC          RBACConfig                `yaml:"rbac" json:"rbac"`
	Budgets       BudgetsConfig             `yaml:"budgets" json:"budgets"`
	Audit         AuditConfig               `yaml:"audit" json:"audit"`
	OTel          OTelConfig                `yaml:"otel" json:"otel"`
	Prometheus    PrometheusConfig          `yaml:"prometheus" json:"prometheus"`
	Alerting      AlertingConfig            `yaml:"alerting" json:"alerting"`
	Admin         AdminConfig               `yaml:"admin" json:"admin"`
	ModelDatabase ModelDatabaseConfig       `yaml:"model_database" json:"model_database"`
	IPACL         IPACLConfig               `yaml:"ip_acl" json:"ip_acl"`
	Secrets       SecretsConfig             `yaml:"secrets" json:"secrets"`
	Privacy       PrivacyConfig             `yaml:"privacy" json:"privacy"`
	ToolPolicy    ToolPolicyConfig          `yaml:"tool_policy" json:"tool_policy"`
	Cluster       ClusterConfig             `yaml:"cluster" json:"cluster"`
	Edge          EdgeConfig                `yaml:"edge" json:"edge"`
	ControlPlane  ControlPlaneConfig        `yaml:"control_plane" json:"control_plane"`
	OrgProviders  OrgProvidersConfig        `yaml:"org_providers" json:"org_providers"`
	CORS          CORSConfig                `yaml:"cors" json:"cors"`
	Assistants    AssistantsConfig          `yaml:"assistants" json:"assistants"`
	MCP           MCPConfig                 `yaml:"mcp" json:"mcp"`
	A2A           A2AConfig                 `yaml:"a2a" json:"a2a"`
	Redis         RedisStateConfig          `yaml:"redis" json:"redis"`
	Managed       ManagedConfig             `yaml:"managed" json:"managed"`
	Catalog       CatalogConfig             `yaml:"catalog" json:"catalog"`
	Generations   GenerationsConfig         `yaml:"generations" json:"generations"`
	Idempotency   IdempotencyConfig         `yaml:"idempotency" json:"idempotency"`
	Capabilities  CapabilitiesConfig        `yaml:"capabilities" json:"capabilities"`
	RuntimeTokens RuntimeTokensConfig       `yaml:"runtime_tokens" json:"runtime_tokens"`
	Tools         ToolsConfig               `yaml:"tools" json:"tools"`
	Runs          RunsConfig                `yaml:"runs" json:"runs"`
	Identity      IdentityConfig            `yaml:"identity" json:"identity"`
	Security      SecurityConfig            `yaml:"security" json:"security"`
	Responses     ResponsesConfig           `yaml:"responses" json:"responses"`
	Telemetry     TelemetryConfig           `yaml:"telemetry" json:"telemetry"`
	Async         AsyncConfig               `yaml:"async" json:"async"`
}

// AsyncConfig bounds async inference (x-agentcc-async: true). Every org's
// jobs share one pool of workers; these limits keep one org from holding
// it. A zero field takes its default.
type AsyncConfig struct {
	// MaxWorkers is how many jobs run at once, across all orgs. Default 10.
	MaxWorkers int `yaml:"max_workers" json:"max_workers"`
	// MaxWorkersPerOrg is how many of them one org's jobs may hold at once.
	// Default half of max_workers (at least 1). A single-tenant gateway can
	// set it to max_workers to let its one org use every worker.
	MaxWorkersPerOrg int `yaml:"max_workers_per_org" json:"max_workers_per_org"`
	// MaxJobTimeout is the longest one job may run. A request's timeout
	// header and its org's config can shorten a job, never lengthen it past
	// this. Default: the longest timeout this config gives a request or a
	// provider (server.default_request_timeout, routing.model_timeouts,
	// providers.*.default_timeout), read at startup.
	MaxJobTimeout time.Duration `yaml:"max_job_timeout" json:"max_job_timeout"`
}

// ManagedConfig governs the providers FutureAGI supplies the credentials for
// (providers with managed: true). An org opts in through its org config; the
// requests those providers serve are billed to the org at upstream cost plus
// MarkupPercent.
type ManagedConfig struct {
	// MarkupPercent is added to the upstream cost of every managed request:
	// 5 bills $1.05 for $1.00 of upstream spend. BYOK requests carry none.
	MarkupPercent float64 `yaml:"markup_percent" json:"markup_percent"`
	// ToolMarkupPercent is the markup on FutureAGI-supplied managed tool
	// calls. -1, the default, inherits MarkupPercent; read it
	// through ToolMarkupBps.
	ToolMarkupPercent float64 `yaml:"tool_markup_percent" json:"tool_markup_percent"`

	// RequireCredits refuses a managed request with 402 unless the org has a
	// positive managed-credit balance, so nobody spends FutureAGI's upstream
	// credit without having paid for it first.
	RequireCredits bool `yaml:"require_credits" json:"require_credits"`

	// CatalogRefresh is how often each managed provider's live model catalog
	// is re-fetched. 0 keeps the snapshot bundled with the gateway.
	CatalogRefresh time.Duration `yaml:"catalog_refresh" json:"catalog_refresh"`

	// RequireRedis makes managed billing fail closed: managed traffic gets
	// 503 managed_billing_unavailable whenever Redis is unavailable.
	// Only tests turn it off, for the in-memory ledger.
	RequireRedis bool `yaml:"require_redis" json:"require_redis"`
	// HoldTTL is how long a credit hold lives before RESERVE sweeps it.
	HoldTTL time.Duration `yaml:"hold_ttl" json:"hold_ttl"`
	// ReserveDefaultOutputTokens is the output a hold assumes when the
	// request sets no max_tokens.
	ReserveDefaultOutputTokens int `yaml:"reserve_default_output_tokens" json:"reserve_default_output_tokens"`
	// UnpricedReserveUSD is held for a managed model with no price.
	UnpricedReserveUSD float64 `yaml:"unpriced_reserve_usd" json:"unpriced_reserve_usd"`
	// MaxOverdraftUSD is how far below zero settlement may take a balance.
	MaxOverdraftUSD float64 `yaml:"max_overdraft_usd" json:"max_overdraft_usd"`
	// SpendGuard is the gateway-wide spend guard; an org's own
	// managed_models.spend_guard overrides it.
	SpendGuard SpendGuardConfig `yaml:"spend_guard" json:"spend_guard"`

	// UpstreamKeyMode is managed.upstream_key_mode: "shared", "per_org" or
	// "per_org_with_shared_fallback".
	UpstreamKeyMode string `yaml:"upstream_key_mode" json:"upstream_key_mode"`
	// UpstreamUserHashKey keys the HMAC of the org id sent upstream as
	// `user` on managed attempts (env AGENTCC_MANAGED_USER_HASH_KEY).
	UpstreamUserHashKey string `yaml:"upstream_user_hash_key" json:"-"`
	// AccountMissingRetryAfter is the Retry-After of the 503 an opted-in org
	// gets while its credit account has not been pushed yet.
	AccountMissingRetryAfter time.Duration `yaml:"account_missing_retry_after" json:"account_missing_retry_after"`
	// ProcessingFeeBps estimates card processing as a share of billed
	// revenue, for landed cost: 2.9% + $0.30 on a $105 top-up
	// is about 3.19%.
	ProcessingFeeBps int `yaml:"processing_fee_bps" json:"processing_fee_bps"`
}

// Managed upstream key modes (ManagedConfig.UpstreamKeyMode).
const (
	UpstreamKeyModeShared                   = "shared"
	UpstreamKeyModePerOrg                   = "per_org"
	UpstreamKeyModePerOrgWithSharedFallback = "per_org_with_shared_fallback"
)

// SpendGuardConfig caps managed spend velocity. A zero cap is unset.
type SpendGuardConfig struct {
	MaxUSDPerMinute   float64 `yaml:"max_usd_per_minute" json:"max_usd_per_minute"`
	MaxUSDPerHour     float64 `yaml:"max_usd_per_hour" json:"max_usd_per_hour"`
	AlertUSDPerHour   float64 `yaml:"alert_usd_per_hour" json:"alert_usd_per_hour"`
	NewOrgDailyCapUSD float64 `yaml:"new_org_daily_cap_usd" json:"new_org_daily_cap_usd"`
	AutoPause         bool    `yaml:"auto_pause" json:"auto_pause"`
}

// CatalogConfig governs the managed catalog: its snapshots, history,
// blocked models and the per-model endpoint refresh.
type CatalogConfig struct {
	// SnapshotDir keeps catalog snapshots across restarts; empty keeps them
	// in memory only.
	SnapshotDir   string   `yaml:"snapshot_dir" json:"snapshot_dir"`
	HistorySize   int      `yaml:"history_size" json:"history_size"`
	KeepSnapshots int      `yaml:"keep_snapshots" json:"keep_snapshots"`
	BlockedModels []string `yaml:"blocked_models" json:"blocked_models"`
	// EndpointsRefresh is how often per-model endpoints are re-fetched, at
	// most EndpointsRPS requests a second.
	EndpointsRefresh time.Duration `yaml:"endpoints_refresh" json:"endpoints_refresh"`
	EndpointsRPS     float64       `yaml:"endpoints_rps" json:"endpoints_rps"`
	// ThoughtSignatureModels are the model globs whose tool history needs a
	// thought signature (tool_history_incompatible). Unset (the default)
	// leaves the engine's rule, every Gemini model of generation 3 or later
	// (eligibility.ThoughtSignatureFromGeneration); a list replaces it.
	ThoughtSignatureModels []string `yaml:"thought_signature_models" json:"thought_signature_models"`
}

// GenerationsConfig is the generations block: ttl, at least 0.
type GenerationsConfig struct {
	TTL time.Duration `yaml:"ttl" json:"ttl"`
}

// IdempotencyConfig is the idempotency block: ttl and max_body_bytes, each at least 0.
type IdempotencyConfig struct {
	TTL          time.Duration `yaml:"ttl" json:"ttl"`
	MaxBodyBytes int           `yaml:"max_body_bytes" json:"max_body_bytes"`
}

// CapabilitiesConfig points at the imported live qualification run that
// /-/capabilities marks endpoints qualified from, and holds the per-capability
// settings of the REST capabilities (capabilities.<cap>, beside
// qualification_file).
type CapabilitiesConfig struct {
	QualificationFile string `yaml:"qualification_file" json:"qualification_file"`
	// PerCapability is every other key of the capabilities block, by
	// capability name (search, fetch, documents.parse...).
	PerCapability map[string]CapabilityConfig `yaml:",inline" json:"per_capability,omitempty"`
}

// CapabilityConfig is one capability's operator settings.
type CapabilityConfig struct {
	// TimeoutMS bounds one call of the capability; 0 takes
	// DefaultCapabilityTimeout.
	TimeoutMS int `yaml:"timeout_ms" json:"timeout_ms"`
	// Defaults are the capability's request defaults; the org's pinned
	// options and the request override them.
	Defaults map[string]interface{} `yaml:"defaults" json:"defaults,omitempty"`
}

// DefaultCapabilityTimeout bounds a capability call whose timeout_ms is
// unset.
const DefaultCapabilityTimeout = 30 * time.Second

// Timeout is the call timeout of capability: its timeout_ms, else
// DefaultCapabilityTimeout.
func (c CapabilitiesConfig) Timeout(capability string) time.Duration {
	if ms := c.PerCapability[capability].TimeoutMS; ms > 0 {
		return time.Duration(ms) * time.Millisecond
	}
	return DefaultCapabilityTimeout
}

// ToolsConfig governs the REST capabilities and their tool price cards.
type ToolsConfig struct {
	Enabled bool `yaml:"enabled" json:"enabled"`
	// CatalogDir is the directory of tool price cards
	// (<capability>/<vendor>.json) the tool catalog loads; empty means the
	// catalog's own default.
	CatalogDir string `yaml:"catalog_dir" json:"catalog_dir"`
	// ReserveDefaultUSD is held for a managed capability call with no price.
	ReserveDefaultUSD float64 `yaml:"reserve_default_usd" json:"reserve_default_usd"`
	// CacheTTLSeconds is how long a cacheable capability result may be
	// reused; 0, the default, reuses none. A card's cacheable terms flag
	// still applies.
	CacheTTLSeconds int `yaml:"cache_ttl_seconds" json:"cache_ttl_seconds"`
}

// RunsConfig is the runs block.
type RunsConfig struct {
	Envelopes RunEnvelopesConfig `yaml:"envelopes" json:"envelopes"`
}

// RunEnvelopesConfig is runs.envelopes: the tree depth, the tool-call loop
// window and threshold, and the fan-out window, each at least 0.
type RunEnvelopesConfig struct {
	MaxTreeDepth  int `yaml:"max_tree_depth" json:"max_tree_depth"`
	LoopWindow    int `yaml:"loop_window" json:"loop_window"`
	LoopThreshold int `yaml:"loop_threshold" json:"loop_threshold"`
	FanoutWindowS int `yaml:"fanout_window_s" json:"fanout_window_s"`
}

// IdentityConfig is the identity block: chain_max_hops and grant_cache_ttl, each at least 0.
type IdentityConfig struct {
	ChainMaxHops  int           `yaml:"chain_max_hops" json:"chain_max_hops"`
	GrantCacheTTL time.Duration `yaml:"grant_cache_ttl" json:"grant_cache_ttl"`
}

// SecurityConfig is the security block.
type SecurityConfig struct {
	// AdminListenAddr is security.admin_listen_addr, a host:port when set.
	AdminListenAddr      string   `yaml:"admin_listen_addr" json:"admin_listen_addr"`
	TrustedHosts         []string `yaml:"trusted_hosts" json:"trusted_hosts"`
	ScrubResponseHeaders bool     `yaml:"scrub_response_headers" json:"scrub_response_headers"`
	RequestShield        bool     `yaml:"request_shield" json:"request_shield"`
}

// ResponsesConfig is the responses block.
type ResponsesConfig struct {
	Hollow HollowConfig `yaml:"hollow" json:"hollow"`
}

// HollowConfig is responses.hollow. Mode defaults to shadow.
type HollowConfig struct {
	Mode                  string  `yaml:"mode" json:"mode"` // off, shadow, retry or fail
	MaxHollowRetries      int     `yaml:"max_hollow_retries" json:"max_hollow_retries"`
	BYOK                  string  `yaml:"byok" json:"byok"` // flag or retry
	AbsorbCapUSDPerOrgDay float64 `yaml:"absorb_cap_usd_per_org_day" json:"absorb_cap_usd_per_org_day"`
}

// TelemetryConfig is the telemetry block.
type TelemetryConfig struct {
	// OverheadHeader is telemetry.overhead_header; on by default.
	OverheadHeader bool `yaml:"overhead_header" json:"overhead_header"`
}

// RuntimeTokensConfig is the runtime_tokens block. With enabled set, Validate
// needs an issuer, an audience, a max_ttl and at least one public key.
type RuntimeTokensConfig struct {
	Enabled    bool              `yaml:"enabled" json:"enabled"`
	Issuer     string            `yaml:"issuer" json:"issuer"`
	Audience   string            `yaml:"audience" json:"audience"`
	PublicKeys []RuntimeTokenKey `yaml:"public_keys" json:"public_keys"`
	MaxTTL     time.Duration     `yaml:"max_ttl" json:"max_ttl"`
	ClockSkew  time.Duration     `yaml:"clock_skew" json:"clock_skew"`
}

// RuntimeTokenKey is one verification key, by its JWT kid.
type RuntimeTokenKey struct {
	Kid       string `yaml:"kid" json:"kid"`
	PublicKey string `yaml:"public_key" json:"public_key"`
}

// MaxRuntimeTokenTTL is the largest runtime_tokens.max_ttl Validate accepts.
const MaxRuntimeTokenTTL = 15 * time.Minute

// RedisStateConfig configures Redis for shared state across replicas.
// When enabled, rate limiting, budget counters, credit balances, and cluster
// heartbeats use Redis as the primary store with automatic in-memory fallback
// via a circuit breaker when Redis is unavailable.
type RedisStateConfig struct {
	Enabled  bool          `yaml:"enabled" json:"enabled"`
	Address  string        `yaml:"address" json:"address"`
	Password string        `yaml:"password" json:"-"`
	DB       int           `yaml:"db" json:"db"`
	PoolSize int           `yaml:"pool_size" json:"pool_size"`
	Timeout  time.Duration `yaml:"timeout" json:"timeout"`
	Prefix   string        `yaml:"prefix" json:"prefix"` // global key prefix, default "agentcc:"
}

// A2AConfig controls the Agent-to-Agent (A2A) protocol support.
type A2AConfig struct {
	Enabled bool                      `yaml:"enabled" json:"enabled"`
	Card    A2ACardConfig             `yaml:"card" json:"card"`
	Agents  map[string]A2AAgentConfig `yaml:"agents" json:"agents"`
}

// A2ACardConfig holds settings for Agentcc's own agent card.
type A2ACardConfig struct {
	Name        string `yaml:"name" json:"name"`
	Description string `yaml:"description" json:"description"`
	Version     string `yaml:"version" json:"version"`
}

// A2AAgentConfig configures a single external A2A agent.
type A2AAgentConfig struct {
	URL         string           `yaml:"url" json:"url"`
	Auth        A2AAgentAuth     `yaml:"auth" json:"auth"`
	Description string           `yaml:"description" json:"description"`
	Skills      []A2ASkillConfig `yaml:"skills" json:"skills"`
}

// A2AAgentAuth holds auth settings for an external A2A agent.
type A2AAgentAuth struct {
	Type   string `yaml:"type" json:"type"` // "bearer", "api_key", "none"
	Token  string `yaml:"token" json:"token"`
	Header string `yaml:"header" json:"header"`
	Key    string `yaml:"key" json:"key"`
}

// A2ASkillConfig defines a skill of an external agent.
type A2ASkillConfig struct {
	ID          string `yaml:"id" json:"id"`
	Name        string `yaml:"name" json:"name"`
	Description string `yaml:"description" json:"description"`
}

// AssistantsConfig controls the Assistants API proxy.
type AssistantsConfig struct {
	Enabled bool `yaml:"enabled" json:"enabled"`
}

// MCPConfig controls the MCP (Model Context Protocol) gateway.
type MCPConfig struct {
	Enabled         bool                         `yaml:"enabled" json:"enabled"`
	Endpoint        string                       `yaml:"endpoint" json:"endpoint"`
	MaxAgentDepth   int                          `yaml:"max_agent_depth" json:"max_agent_depth"`
	ToolCallTimeout time.Duration                `yaml:"tool_call_timeout" json:"tool_call_timeout"`
	SessionTTL      time.Duration                `yaml:"session_ttl" json:"session_ttl"`
	Separator       string                       `yaml:"separator" json:"separator"`
	Servers         map[string]MCPServerConfig   `yaml:"servers" json:"servers"`
	Guardrails      MCPGuardrailConfig           `yaml:"guardrails" json:"guardrails"`
	ToolVersions    map[string]ToolVersionConfig `yaml:"tool_versions" json:"tool_versions"`

	// MaxBodyBytes is mcp.max_body_bytes, a positive byte count.
	MaxBodyBytes int64 `yaml:"max_body_bytes" json:"max_body_bytes"`
	// MaxResultBytes is mcp.max_result_bytes, a positive byte count; mcp.shaping's
	// default_budget_bytes and spill_max_bytes stay at or below it.
	MaxResultBytes int64 `yaml:"max_result_bytes" json:"max_result_bytes"`
	// AllowedOrigins is mcp.allowed_origins, a list of browser Origin values.
	AllowedOrigins []string          `yaml:"allowed_origins" json:"allowed_origins"`
	Protocol       MCPProtocolConfig `yaml:"protocol" json:"protocol"`
	// SessionStore is mcp.session_store: "memory" or "redis" (redis needs Redis
	// configured). Load resolves an empty value to redis when Redis is configured, else memory.
	SessionStore string `yaml:"session_store" json:"session_store"`
	// StateKeys are mcp.state_keys: the current key first, then previous ones, each
	// at least MinMCPStateKeyBytes; mcp.enabled needs one. EnvMCPStateKeys
	// replaces them.
	StateKeys []string `yaml:"state_keys" json:"-"`
	// DevInsecureState lets mcp.enabled load with no state key. Tests only.
	DevInsecureState bool `yaml:"dev_insecure_state" json:"dev_insecure_state"`

	Stdio   MCPStdioConfig   `yaml:"stdio" json:"stdio"`
	Egress  MCPEgressConfig  `yaml:"egress" json:"egress"`
	FairUse MCPFairUseConfig `yaml:"fair_use" json:"fair_use"`
	// ManagedServers are the mcp.managed_servers entries, keyed by id with or
	// without the fagi. prefix.
	ManagedServers map[string]ManagedMCPServerConfig `yaml:"managed_servers" json:"managed_servers"`

	OAuth         MCPOAuthConfig         `yaml:"oauth" json:"oauth"`
	OAuthAdapters MCPOAuthAdaptersConfig `yaml:"oauth_adapters" json:"oauth_adapters"`
	Django        MCPDjangoConfig        `yaml:"django" json:"django"`
	Approvals     MCPApprovalsConfig     `yaml:"approvals" json:"approvals"`
	Metrics       MCPMetricsConfig       `yaml:"metrics" json:"metrics"`
	// AllowAnonymousDev is mcp.allow_anonymous_dev. Tests only.
	AllowAnonymousDev bool               `yaml:"allow_anonymous_dev" json:"allow_anonymous_dev"`
	Static            MCPStaticConfig    `yaml:"static" json:"static"`
	RateLimit         MCPRateLimitConfig `yaml:"ratelimit" json:"ratelimit"`
	// InstanceID is mcp.instance_id, a name for this gateway process.
	InstanceID string `yaml:"instance_id" json:"instance_id"`
	// PublicURL is mcp.public_url, an absolute http(s) URL when set.
	PublicURL string `yaml:"public_url" json:"public_url"`
	// CallbackMaxCalls is mcp.callback_max_calls, at least 0.
	CallbackMaxCalls     int                           `yaml:"callback_max_calls" json:"callback_max_calls"`
	ResponsesPassthrough MCPResponsesPassthroughConfig `yaml:"responses_passthrough" json:"responses_passthrough"`
	Shaping              MCPShapingConfig              `yaml:"shaping" json:"shaping"`
}

// Env vars and limits the MCP keys read.
const (
	// EnvMCPStateKeys holds mcp.state_keys, comma-separated, current first.
	EnvMCPStateKeys = "AGENTCC_MCP_STATE_KEYS"
	// EnvMCPDjangoSecret holds mcp.django.internal_token_secret.
	EnvMCPDjangoSecret = "AGENTCC_MCP_DJANGO_SECRET"
	// EnvMCPEgressPrivateAllow adds entries to mcp.egress.private_allow,
	// comma-separated, after the config file's. The Standalone install sets
	// it for its own loopback backend (deploy/standalone/supervisord.conf):
	// its gateway reads a config file the operator mounts.
	EnvMCPEgressPrivateAllow = "AGENTCC_MCP_EGRESS_PRIVATE_ALLOW"
	// MinMCPStateKeyBytes is the shortest state key accepted.
	MinMCPStateKeyBytes = 32
)

// MCP session stores (MCPConfig.SessionStore).
const (
	MCPSessionStoreMemory = "memory"
	MCPSessionStoreRedis  = "redis"
)

// MCPProtocolVersions are the MCP revisions mcp.protocol.versions and a server's era may name, newest first.
func MCPProtocolVersions() []string {
	return []string{"2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}
}

// MCPProtocolConfig is mcp.protocol: the revisions (at least one when MCP is on) and the legacy_sessions and http_sse flags.
type MCPProtocolConfig struct {
	Versions       []string `yaml:"versions" json:"versions"`
	LegacySessions bool     `yaml:"legacy_sessions" json:"legacy_sessions"`
	HTTPSSE        bool     `yaml:"http_sse" json:"http_sse"`
}

// MCPStdioConfig is mcp.stdio, the stdio settings of operator YAML.
type MCPStdioConfig struct {
	Enabled  bool   `yaml:"enabled" json:"enabled"`
	Launcher string `yaml:"launcher" json:"launcher"` // local or sandbox
	// BaseEnv is mcp.stdio.base_env, a list of gateway variable names; no name may
	// be empty.
	BaseEnv            []string      `yaml:"base_env" json:"base_env"`
	Grace              time.Duration `yaml:"grace" json:"grace"`
	MaxMessageBytes    int64         `yaml:"max_message_bytes" json:"max_message_bytes"`
	StderrMaxLineBytes int           `yaml:"stderr_max_line_bytes" json:"stderr_max_line_bytes"`
}

// MCPEgressConfig is the operator's exception list for the SSRF guard.
type MCPEgressConfig struct {
	// PrivateAllow lists CIDRs or host[:port] that upstream fetches may reach
	// although they are private. Cloud-metadata ranges and hostnames are
	// refused at load even if listed.
	PrivateAllow []string `yaml:"private_allow" json:"private_allow"`
}

// MCPFairUseConfig is mcp.fair_use: requests a minute per org and per key, and
// concurrent calls, each at least 0.
type MCPFairUseConfig struct {
	OrgRPM        int `yaml:"org_rpm" json:"org_rpm"`
	KeyRPM        int `yaml:"key_rpm" json:"key_rpm"`
	MaxConcurrent int `yaml:"max_concurrent" json:"max_concurrent"`
}

// Managed tool server kinds and billing (ManagedMCPServerConfig).
const (
	ManagedServerKindRemote       = "remote"
	ManagedServerKindBuiltin      = "builtin"
	ManagedServerBillingReserve   = "reserve"
	ManagedServerBillingDelegated = "delegated"
)

// ManagedMCPServerConfig is one mcp.managed_servers entry. A remote one names an
// absolute http(s) url and may name credential_env; a builtin one names neither.
// A reserve one names a price_card SKU, which the tool catalog resolves at
// startup; a delegated one may name one.
type ManagedMCPServerConfig struct {
	Kind          string           `yaml:"kind" json:"kind"`       // remote (default) or builtin
	Billing       string           `yaml:"billing" json:"billing"` // reserve (default) or delegated
	URL           string           `yaml:"url" json:"url"`
	Transport     string           `yaml:"transport" json:"transport"` // http or sse
	Era           string           `yaml:"era" json:"era"`
	CredentialEnv string           `yaml:"credential_env" json:"credential_env"` // the variable's name, not its value
	Header        string           `yaml:"header" json:"header"`
	PriceCard     ToolPriceCardRef `yaml:"price_card" json:"price_card"`
	Title         string           `yaml:"title" json:"title"`
	Description   string           `yaml:"description" json:"description"`
}

// ToolPriceCardRef names one SKU of a tool-price-card-v1 card.
type ToolPriceCardRef struct {
	Capability string `yaml:"capability" json:"capability"`
	Vendor     string `yaml:"vendor" json:"vendor"`
	SKU        string `yaml:"sku" json:"sku"`
}

// IsZero reports whether the reference names nothing.
func (r ToolPriceCardRef) IsZero() bool {
	return r.Capability == "" && r.Vendor == "" && r.SKU == ""
}

// MCPOAuthConfig is mcp.oauth: the issuer, the callback issuer and the callback signing key.
type MCPOAuthConfig struct {
	Enabled            bool   `yaml:"enabled" json:"enabled"`
	Issuer             string `yaml:"issuer" json:"issuer"`
	CallbackIssuer     string `yaml:"callback_issuer" json:"callback_issuer"`
	CallbackSigningKey string `yaml:"callback_signing_key" json:"-"`
}

// MCPOAuthAdaptersConfig is mcp.oauth_adapters: the refresh skew and the retry-once-on-401 flag.
type MCPOAuthAdaptersConfig struct {
	RefreshSkew    time.Duration `yaml:"refresh_skew" json:"refresh_skew"`
	RetryOnceOn401 bool          `yaml:"retry_once_on_401" json:"retry_once_on_401"`
}

// MCPDjangoConfig is mcp.django: Django's base URL and the internal token
// secret, which is not the admin token.
type MCPDjangoConfig struct {
	BaseURL             string `yaml:"base_url" json:"base_url"`
	InternalTokenSecret string `yaml:"internal_token_secret" json:"-"`
}

// MCPApprovalsConfig is mcp.approvals: the default TTL, positive when MCP is on.
type MCPApprovalsConfig struct {
	DefaultTTL time.Duration `yaml:"default_ttl" json:"default_ttl"`
}

// MCPMetricsConfig is mcp.metrics.
type MCPMetricsConfig struct {
	// ToolLabels is mcp.metrics.tool_labels; off by default.
	ToolLabels bool `yaml:"tool_labels" json:"tool_labels"`
}

// MCPStaticConfig is mcp.static.
type MCPStaticConfig struct {
	// OrgID is mcp.static.org_id, an org id.
	OrgID string `yaml:"org_id" json:"org_id"`
}

// MCPRateLimitConfig is mcp.ratelimit.
type MCPRateLimitConfig struct {
	// Replicas is mcp.ratelimit.replicas, at least 1.
	Replicas int `yaml:"replicas" json:"replicas"`
}

// MCPResponsesPassthroughConfig is mcp.responses_passthrough: "allow" or "deny"
// for managed and for BYOK requests.
type MCPResponsesPassthroughConfig struct {
	Managed string `yaml:"managed" json:"managed"`
	BYOK    string `yaml:"byok" json:"byok"`
}

// MCPShapingConfig is mcp.shaping: byte budgets and a spill TTL, each at least 0; default_budget_bytes and spill_max_bytes stay at or below MCPConfig.MaxResultBytes.
type MCPShapingConfig struct {
	DefaultBudgetBytes int64         `yaml:"default_budget_bytes" json:"default_budget_bytes"`
	PreviewBytes       int64         `yaml:"preview_bytes" json:"preview_bytes"`
	SpillTTL           time.Duration `yaml:"spill_ttl" json:"spill_ttl"`
	SpillMaxBytes      int64         `yaml:"spill_max_bytes" json:"spill_max_bytes"`
}

// ToolVersionConfig holds version and deprecation info for an MCP tool.
type ToolVersionConfig struct {
	Version            string `yaml:"version" json:"version"`
	Deprecated         bool   `yaml:"deprecated" json:"deprecated"`
	DeprecationMessage string `yaml:"deprecation_message" json:"deprecation_message"`
	ReplacedBy         string `yaml:"replaced_by" json:"replaced_by"`
}

// MCPGuardrailConfig controls security checks on MCP tool calls.
type MCPGuardrailConfig struct {
	Enabled         bool           `yaml:"enabled" json:"enabled"`
	BlockedTools    []string       `yaml:"blocked_tools" json:"blocked_tools"`
	AllowedServers  []string       `yaml:"allowed_servers" json:"allowed_servers"`
	ValidateInputs  bool           `yaml:"validate_inputs" json:"validate_inputs"`
	ValidateOutputs bool           `yaml:"validate_outputs" json:"validate_outputs"`
	CustomPatterns  []string       `yaml:"custom_patterns" json:"custom_patterns"`
	ToolRateLimits  map[string]int `yaml:"tool_rate_limits" json:"tool_rate_limits"`
}

// MCPServerConfig configures a single upstream MCP server.
type MCPServerConfig struct {
	URL           string        `yaml:"url" json:"url"`
	Command       string        `yaml:"command" json:"command"`
	Args          []string      `yaml:"args" json:"args"`
	Transport     string        `yaml:"transport" json:"transport"` // "http" or "stdio"
	Auth          MCPAuthConfig `yaml:"auth" json:"auth"`
	ToolsCacheTTL time.Duration `yaml:"tools_cache_ttl" json:"tools_cache_ttl"`

	// Env is the server's env map. Values may be secrets, so the map is never
	// JSON-encoded; EnvFromHost is its env_from_host list of gateway variable names.
	Env         map[string]string `yaml:"env" json:"-"`
	EnvFromHost []string          `yaml:"env_from_host" json:"env_from_host"`
	// Era is the server's era: empty, "auto" or one of MCPProtocolVersions.
	Era string `yaml:"era" json:"era"`
	// TimeoutMS is the server's timeout_ms, at least 0.
	TimeoutMS          int  `yaml:"timeout_ms" json:"timeout_ms"`
	TrustedAnnotations bool `yaml:"trusted_annotations" json:"trusted_annotations"`
	// ForwardHeaders is the server's forward_headers list; Load refuses a name
	// ForwardHeaderRefused names.
	ForwardHeaders []string `yaml:"forward_headers" json:"forward_headers"`
	// PropagateDepth is the server's propagate_depth flag.
	PropagateDepth bool `yaml:"propagate_depth" json:"propagate_depth"`
}

// MCPAuthConfig holds auth settings for an upstream MCP server.
type MCPAuthConfig struct {
	Type   string `yaml:"type" json:"type"`     // "bearer", "api_key", "none"
	Token  string `yaml:"token" json:"-"`       // for bearer
	Header string `yaml:"header" json:"header"` // for api_key
	Key    string `yaml:"key" json:"-"`         // for api_key
}

// refusedForwardHeaders may never be forwarded to an MCP upstream: inbound
// credentials and the MCP session and version headers, which
// belong to the gateway's own hop.
var refusedForwardHeaders = map[string]bool{
	"authorization":        true,
	"cookie":               true,
	"x-api-key":            true,
	"proxy-authorization":  true,
	"mcp-session-id":       true,
	"mcp-protocol-version": true,
}

// ForwardHeaderRefused reports whether an MCP server may never forward the
// inbound header name upstream: the refused list above, and every x-agentcc-*
// and x-omega-* header. Names match case-insensitively. The org-config check
// applies the same rule.
func ForwardHeaderRefused(name string) bool {
	n := strings.ToLower(strings.TrimSpace(name))
	return refusedForwardHeaders[n] || strings.HasPrefix(n, "x-agentcc-") || strings.HasPrefix(n, "x-omega-")
}

// PrivacyConfig controls message redaction in logs.
type PrivacyConfig struct {
	Enabled  bool                  `yaml:"enabled" json:"enabled"`
	Mode     string                `yaml:"mode" json:"mode"` // "full", "patterns", "none"
	Patterns []RedactPatternConfig `yaml:"redact_patterns" json:"redact_patterns"`
	// DefaultContentMode is how much request content is logged when neither
	// the org, the key nor the request says: "full", "metadata" or "none".
	// It is the least restrictive source.
	DefaultContentMode string `yaml:"default_content_mode" json:"default_content_mode"`
}

// RedactPatternConfig defines a named regex pattern for redaction.
type RedactPatternConfig struct {
	Name    string `yaml:"name" json:"name"`
	Pattern string `yaml:"pattern" json:"pattern"`
}

// SecretsConfig configures external secret manager backends.
type SecretsConfig struct {
	Vault VaultSecretsConfig `yaml:"vault" json:"vault"`
	AWS   AWSSecretsConfig   `yaml:"aws" json:"aws"`
	GCP   GCPSecretsConfig   `yaml:"gcp" json:"gcp"`
	Azure AzureSecretsConfig `yaml:"azure" json:"azure"`
}

// VaultSecretsConfig is the HashiCorp Vault config subset stored in gateway config.
type VaultSecretsConfig struct {
	Address       string `yaml:"address" json:"address"`
	Token         string `yaml:"token" json:"-"`
	AppRoleID     string `yaml:"app_role_id" json:"-"`
	AppRoleSecret string `yaml:"app_role_secret" json:"-"`
}

// AWSSecretsConfig is the AWS Secrets Manager config subset.
type AWSSecretsConfig struct {
	Region          string `yaml:"region" json:"region"`
	AccessKeyID     string `yaml:"access_key_id" json:"-"`
	SecretAccessKey string `yaml:"secret_access_key" json:"-"`
}

// GCPSecretsConfig is the GCP Secret Manager config subset.
type GCPSecretsConfig struct {
	Project string `yaml:"project" json:"project"`
}

// AzureSecretsConfig is the Azure Key Vault config subset.
type AzureSecretsConfig struct {
	VaultURL     string `yaml:"vault_url" json:"vault_url"`
	TenantID     string `yaml:"tenant_id" json:"-"`
	ClientID     string `yaml:"client_id" json:"-"`
	ClientSecret string `yaml:"client_secret" json:"-"`
}

// ModelDatabaseConfig controls the provider model database.
type ModelDatabaseConfig struct {
	ValidationEnabled *bool                          `yaml:"validation_enabled" json:"validation_enabled"` // nil = default true
	Overrides         map[string]ModelOverrideConfig `yaml:"overrides" json:"overrides"`
}

// IsValidationEnabled returns whether model validation is enabled (default true).
func (c *ModelDatabaseConfig) IsValidationEnabled() bool {
	if c.ValidationEnabled == nil {
		return true
	}
	return *c.ValidationEnabled
}

// ModelOverrideConfig allows overriding or adding model entries via config.
type ModelOverrideConfig struct {
	Provider        string                 `yaml:"provider" json:"provider"`
	Mode            string                 `yaml:"mode" json:"mode"`
	MaxInputTokens  *int                   `yaml:"max_input_tokens" json:"max_input_tokens"`
	MaxOutputTokens *int                   `yaml:"max_output_tokens" json:"max_output_tokens"`
	Pricing         *PricingOverrideConfig `yaml:"pricing" json:"pricing"`
	Capabilities    *CapOverrideConfig     `yaml:"capabilities" json:"capabilities"`
	DeprecationDate string                 `yaml:"deprecation_date" json:"deprecation_date"`
	Regions         []string               `yaml:"regions" json:"regions"`
}

// PricingOverrideConfig allows overriding individual pricing fields.
type PricingOverrideConfig struct {
	InputPerToken       *float64 `yaml:"input_per_token" json:"input_per_token"`
	OutputPerToken      *float64 `yaml:"output_per_token" json:"output_per_token"`
	CachedInputPerToken *float64 `yaml:"cached_input_per_token" json:"cached_input_per_token"`
	BatchInputPerToken  *float64 `yaml:"batch_input_per_token" json:"batch_input_per_token"`
	BatchOutputPerToken *float64 `yaml:"batch_output_per_token" json:"batch_output_per_token"`
	InputPerSecond      *float64 `yaml:"input_per_second" json:"input_per_second"`
	InputPerCharacter   *float64 `yaml:"input_per_character" json:"input_per_character"`
}

// CapOverrideConfig allows overriding individual capability flags.
type CapOverrideConfig struct {
	FunctionCalling   *bool `yaml:"function_calling" json:"function_calling"`
	ParallelToolCalls *bool `yaml:"parallel_tool_calls" json:"parallel_tool_calls"`
	Vision            *bool `yaml:"vision" json:"vision"`
	AudioInput        *bool `yaml:"audio_input" json:"audio_input"`
	AudioOutput       *bool `yaml:"audio_output" json:"audio_output"`
	PDFInput          *bool `yaml:"pdf_input" json:"pdf_input"`
	Streaming         *bool `yaml:"streaming" json:"streaming"`
	ResponseSchema    *bool `yaml:"response_schema" json:"response_schema"`
	SystemMessages    *bool `yaml:"system_messages" json:"system_messages"`
	PromptCaching     *bool `yaml:"prompt_caching" json:"prompt_caching"`
	Reasoning         *bool `yaml:"reasoning" json:"reasoning"`
}

// AuthConfig configures virtual API key authentication.
type AuthConfig struct {
	Enabled bool            `yaml:"enabled" json:"enabled"`
	Keys    []AuthKeyConfig `yaml:"keys" json:"keys"`
}

type LicenseAuthConfig struct {
	Enabled              bool                   `yaml:"enabled" json:"enabled"`
	PublicKey            string                 `yaml:"public_key" json:"-"`
	PublicKeys           []LicenseAuthPublicKey `yaml:"public_keys" json:"-"`
	Issuer               string                 `yaml:"issuer" json:"issuer"`
	Audience             string                 `yaml:"audience" json:"audience"`
	TokenType            string                 `yaml:"token_type" json:"token_type"`
	ClockSkewSeconds     int                    `yaml:"clock_skew_seconds" json:"clock_skew_seconds"`
	RuntimeStateRequired bool                   `yaml:"runtime_state_required" json:"runtime_state_required"`
	RateLimitRPM         int                    `yaml:"rate_limit_rpm" json:"rate_limit_rpm"`
	MonthlyUsageLimit    int                    `yaml:"monthly_usage_limit" json:"monthly_usage_limit"`
}

type LicenseAuthPublicKey struct {
	KID       string `yaml:"kid" json:"kid"`
	PublicKey string `yaml:"public_key" json:"-"`
}

// AuthKeyConfig is a single API key definition in config.
type AuthKeyConfig struct {
	Name          string                    `yaml:"name" json:"name"`
	Key           string                    `yaml:"key" json:"-"`
	Owner         string                    `yaml:"owner" json:"owner"`
	KeyType       string                    `yaml:"key_type" json:"key_type"`             // "byok" (default) or "managed"
	CreditBalance float64                   `yaml:"credit_balance" json:"credit_balance"` // initial USD balance for managed keys
	Models        []string                  `yaml:"models" json:"models"`
	Providers     []string                  `yaml:"providers" json:"providers"`
	RateLimitRPM  int                       `yaml:"rate_limit_rpm" json:"rate_limit_rpm"`
	RateLimitTPM  int                       `yaml:"rate_limit_tpm" json:"rate_limit_tpm"`
	Metadata      map[string]string         `yaml:"metadata" json:"metadata"`
	ExpiresAt     string                    `yaml:"expires_at" json:"expires_at"`
	AllowedIPs    []string                  `yaml:"allowed_ips" json:"allowed_ips,omitempty"`
	AllowedTools  []string                  `yaml:"allowed_tools" json:"allowed_tools,omitempty"`
	DeniedTools   []string                  `yaml:"denied_tools" json:"denied_tools,omitempty"`
	Guardrails    *KeyGuardrailPolicyConfig `yaml:"guardrails" json:"guardrails,omitempty"`
	// MCPAllowedTools and MCPDeniedTools are the key's mcp_allowed_tools and mcp_denied_tools lists.
	MCPAllowedTools []string `yaml:"mcp_allowed_tools" json:"mcp_allowed_tools,omitempty"`
	MCPDeniedTools  []string `yaml:"mcp_denied_tools" json:"mcp_denied_tools,omitempty"`
}

// KeyGuardrailPolicyConfig defines per-key guardrail policy overrides.
type KeyGuardrailPolicyConfig struct {
	Overrides []KeyGuardrailOverride `yaml:"overrides" json:"overrides"`
}

// KeyGuardrailOverride overrides a single guardrail's behavior for a key.
type KeyGuardrailOverride struct {
	Name      string   `yaml:"name" json:"name"`
	Disabled  bool     `yaml:"disabled" json:"disabled,omitempty"`
	Action    string   `yaml:"action" json:"action,omitempty"`       // "block", "warn", "log"
	Threshold *float64 `yaml:"threshold" json:"threshold,omitempty"` // pointer so 0 is distinguishable from unset
}

type ServerConfig struct {
	Port                  int           `yaml:"port" json:"port"`
	Host                  string        `yaml:"host" json:"host"`
	ReadHeaderTimeout     time.Duration `yaml:"read_header_timeout" json:"read_header_timeout"`
	ReadTimeout           time.Duration `yaml:"read_timeout" json:"read_timeout"`
	WriteTimeout          time.Duration `yaml:"write_timeout" json:"write_timeout"`
	IdleTimeout           time.Duration `yaml:"idle_timeout" json:"idle_timeout"`
	ShutdownTimeout       time.Duration `yaml:"shutdown_timeout" json:"shutdown_timeout"`
	MaxRequestBodySize    int64         `yaml:"max_request_body_size" json:"max_request_body_size"`
	DefaultRequestTimeout time.Duration `yaml:"default_request_timeout" json:"default_request_timeout"`

	// MaxRequestTimeout is the longest a request may run, whatever a client
	// asks for in x-agentcc-timeout or a per-model timeout states. Unset, it
	// becomes WriteTimeout, which is the bound a request used to get anyway.
	// It may be set above WriteTimeout: the middleware holds the response
	// write deadline at this ceiling, so WriteTimeout no longer decides when
	// a request the gateway resolved a deadline for can still answer. An
	// async job writes no response, so Async.MaxJobTimeout bounds it instead.
	MaxRequestTimeout time.Duration `yaml:"max_request_timeout" json:"max_request_timeout"`

	// TrustedProxies are the addresses or CIDRs of the proxies in front of
	// the gateway (load balancer, ingress). Only a request whose TCP peer is
	// one of them has its X-Forwarded-For read, right to left past every
	// trusted entry. Empty, with TrustedHops 0: every IP rule uses the TCP
	// peer address and ignores X-Forwarded-For and X-Real-IP. Read at start-up.
	TrustedProxies []string `yaml:"trusted_proxies" json:"trusted_proxies"`
	// TrustedHops, instead of TrustedProxies, for proxies whose addresses are
	// not known: the client is the TrustedHops-th X-Forwarded-For entry from
	// the right, i.e. the number of entries the proxies append, the client's
	// included. Only safe when every path to the gateway passes through those
	// proxies. Read at start-up.
	TrustedHops int `yaml:"trusted_hops" json:"trusted_hops"`
}

type ProviderConfig struct {
	BaseURL   string `yaml:"base_url" json:"base_url"`
	APIKey    string `yaml:"api_key" json:"-"`
	APIFormat string `yaml:"api_format" json:"api_format"`

	// APIPathPrefix is the version segment placed between base_url and each
	// endpoint — "/v1" unless the provider says otherwise. Set it to "" for an
	// upstream that serves /chat/completions with no version (Perplexity's
	// Sonar API), or to something else entirely (DeepInfra uses /v1/openai).
	//
	// nil takes the preset's value, or "/v1". A pointer because "" is a
	// meaningful setting distinct from unset. openai-format providers only.
	APIPathPrefix  *string           `yaml:"api_path_prefix" json:"api_path_prefix"`
	DefaultTimeout time.Duration     `yaml:"default_timeout" json:"default_timeout"`
	MaxConcurrent  int               `yaml:"max_concurrent" json:"max_concurrent"`
	ConnPoolSize   int               `yaml:"conn_pool_size" json:"conn_pool_size"`
	Headers        map[string]string `yaml:"headers" json:"headers"`
	Models         []string          `yaml:"models" json:"models"`
	Priority       int               `yaml:"priority" json:"priority"`
	Weight         int               `yaml:"weight" json:"weight"`

	// Managed marks a provider whose credentials FutureAGI supplies. Orgs that
	// opt in to managed models may use it; it is billed with the managed
	// markup. Any other global provider stays reserved for internal keys.
	Managed bool `yaml:"managed" json:"managed"`

	// UpstreamFeeBps is what the upstream adds on top of model prices, for
	// landed cost. Unset means 550 for an openrouter catalog
	// source and 0 otherwise; read it through EffectiveUpstreamFeeBps.
	UpstreamFeeBps *int `yaml:"upstream_fee_bps" json:"upstream_fee_bps"`
	// UpstreamSlug is the provider's name in OpenRouter's upstream naming,
	// which provider.only, ignore and order match on.
	UpstreamSlug string `yaml:"upstream_slug" json:"upstream_slug"`
	// CatalogSource is where the managed catalog for this provider comes
	// from: "openrouter", "openai_compatible" or "price_card".
	CatalogSource string `yaml:"catalog_source" json:"catalog_source"`
	// PriceCardFile prices a price_card source's models.
	PriceCardFile string `yaml:"price_card_file" json:"price_card_file"`
	// ResaleTermsRef names the resale agreement that clears this managed
	// provider for launch. Empty means not cleared; traffic is never blocked
	// by it.
	ResaleTermsRef string `yaml:"resale_terms_ref" json:"resale_terms_ref"`

	// Key rotation.
	Rotation RotationConfig `yaml:"rotation" json:"rotation"`

	// Self-hosted / custom endpoint fields.
	Type         string `yaml:"type" json:"type"`                   // "vllm", "ollama", "lmstudio", "tgi", "custom"
	Local        *bool  `yaml:"local" json:"local"`                 // force local/remote treatment
	SkipTLS      bool   `yaml:"skip_tls" json:"skip_tls"`           // skip TLS verification
	AutoDiscover *bool  `yaml:"auto_discover" json:"auto_discover"` // auto-discover models from /v1/models

	// AWS Bedrock credentials (per-provider, overrides env vars).
	AWSAccessKeyID     string `yaml:"aws_access_key_id" json:"-"`
	AWSSecretAccessKey string `yaml:"aws_secret_access_key" json:"-"`
	AWSRegion          string `yaml:"aws_region" json:"aws_region"`

	// Google service account credentials file (for Vertex AI OAuth2 token generation).
	CredentialsFile    string `yaml:"credentials_file" json:"-"`
	ServiceAccountJSON string `yaml:"service_account_json" json:"-"`
	AWSSessionToken    string `yaml:"aws_session_token" json:"-"`

	// DialContext, when set, opens the provider's upstream connections in
	// place of the default dialer. Set by the gateway, not in config.yaml: org
	// providers use it to check every address they connect to.
	DialContext func(ctx context.Context, network, addr string) (net.Conn, error) `yaml:"-" json:"-"`
}

type LoggingConfig struct {
	Level          string               `yaml:"level" json:"level"`
	Format         string               `yaml:"format" json:"format"` // json, text
	RequestLogging RequestLoggingConfig `yaml:"request_logging" json:"request_logging"`
}

// RequestLoggingConfig controls per-request trace logging.
type RequestLoggingConfig struct {
	Enabled       bool     `yaml:"enabled" json:"enabled"`
	IncludeBodies bool     `yaml:"include_bodies" json:"include_bodies"`
	BufferSize    int      `yaml:"buffer_size" json:"buffer_size"`
	Workers       int      `yaml:"workers" json:"workers"`
	ExcludePaths  []string `yaml:"exclude_paths" json:"exclude_paths"`
}

// CostTrackingConfig controls per-request cost calculation.
type CostTrackingConfig struct {
	Enabled          bool                     `yaml:"enabled" json:"enabled"`
	CustomPricing    map[string]CustomPricing `yaml:"custom_pricing" json:"custom_pricing"`
	AliasCostFactors map[string]float64       `yaml:"alias_cost_factors" json:"alias_cost_factors"`
}

// CustomPricing allows overriding model pricing.
type CustomPricing struct {
	InputPerMTok  float64 `yaml:"input_per_mtok" json:"input_per_mtok"`
	OutputPerMTok float64 `yaml:"output_per_mtok" json:"output_per_mtok"`
}

// RateLimitConfig controls request rate limiting.
type RateLimitConfig struct {
	Enabled   bool `yaml:"enabled" json:"enabled"`
	GlobalRPM int  `yaml:"global_rpm" json:"global_rpm"`
}

// CacheConfig controls response caching.
type CacheConfig struct {
	Enabled    bool                `yaml:"enabled" json:"enabled"`
	Backend    string              `yaml:"backend" json:"backend"` // "memory" (default), "disk", "s3", "azure-blob", "gcs", "redis"
	DefaultTTL time.Duration       `yaml:"default_ttl" json:"default_ttl"`
	MaxEntries int                 `yaml:"max_entries" json:"max_entries"`
	Semantic   SemanticCacheConfig `yaml:"semantic" json:"semantic"`
	Edge       CacheEdgeConfig     `yaml:"edge" json:"edge"`
	Disk       DiskCacheConfig     `yaml:"disk" json:"disk"`
	S3         S3CacheConfig       `yaml:"s3" json:"s3"`
	AzureBlob  AzBlobCacheConfig   `yaml:"azure_blob" json:"azure_blob"`
	GCS        GCSCacheConfig      `yaml:"gcs" json:"gcs"`
	Redis      RedisCacheConfig    `yaml:"redis" json:"redis"`
}

// SemanticCacheConfig controls semantic similarity caching.
type SemanticCacheConfig struct {
	Enabled    bool           `yaml:"enabled" json:"enabled"`
	Backend    string         `yaml:"backend" json:"backend"`     // "memory" (default), "qdrant", "weaviate", "pinecone"
	Threshold  float64        `yaml:"threshold" json:"threshold"` // 0.0-1.0 cosine similarity
	Dimensions int            `yaml:"dimensions" json:"dimensions"`
	MaxEntries int            `yaml:"max_entries" json:"max_entries"`
	Qdrant     QdrantConfig   `yaml:"qdrant" json:"qdrant"`
	Weaviate   WeaviateConfig `yaml:"weaviate" json:"weaviate"`
	Pinecone   PineconeConfig `yaml:"pinecone" json:"pinecone"`
}

// DiskCacheConfig configures the local file-based cache backend.
type DiskCacheConfig struct {
	Directory       string        `yaml:"directory" json:"directory"`
	MaxSizeBytes    int64         `yaml:"max_size_bytes" json:"max_size_bytes"`
	Compress        bool          `yaml:"compress" json:"compress"`
	CleanupInterval time.Duration `yaml:"cleanup_interval" json:"cleanup_interval"`
}

// S3CacheConfig configures the Amazon S3 cache backend.
type S3CacheConfig struct {
	Bucket          string        `yaml:"bucket" json:"bucket"`
	Prefix          string        `yaml:"prefix" json:"prefix"`
	Region          string        `yaml:"region" json:"region"`
	AccessKeyID     string        `yaml:"access_key_id" json:"-"`
	SecretAccessKey string        `yaml:"secret_access_key" json:"-"`
	Timeout         time.Duration `yaml:"timeout" json:"timeout"`
	Compress        bool          `yaml:"compress" json:"compress"`
}

// AzBlobCacheConfig configures the Azure Blob Storage cache backend.
type AzBlobCacheConfig struct {
	Container        string        `yaml:"container" json:"container"`
	Prefix           string        `yaml:"prefix" json:"prefix"`
	ConnectionString string        `yaml:"connection_string" json:"-"`
	SASToken         string        `yaml:"sas_token" json:"-"`
	Timeout          time.Duration `yaml:"timeout" json:"timeout"`
	Compress         bool          `yaml:"compress" json:"compress"`
}

// GCSCacheConfig configures the Google Cloud Storage cache backend.
type GCSCacheConfig struct {
	Bucket          string        `yaml:"bucket" json:"bucket"`
	Prefix          string        `yaml:"prefix" json:"prefix"`
	Project         string        `yaml:"project" json:"project"`
	CredentialsFile string        `yaml:"credentials_file" json:"-"`
	Timeout         time.Duration `yaml:"timeout" json:"timeout"`
	Compress        bool          `yaml:"compress" json:"compress"`
}

// RedisCacheConfig configures the Redis cache backend.
type RedisCacheConfig struct {
	Address   string        `yaml:"address" json:"address"`
	Addresses []string      `yaml:"addresses" json:"addresses"` // for cluster/sentinel
	Password  string        `yaml:"password" json:"-"`
	DB        int           `yaml:"db" json:"db"`
	Mode      string        `yaml:"mode" json:"mode"` // "single", "sentinel", "cluster"
	PoolSize  int           `yaml:"pool_size" json:"pool_size"`
	TLS       bool          `yaml:"tls" json:"tls"`
	KeyPrefix string        `yaml:"key_prefix" json:"key_prefix"`
	Compress  bool          `yaml:"compress" json:"compress"`
	Timeout   time.Duration `yaml:"timeout" json:"timeout"`
}

// QdrantConfig configures the Qdrant vector database backend.
type QdrantConfig struct {
	URL        string        `yaml:"url" json:"url"`
	Collection string        `yaml:"collection" json:"collection"`
	APIKey     string        `yaml:"api_key" json:"-"`
	Timeout    time.Duration `yaml:"timeout" json:"timeout"`
}

// WeaviateConfig configures the Weaviate vector database backend.
type WeaviateConfig struct {
	URL     string        `yaml:"url" json:"url"`
	Class   string        `yaml:"class" json:"class"`
	APIKey  string        `yaml:"api_key" json:"-"`
	Timeout time.Duration `yaml:"timeout" json:"timeout"`
}

// PineconeConfig configures the Pinecone vector database backend.
type PineconeConfig struct {
	URL     string        `yaml:"url" json:"url"`
	APIKey  string        `yaml:"api_key" json:"-"`
	Timeout time.Duration `yaml:"timeout" json:"timeout"`
}

// CacheEdgeConfig configures CDN edge caching.
type CacheEdgeConfig struct {
	Enabled         bool     `yaml:"enabled" json:"enabled"`
	DefaultTTL      int      `yaml:"default_ttl" json:"default_ttl"`           // seconds
	MaxSize         int      `yaml:"max_size" json:"max_size"`                 // max response size in bytes
	CacheableModels []string `yaml:"cacheable_models" json:"cacheable_models"` // empty = all
	RequireOptIn    bool     `yaml:"require_opt_in" json:"require_opt_in"`
}

// GuardrailsConfig controls the guardrail pipeline.
type GuardrailsConfig struct {
	Enabled        bool                     `yaml:"enabled" json:"enabled"`
	FailOpen       bool                     `yaml:"fail_open" json:"fail_open"`
	DefaultTimeout time.Duration            `yaml:"default_timeout" json:"default_timeout"`
	Rules          []GuardrailRuleConfig    `yaml:"rules" json:"rules"`
	Streaming      StreamingGuardrailConfig `yaml:"streaming" json:"streaming"`
}

// StreamingGuardrailConfig controls guardrails on streaming responses.
type StreamingGuardrailConfig struct {
	Enabled       bool   `yaml:"enabled" json:"enabled"`
	CheckInterval int    `yaml:"check_interval" json:"check_interval"` // chars between checks
	FailureAction string `yaml:"failure_action" json:"failure_action"` // "stop" or "disclaimer"
}

// GuardrailRuleConfig defines a single guardrail rule.
type GuardrailRuleConfig struct {
	Name      string                 `yaml:"name" json:"name"`
	Stage     string                 `yaml:"stage" json:"stage"`   // "pre" or "post"
	Mode      string                 `yaml:"mode" json:"mode"`     // "sync" or "async"
	Action    string                 `yaml:"action" json:"action"` // "block", "warn", "log"
	Threshold float64                `yaml:"threshold" json:"threshold"`
	Timeout   time.Duration          `yaml:"timeout" json:"timeout"`
	Config    map[string]interface{} `yaml:"config" json:"config"`
}

// RoutingConfig controls load balancing and multi-provider routing.
type RoutingConfig struct {
	DefaultStrategy   string                           `yaml:"default_strategy" json:"default_strategy"`
	Failover          FailoverConfig                   `yaml:"failover" json:"failover"`
	Retry             RetryConfig                      `yaml:"retry" json:"retry"`
	CircuitBreaker    CircuitBreakerConfig             `yaml:"circuit_breaker" json:"circuit_breaker"`
	ModelFallbacks    map[string][]string              `yaml:"model_fallbacks" json:"model_fallbacks"`
	ModelTimeouts     map[string]time.Duration         `yaml:"model_timeouts" json:"model_timeouts"`
	Mirror            MirrorConfig                     `yaml:"mirror" json:"mirror"`
	ConditionalRoutes []ConditionalRouteConfig         `yaml:"conditional_routes" json:"conditional_routes"`
	Targets           map[string][]RoutingTargetConfig `yaml:"targets" json:"targets"`
	Complexity        ComplexityConfig                 `yaml:"complexity" json:"complexity"`
	Fastest           FastestConfig                    `yaml:"fastest" json:"fastest"`
	Scheduled         ScheduledConfig                  `yaml:"scheduled" json:"scheduled"`
	Adaptive          AdaptiveConfig                   `yaml:"adaptive" json:"adaptive"`
	ProviderLock      ProviderLockConfig               `yaml:"provider_lock" json:"provider_lock"`
	AccessGroups      AccessGroupsConfig               `yaml:"access_groups" json:"access_groups"`
}

// ComplexityConfig controls complexity-based prompt routing.
type ComplexityConfig struct {
	Enabled     bool                            `yaml:"enabled" json:"enabled"`
	DefaultTier string                          `yaml:"default_tier" json:"default_tier"`
	Tiers       map[string]ComplexityTierConfig `yaml:"tiers" json:"tiers"`
	Weights     map[string]float64              `yaml:"weights" json:"weights"`
}

// ComplexityTierConfig defines a complexity tier.
type ComplexityTierConfig struct {
	MaxScore int    `yaml:"max_score" json:"max_score"`
	Model    string `yaml:"model" json:"model"`
	Provider string `yaml:"provider" json:"provider"`
}

// FastestConfig controls fastest-response (race) mode.
type FastestConfig struct {
	MaxConcurrent     int           `yaml:"max_concurrent" json:"max_concurrent"`
	CancelDelay       time.Duration `yaml:"cancel_delay" json:"cancel_delay"`
	ExcludedProviders []string      `yaml:"excluded_providers" json:"excluded_providers"`
}

// ScheduledConfig controls scheduled completions.
type ScheduledConfig struct {
	Enabled          bool          `yaml:"enabled" json:"enabled"`
	MaxPendingJobs   int           `yaml:"max_pending_jobs" json:"max_pending_jobs"`
	ResultTTL        time.Duration `yaml:"result_ttl" json:"result_ttl"`
	MaxScheduleAhead time.Duration `yaml:"max_schedule_ahead" json:"max_schedule_ahead"`
	RetryAttempts    int           `yaml:"retry_attempts" json:"retry_attempts"`
	RetryBackoff     time.Duration `yaml:"retry_backoff" json:"retry_backoff"`
	WorkerCount      int           `yaml:"worker_count" json:"worker_count"`
}

// AdaptiveConfig controls adaptive routing.
type AdaptiveConfig struct {
	Enabled          bool                `yaml:"enabled" json:"enabled"`
	LearningRequests int                 `yaml:"learning_requests" json:"learning_requests"`
	UpdateInterval   time.Duration       `yaml:"update_interval" json:"update_interval"`
	SmoothingFactor  float64             `yaml:"smoothing_factor" json:"smoothing_factor"`
	MinWeight        float64             `yaml:"min_weight" json:"min_weight"`
	SignalWeights    SignalWeightsConfig `yaml:"signal_weights" json:"signal_weights"`
}

// SignalWeightsConfig sets how much each signal contributes to adaptive scoring.
type SignalWeightsConfig struct {
	Latency   float64 `yaml:"latency" json:"latency"`
	ErrorRate float64 `yaml:"error_rate" json:"error_rate"`
	Cost      float64 `yaml:"cost" json:"cost"`
}

// ProviderLockConfig controls provider locking (force specific provider).
type ProviderLockConfig struct {
	Enabled          bool     `yaml:"enabled" json:"enabled"`
	AllowedProviders []string `yaml:"allowed_providers" json:"allowed_providers"`
	DenyProviders    []string `yaml:"deny_providers" json:"deny_providers"`
}

// AccessGroupsConfig maps group names to their definitions.
type AccessGroupsConfig map[string]AccessGroupConfig

// AccessGroupConfig defines a model access group.
type AccessGroupConfig struct {
	Models      []string          `yaml:"models" json:"models"`
	Description string            `yaml:"description" json:"description"`
	Aliases     map[string]string `yaml:"aliases" json:"aliases"`
}

// MirrorConfig controls traffic mirroring for shadow testing.
type MirrorConfig struct {
	Enabled          bool         `yaml:"enabled" json:"enabled"`
	CaptureResults   bool         `yaml:"capture_results" json:"capture_results"`
	MaxStored        int          `yaml:"max_stored" json:"max_stored"`
	FlushIntervalSec int          `yaml:"flush_interval_sec" json:"flush_interval_sec"`
	Rules            []MirrorRule `yaml:"rules" json:"rules"`
}

// MirrorRule defines a single traffic mirroring rule.
type MirrorRule struct {
	SourceModel    string  `yaml:"source_model" json:"source_model"`
	TargetProvider string  `yaml:"target_provider" json:"target_provider"`
	TargetModel    string  `yaml:"target_model" json:"target_model"`
	SampleRate     float64 `yaml:"sample_rate" json:"sample_rate"`
	ExperimentID   string  `yaml:"experiment_id" json:"experiment_id"`
}

// ConditionalRouteConfig defines a rule-based routing override.
type ConditionalRouteConfig struct {
	Name      string            `yaml:"name" json:"name"`
	Priority  int               `yaml:"priority" json:"priority"`
	Condition ConditionConfig   `yaml:"condition" json:"condition"`
	Action    RouteActionConfig `yaml:"action" json:"action"`
}

// ConditionConfig is a recursive condition tree for conditional routing.
type ConditionConfig struct {
	Field string            `yaml:"field" json:"field"`
	Op    string            `yaml:"op" json:"op"`
	Value interface{}       `yaml:"value" json:"value"`
	And   []ConditionConfig `yaml:"$and" json:"$and"`
	Or    []ConditionConfig `yaml:"$or" json:"$or"`
	Not   *ConditionConfig  `yaml:"$not" json:"$not"`
}

// RouteActionConfig defines what happens when a conditional route matches.
type RouteActionConfig struct {
	Provider      string `yaml:"provider" json:"provider"`
	ModelOverride string `yaml:"model_override" json:"model_override"`
}

// FailoverConfig controls automatic failover to alternate providers on error.
type FailoverConfig struct {
	Enabled           bool          `yaml:"enabled" json:"enabled"`
	MaxAttempts       int           `yaml:"max_attempts" json:"max_attempts"`
	OnStatusCodes     []int         `yaml:"on_status_codes" json:"on_status_codes"`
	OnTimeout         bool          `yaml:"on_timeout" json:"on_timeout"`
	PerAttemptTimeout time.Duration `yaml:"per_attempt_timeout" json:"per_attempt_timeout"` // timeout per failover attempt (0 = use parent ctx deadline)
}

// RetryConfig controls per-provider retry with exponential backoff.
type RetryConfig struct {
	Enabled       bool          `yaml:"enabled" json:"enabled"`
	MaxRetries    int           `yaml:"max_retries" json:"max_retries"`
	InitialDelay  time.Duration `yaml:"initial_delay" json:"initial_delay"`
	MaxDelay      time.Duration `yaml:"max_delay" json:"max_delay"`
	Multiplier    float64       `yaml:"multiplier" json:"multiplier"`
	OnStatusCodes []int         `yaml:"on_status_codes" json:"on_status_codes"`
	OnTimeout     bool          `yaml:"on_timeout" json:"on_timeout"`
}

// CircuitBreakerConfig controls per-provider circuit breaking.
type CircuitBreakerConfig struct {
	Enabled          bool          `yaml:"enabled" json:"enabled"`
	FailureThreshold int           `yaml:"failure_threshold" json:"failure_threshold"`
	SuccessThreshold int           `yaml:"success_threshold" json:"success_threshold"`
	Cooldown         time.Duration `yaml:"cooldown" json:"cooldown"`
	OnStatusCodes    []int         `yaml:"on_status_codes" json:"on_status_codes"`
}

// RoutingTargetConfig defines a single routing target for a model.
type RoutingTargetConfig struct {
	Provider      string `yaml:"provider" json:"provider"`
	Weight        int    `yaml:"weight" json:"weight"`
	Priority      int    `yaml:"priority" json:"priority"`
	ModelOverride string `yaml:"model_override" json:"model_override"`
}

// RBACConfig controls role-based access control.
type RBACConfig struct {
	Enabled     bool                      `yaml:"enabled" json:"enabled"`
	DefaultRole string                    `yaml:"default_role" json:"default_role"`
	Roles       map[string]RBACRoleConfig `yaml:"roles" json:"roles"`
	Teams       map[string]RBACTeamConfig `yaml:"teams" json:"teams"`
}

// RBACRoleConfig defines a role's permissions.
type RBACRoleConfig struct {
	Permissions []string `yaml:"permissions" json:"permissions"`
}

// RBACTeamConfig defines team-level RBAC settings.
type RBACTeamConfig struct {
	Role    string                      `yaml:"role" json:"role"`
	Models  []string                    `yaml:"models" json:"models"`
	Members map[string]RBACMemberConfig `yaml:"members" json:"members"`
}

// RBACMemberConfig defines user-level role override within a team.
type RBACMemberConfig struct {
	Role string `yaml:"role" json:"role"`
}

// BudgetsConfig controls hierarchical spend budgets.
type BudgetsConfig struct {
	Enabled       bool                         `yaml:"enabled" json:"enabled"`
	DefaultPeriod string                       `yaml:"default_period" json:"default_period"` // daily|weekly|monthly|total
	WarnThreshold float64                      `yaml:"warn_threshold" json:"warn_threshold"` // 0-1, e.g. 0.8 = 80%
	Org           *BudgetLevelConfig           `yaml:"org" json:"org,omitempty"`
	Teams         map[string]BudgetLevelConfig `yaml:"teams" json:"teams,omitempty"`
	Users         map[string]BudgetLevelConfig `yaml:"users" json:"users,omitempty"`
	Keys          map[string]BudgetLevelConfig `yaml:"keys" json:"keys,omitempty"`
	Tags          map[string]BudgetLevelConfig `yaml:"tags" json:"tags,omitempty"`
	Hold          BudgetHoldConfig             `yaml:"hold" json:"hold"`
}

// Budget hold policies for an unpriced call (BudgetHoldConfig.Unpriced).
const (
	BudgetUnpricedReserveDefault = "reserve_default"
	BudgetUnpricedRefuse         = "refuse"
)

// BudgetHoldConfig is budgets.hold. A budget is a limit, not a charge.
type BudgetHoldConfig struct {
	Enabled bool `yaml:"enabled" json:"enabled"`
	// Unpriced is budgets.hold.unpriced: reserve_default or refuse.
	Unpriced           string  `yaml:"unpriced" json:"unpriced"`
	UnpricedReserveUSD float64 `yaml:"unpriced_reserve_usd" json:"unpriced_reserve_usd"`
}

// BudgetLevelConfig defines a single budget at any level.
type BudgetLevelConfig struct {
	Limit    float64            `yaml:"limit" json:"limit"`
	Period   string             `yaml:"period" json:"period,omitempty"` // overrides default_period
	Hard     *bool              `yaml:"hard" json:"hard,omitempty"`     // nil = default true
	PerModel map[string]float64 `yaml:"per_model" json:"per_model,omitempty"`
}

// AuditConfig controls audit logging.
type AuditConfig struct {
	Enabled     bool              `yaml:"enabled" json:"enabled"`
	BufferSize  int               `yaml:"buffer_size" json:"buffer_size"`
	MinSeverity string            `yaml:"min_severity" json:"min_severity"` // info|warn|error|critical
	Categories  []string          `yaml:"categories" json:"categories"`     // empty = all
	Sinks       []AuditSinkConfig `yaml:"sinks" json:"sinks"`
}

// AuditSinkConfig defines an audit log sink.
type AuditSinkConfig struct {
	Type    string            `yaml:"type" json:"type"` // stdout|file|webhook
	Path    string            `yaml:"path" json:"path,omitempty"`
	URL     string            `yaml:"url" json:"url,omitempty"`
	Headers map[string]string `yaml:"headers" json:"headers,omitempty"`
}

// OTelConfig controls OpenTelemetry trace and metric export.
type OTelConfig struct {
	Enabled     bool              `yaml:"enabled" json:"enabled"`
	ServiceName string            `yaml:"service_name" json:"service_name"`
	Exporter    string            `yaml:"exporter" json:"exporter"` // "stdout" | "otlp"
	SampleRate  float64           `yaml:"sample_rate" json:"sample_rate"`
	Attributes  map[string]string `yaml:"attributes" json:"attributes"`

	// Endpoint is the OTLP collector base URL, e.g. "http://otel-collector:4318".
	// Required when Exporter is "otlp". "/v1/traces" is appended when the URL
	// carries no path of its own.
	Endpoint string `yaml:"endpoint" json:"endpoint"`

	// Protocol selects the OTLP transport. Only "http/protobuf" is supported;
	// empty means "http/protobuf".
	Protocol string `yaml:"protocol" json:"protocol"`

	// Headers are sent on every OTLP request. Hosted collectors authenticate
	// this way, so write credentials as ${VAR} and keep them in the
	// environment rather than in this file.
	Headers map[string]string `yaml:"headers" json:"-"`

	// TracePropagation makes the gateway honour an inbound W3C `traceparent`
	// header: its trace id is adopted and its span id becomes this span's
	// parent, so the gateway span nests under the caller's span instead of
	// standing alone as a second root.
	//
	// Off by default, and deliberately so. A trace whose root span lives in a
	// different backend has no root here, and consumers that key a trace off
	// its root span will not list it. Enable when the caller exports its spans
	// to the same destination as this gateway.
	TracePropagation bool `yaml:"trace_propagation" json:"trace_propagation"`

	// IncludeBodies attaches the prompt and completion to each span. Off by
	// default: it sends user content to the collector, and it is a separate
	// decision from logging.request_logging.include_bodies because the two
	// go to different places and are signed off separately. Content is
	// redacted with the org's privacy config exactly as the request log is.
	IncludeBodies bool `yaml:"include_bodies" json:"include_bodies"`

	// MetadataAttributes additionally emits every caller-supplied metadata key
	// as its own `agentcc.metadata.<key>` span attribute, next to the `metadata`
	// JSON object that is always written. Flat attributes are what a trace
	// backend can filter and group on; the JSON object is one opaque string.
	// nil = default true.
	MetadataAttributes *bool `yaml:"metadata_attributes" json:"metadata_attributes"`

	// CaptureHeaders lists request headers to copy onto the span as
	// `http.request.header.<name>`. Names are matched case-insensitively and a
	// trailing `*` is a prefix wildcard, so "x-acme-*" takes a whole namespace.
	//
	// Empty by default, and an allowlist rather than a blocklist on purpose.
	// Request headers carry credentials — the gateway itself copies x-api-key
	// into Authorization before a span exists — and a trace goes somewhere
	// wider than a log does. A blocklist is a list you can only be wrong about
	// once. Credential headers stay denied even when a wildcard matches them.
	CaptureHeaders []string `yaml:"capture_headers" json:"capture_headers"`

	// PropagateForAgents is otel.propagate_for_agents; on by default.
	PropagateForAgents bool `yaml:"propagate_for_agents" json:"propagate_for_agents"`

	// BodyAttributes emits the request body's unknown top-level fields as
	// `agentcc.body.<key>` — an SDK's extra_body, and every OpenAI parameter
	// newer than the field list the request struct knows about
	// (reasoning_effort, parallel_tool_calls, store...). On by default: those
	// are ordinary request knobs, and a span that cannot be filtered by them is
	// missing something the caller assumes is recorded.
	//
	// Scalars only, so a nested object — the shape credentials arrive in when
	// someone passes service-account JSON through extra_body — is never
	// exported. Values are redacted with the privacy config. nil = default true.
	BodyAttributes *bool `yaml:"body_attributes" json:"body_attributes"`
}

// PrometheusConfig controls the Prometheus metrics endpoint.
type PrometheusConfig struct {
	Enabled bool   `yaml:"enabled" json:"enabled"`
	Path    string `yaml:"path" json:"path"` // default: "/-/metrics"
}

// AlertingConfig controls threshold-based alerting.
type AlertingConfig struct {
	Enabled  bool                 `yaml:"enabled" json:"enabled"`
	Rules    []AlertRuleConfig    `yaml:"rules" json:"rules"`
	Channels []AlertChannelConfig `yaml:"channels" json:"channels"`
}

// AlertRuleConfig defines a single alerting rule.
type AlertRuleConfig struct {
	Name      string        `yaml:"name" json:"name"`
	Metric    string        `yaml:"metric" json:"metric"`       // error_count, request_count, cost_total, latency_avg, tokens_total
	Condition string        `yaml:"condition" json:"condition"` // >=, >, <=, <, ==
	Threshold float64       `yaml:"threshold" json:"threshold"`
	Window    time.Duration `yaml:"window" json:"window"`
	Cooldown  time.Duration `yaml:"cooldown" json:"cooldown"`
	Channels  []string      `yaml:"channels" json:"channels"`
}

// AlertChannelConfig defines an alert delivery channel.
type AlertChannelConfig struct {
	Name    string            `yaml:"name" json:"name"`
	Type    string            `yaml:"type" json:"type"` // webhook, slack, log
	URL     string            `yaml:"url" json:"url,omitempty"`
	Headers map[string]string `yaml:"headers" json:"headers,omitempty"`
}

type AdminConfig struct {
	Token string `yaml:"token" json:"-"`
	// AllowInsecureToken lets the gateway start with an empty, short, default
	// or placeholder admin token. Set only from AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN,
	// never from a config file. Local development only.
	AllowInsecureToken bool `yaml:"-" json:"-"`
	// tokenSet records that admin auth was configured even if the token is
	// empty: the file has an admin.token key, or AGENTCC_ADMIN_TOKEN is present.
	tokenSet bool
}

// RotationConfig controls key rotation for a provider.
type RotationConfig struct {
	Enabled     bool          `yaml:"enabled" json:"enabled"`
	DrainPeriod time.Duration `yaml:"drain_period" json:"drain_period"` // time to keep old key active (default 30s)
}

// ToolPolicyConfig controls tool/function filtering in requests.
type ToolPolicyConfig struct {
	Enabled            bool     `yaml:"enabled" json:"enabled"`
	MaxToolsPerRequest int      `yaml:"max_tools_per_request" json:"max_tools_per_request"`
	DefaultAction      string   `yaml:"default_action" json:"default_action"` // "strip" or "reject"
	Allow              []string `yaml:"allow" json:"allow"`
	Deny               []string `yaml:"deny" json:"deny"`
}

// ClusterConfig controls multi-instance HA mode.
type ClusterConfig struct {
	Enabled           bool          `yaml:"enabled" json:"enabled"`
	NodeID            string        `yaml:"node_id" json:"node_id"`
	RedisURL          string        `yaml:"redis_url" json:"-"`
	HeartbeatInterval time.Duration `yaml:"heartbeat_interval" json:"heartbeat_interval"`
	HeartbeatTTL      time.Duration `yaml:"heartbeat_ttl" json:"heartbeat_ttl"`
	DrainTimeout      time.Duration `yaml:"drain_timeout" json:"drain_timeout"`
}

// EdgeConfig controls edge proxy generation.
type EdgeConfig struct {
	Enabled bool               `yaml:"enabled" json:"enabled"`
	Regions []EdgeRegionConfig `yaml:"regions" json:"regions"`
	Cache   EdgeCacheConfig    `yaml:"cache" json:"cache"`
}

// ControlPlaneConfig configures the connection to the Django control plane.
type ControlPlaneConfig struct {
	URL           string        `yaml:"url" json:"url"`                         // e.g. "http://localhost:8000"
	AdminToken    string        `yaml:"admin_token" json:"-"`                   // shared secret for gateway ↔ Django auth
	WebhookSecret string        `yaml:"webhook_secret" json:"-"`                // shared secret sent as X-Webhook-Secret header
	SyncOnStartup bool          `yaml:"sync_on_startup" json:"sync_on_startup"` // pull all org configs on boot
	SyncInterval  time.Duration `yaml:"sync_interval" json:"sync_interval"`     // periodic re-sync (0 = disabled)

	// KeySyncGrace is how long after a key is created through the admin API
	// (POST /-/keys) a key sync whose list lacks it still keeps it. The app
	// creates a key on the gateway first and stores its own row when its
	// request ends, about a second later, so a sync whose list was fetched in
	// between misses the new key. The window only has to cover that store
	// latency; it does not depend on SyncInterval. Raise it only if the app's
	// key-create request can take longer than this after the gateway answers.
	// A longer window also keeps a key the app deleted before any sync listed
	// it for longer. 0 means DefaultKeySyncGrace.
	KeySyncGrace time.Duration `yaml:"key_sync_grace" json:"key_sync_grace"`
}

// SyncsKeys reports whether the gateway loads its keys from the control
// plane: a URL, and a sync on start or on an interval (cmd/agentcc runs
// them). With a URL and neither, the control plane only takes the request
// logs, and keys reach the gateway only through the admin API.
func (c ControlPlaneConfig) SyncsKeys() bool {
	return c.URL != "" && (c.SyncOnStartup || c.SyncInterval > 0)
}

// DefaultKeySyncGrace is the key sync grace window when none is configured:
// many times the app's usual store latency, short enough that a key deleted in
// the app right after it was created does not outlive it by much.
const DefaultKeySyncGrace = 30 * time.Second

// EffectiveKeySyncGrace returns KeySyncGrace when set, else
// DefaultKeySyncGrace.
func (c ControlPlaneConfig) EffectiveKeySyncGrace() time.Duration {
	if c.KeySyncGrace > 0 {
		return c.KeySyncGrace
	}
	return DefaultKeySyncGrace
}

// EnvAllowPrivateProviderURLs names the env var behind
// OrgProvidersConfig.AllowPrivateURLs. The Django backend reads the same name
// for model discovery and saving providers, so one setting covers both.
const EnvAllowPrivateProviderURLs = "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS"

// OrgProvidersConfig governs the providers orgs configure through the control
// plane (not the ones in this file's providers section, which are trusted).
type OrgProvidersConfig struct {
	// AllowPrivateURLs lets an org provider's base_url point at private and
	// LAN addresses (RFC 1918, 100.64.0.0/10, fc00::/7, and host names such
	// as Docker service names that resolve to them): a local Ollama or vLLM.
	// Off by default, since on a shared gateway it lets any org reach
	// internal hosts. Loopback, link-local, cloud metadata, multicast and
	// unspecified addresses stay refused regardless.
	AllowPrivateURLs bool `yaml:"allow_private_urls" json:"allow_private_urls"`
}

// EdgeRegionConfig defines a backend region for edge routing.
type EdgeRegionConfig struct {
	Name    string `yaml:"name" json:"name"`
	Backend string `yaml:"backend" json:"backend"`
	Weight  int    `yaml:"weight" json:"weight"`
}

// EdgeCacheConfig controls edge-level caching.
type EdgeCacheConfig struct {
	Enabled    bool `yaml:"enabled" json:"enabled"`
	DefaultTTL int  `yaml:"default_ttl" json:"default_ttl"` // seconds
	MaxSize    int  `yaml:"max_size" json:"max_size"`       // max cached response size in bytes
}

// CORSConfig controls Cross-Origin Resource Sharing headers.
type CORSConfig struct {
	Enabled          bool     `yaml:"enabled" json:"enabled"`
	AllowedOrigins   []string `yaml:"allowed_origins" json:"allowed_origins"` // e.g. ["https://app.example.com", "*"]
	AllowedMethods   []string `yaml:"allowed_methods" json:"allowed_methods"` // e.g. ["GET", "POST", "OPTIONS"]
	AllowedHeaders   []string `yaml:"allowed_headers" json:"allowed_headers"` // e.g. ["Authorization", "Content-Type"]
	ExposedHeaders   []string `yaml:"exposed_headers" json:"exposed_headers"` // headers the browser can read
	MaxAge           int      `yaml:"max_age" json:"max_age"`                 // preflight cache seconds (default 86400)
	AllowCredentials bool     `yaml:"allow_credentials" json:"allow_credentials"`
}

// IPACLConfig controls IP-based access control lists.
type IPACLConfig struct {
	Enabled        bool     `yaml:"enabled" json:"enabled"`
	TrustedProxies int      `yaml:"trusted_proxies" json:"trusted_proxies"` // not read; server.trusted_proxies or server.trusted_hops set the client address (a start-up warning names them when this is set)
	Allow          []string `yaml:"allow" json:"allow"`                     // allowed IPs/CIDRs (empty = all allowed)
	Deny           []string `yaml:"deny" json:"deny"`                       // denied IPs/CIDRs (takes precedence over allow)
}

// DefaultConfig returns sensible defaults.
func DefaultConfig() *Config {
	return &Config{
		Server: ServerConfig{
			Port:                  8080,
			Host:                  "0.0.0.0",
			ReadHeaderTimeout:     5 * time.Second,
			ReadTimeout:           60 * time.Second,
			WriteTimeout:          300 * time.Second,
			IdleTimeout:           120 * time.Second,
			ShutdownTimeout:       30 * time.Second,
			MaxRequestBodySize:    50 * 1024 * 1024, // 50MB
			DefaultRequestTimeout: 60 * time.Second,
			// Unset: Load reads it as WriteTimeout, whatever that is set to.
			MaxRequestTimeout: 0,
		},
		Providers: make(map[string]ProviderConfig),
		ModelMap:  make(map[string]string),
		LicenseAuth: LicenseAuthConfig{
			Issuer:               "https://licenses.futureagi.com",
			Audience:             "futureagi-agentcc-gateway",
			TokenType:            "futureagi-managed-service-token",
			ClockSkewSeconds:     300,
			RuntimeStateRequired: true,
		},
		CostTracking: CostTrackingConfig{Enabled: true},
		Managed: ManagedConfig{
			MarkupPercent:              5,
			ToolMarkupPercent:          -1,
			RequireCredits:             true,
			CatalogRefresh:             6 * time.Hour,
			RequireRedis:               true,
			HoldTTL:                    15 * time.Minute,
			ReserveDefaultOutputTokens: 4096,
			UnpricedReserveUSD:         0.10,
			UpstreamKeyMode:            UpstreamKeyModeShared,
			AccountMissingRetryAfter:   30 * time.Second,
			ProcessingFeeBps:           320,
		},
		Catalog: CatalogConfig{
			HistorySize:      50,
			KeepSnapshots:    10,
			BlockedModels:    []string{},
			EndpointsRefresh: 6 * time.Hour,
			EndpointsRPS:     5,
			// ThoughtSignatureModels is unset (nil) by default, which leaves
			// the eligibility engine's own rule: every Gemini model of
			// generation 3 or later.
		},
		Generations: GenerationsConfig{TTL: 72 * time.Hour},
		Idempotency: IdempotencyConfig{TTL: 24 * time.Hour, MaxBodyBytes: 1 << 20},
		RuntimeTokens: RuntimeTokensConfig{
			Audience:  "futureagi-agentcc-gateway",
			MaxTTL:    MaxRuntimeTokenTTL,
			ClockSkew: 60 * time.Second,
		},
		Privacy:      PrivacyConfig{DefaultContentMode: "full"},
		OTel:         OTelConfig{PropagateForAgents: true},
		RateLimiting: RateLimitConfig{Enabled: true},
		Cache: CacheConfig{
			Enabled:    false,
			DefaultTTL: 5 * time.Minute,
			MaxEntries: 10000,
		},
		Guardrails: GuardrailsConfig{
			Enabled:        false,
			FailOpen:       true,
			DefaultTimeout: 5 * time.Second,
		},
		Logging: LoggingConfig{
			Level:  "info",
			Format: "json",
			RequestLogging: RequestLoggingConfig{
				Enabled:    true,
				BufferSize: 4096,
				Workers:    2,
			},
		},
		Assistants: AssistantsConfig{
			Enabled: true,
		},
		MCP: MCPConfig{
			Enabled:         false,
			Endpoint:        "/mcp",
			MaxAgentDepth:   10,
			ToolCallTimeout: 30 * time.Second,
			SessionTTL:      30 * time.Minute,
			Separator:       "_",
			MaxBodyBytes:    4 << 20,
			MaxResultBytes:  8 << 20,
			Protocol: MCPProtocolConfig{
				Versions:       MCPProtocolVersions(),
				LegacySessions: true,
				HTTPSSE:        true,
			},
			Stdio: MCPStdioConfig{
				Enabled:            true,
				Launcher:           "local",
				BaseEnv:            []string{"PATH", "HOME", "LANG", "TMPDIR"},
				Grace:              5 * time.Second,
				MaxMessageBytes:    16 << 20,
				StderrMaxLineBytes: 1024,
			},
			FairUse:              MCPFairUseConfig{OrgRPM: 600, KeyRPM: 300, MaxConcurrent: 32},
			OAuthAdapters:        MCPOAuthAdaptersConfig{RefreshSkew: 120 * time.Second, RetryOnceOn401: true},
			Approvals:            MCPApprovalsConfig{DefaultTTL: 15 * time.Minute},
			RateLimit:            MCPRateLimitConfig{Replicas: 1},
			CallbackMaxCalls:     20,
			ResponsesPassthrough: MCPResponsesPassthroughConfig{Managed: "deny", BYOK: "allow"},
			Shaping: MCPShapingConfig{
				DefaultBudgetBytes: 65536,
				PreviewBytes:       2048,
				SpillTTL:           24 * time.Hour,
				SpillMaxBytes:      8 << 20,
			},
		},
		Budgets: BudgetsConfig{
			Hold: BudgetHoldConfig{Enabled: true, Unpriced: BudgetUnpricedReserveDefault, UnpricedReserveUSD: 0.10},
		},
		Tools: ToolsConfig{ReserveDefaultUSD: 0.10},
		Runs: RunsConfig{
			Envelopes: RunEnvelopesConfig{MaxTreeDepth: 8, LoopWindow: 50, LoopThreshold: 5, FanoutWindowS: 60},
		},
		Identity: IdentityConfig{ChainMaxHops: 8, GrantCacheTTL: 60 * time.Second},
		Security: SecurityConfig{TrustedHosts: []string{}, ScrubResponseHeaders: true, RequestShield: true},
		Responses: ResponsesConfig{
			Hollow: HollowConfig{Mode: "shadow", MaxHollowRetries: 1, BYOK: "flag", AbsorbCapUSDPerOrgDay: 1.00},
		},
		Telemetry: TelemetryConfig{OverheadHeader: true},
		A2A: A2AConfig{
			Enabled: false,
			Card: A2ACardConfig{
				Name:        "Agentcc LLM Gateway",
				Description: "Route to any LLM with guardrails, caching, and observability",
				Version:     "1.0.0",
			},
		},
	}
}

// Load reads config from a YAML/JSON file and overrides with env vars.
func Load(path string) (*Config, error) {
	cfg := DefaultConfig()

	if path != "" {
		if err := loadFromFile(cfg, path); err != nil {
			return nil, fmt.Errorf("loading config file: %w", err)
		}
	}

	loadFromEnv(cfg)

	// The control plane checks the gateway's calls against the same shared
	// admin token (Django's AGENTCC_ADMIN_TOKEN), so a gateway told only the
	// one token can still sync keys and org configs.
	if cfg.ControlPlane.URL != "" && cfg.ControlPlane.AdminToken == "" {
		cfg.ControlPlane.AdminToken = cfg.Admin.Token
	}

	cfg.resolveDependentDefaults()

	// Unset, the ceiling is write_timeout, the bound a request got anyway. A
	// ceiling above it is kept: middleware.Timeout holds the response write
	// deadline at the ceiling, so write_timeout is a default for a client that
	// reads slowly, not a limit on how long a request may take.
	if cfg.Server.MaxRequestTimeout <= 0 {
		cfg.Server.MaxRequestTimeout = cfg.Server.WriteTimeout
	}

	if err := cfg.Validate(); err != nil {
		return nil, fmt.Errorf("config validation: %w", err)
	}

	return cfg, nil
}

func loadFromFile(cfg *Config, path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	warnUnreadKeys(data)
	// Expand ${VAR} and $VAR references from environment variables.
	expanded := os.ExpandEnv(string(data))
	return yaml.Unmarshal([]byte(expanded), cfg)
}

// warnUnreadKeys logs the keys in a config file that no field reads, which
// the decoder otherwise drops in silence. A typo makes one: in a flow mapping,
// "input_per_token:1e-07" with no space is a key, and the price stays unset.
// Deployed configs still carry keys the gateway no longer reads, so this warns
// and does not fail.
func warnUnreadKeys(data []byte) {
	// $NAME stands in for each ${NAME}, not its value: a mistyped key then
	// cannot carry a secret into the log, and no brace breaks a flow mapping.
	// The type errors the stand-ins cause are not unread keys; they are left out.
	text := os.Expand(string(data), func(name string) string { return "$" + name })
	dec := yaml.NewDecoder(strings.NewReader(text))
	dec.KnownFields(true)
	var typeErr *yaml.TypeError
	if !errors.As(dec.Decode(DefaultConfig()), &typeErr) {
		return
	}
	var unread []string
	for _, e := range typeErr.Errors {
		if strings.Contains(e, "not found in type") {
			unread = append(unread, withoutKeyValue(e))
		}
	}
	if len(unread) > 0 {
		slog.Warn("config: keys the gateway does not read were ignored", "keys", unread)
	}
}

// withoutKeyValue removes a literal value that a missing space pulled into a
// key name.
//
// The typo this warning exists to catch is what makes one. In a flow mapping,
// {api_key:sk-live-...} is a single key named "api_key:sk-live-...", and the
// decoder repeats it whole; the same holds for {token:...}. So the error text
// is exactly where a literal credential turns up, and the warning would
// publish it to every log that reads the gateway's output.
//
// Every colon-attached value is dropped, including one that begins with a
// ${VAR} stand-in. Keeping those looked safe — a stand-in names a variable
// rather than its contents — but a reference and a literal compose:
// {token:${EMPTY_VAR}sk-live-...} expands to a key starting with "$" whose
// remainder is the secret in full. Deciding which bytes after a colon are a
// variable name and which are a credential is not a judgement a startup
// diagnostic should be making, and the key name with its line number already
// finds the typo.
func withoutKeyValue(e string) string {
	const prefix, suffix = "field ", " not found in type "
	at := strings.Index(e, prefix)
	if at < 0 {
		return e
	}
	name, rest, found := strings.Cut(e[at+len(prefix):], suffix)
	if !found {
		return e
	}
	key, _, split := strings.Cut(name, ":")
	if !split {
		return e
	}
	return e[:at+len(prefix)] + key + ":<value omitted>" + suffix + rest
}

// authKeyConfigured reports whether an auth key with the given raw value is
// already present (e.g. loaded from config.yaml), so env seeding doesn't
// clobber an operator's explicit entry.
func authKeyConfigured(keys []AuthKeyConfig, raw string) bool {
	for i := range keys {
		if keys[i].Key == raw {
			return true
		}
	}
	return false
}

// envDuration applies a duration environment variable to dst. A value that
// does not parse keeps the configured one and says so: a bare number is the
// usual mistake, and silence reads as "the environment does not override the
// file".
func envDuration(name string, dst *time.Duration) {
	v := os.Getenv(name)
	if v == "" {
		return
	}
	d, err := time.ParseDuration(v)
	if err != nil {
		slog.Warn("ignoring environment variable: not a duration such as 60s or 3m",
			"env", name, "value", v)
		return
	}
	*dst = d
}

// envInt is envDuration for a plain integer.
func envInt(name string, dst *int) {
	v := os.Getenv(name)
	if v == "" {
		return
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		slog.Warn("ignoring environment variable: not a whole number",
			"env", name, "value", v)
		return
	}
	*dst = n
}

func loadFromEnv(cfg *Config) {
	envInt("AGENTCC_PORT", &cfg.Server.Port)
	if v := os.Getenv("AGENTCC_HOST"); v != "" {
		cfg.Server.Host = v
	}
	if v := os.Getenv("AGENTCC_LOG_LEVEL"); v != "" {
		cfg.Logging.Level = v
	}
	if v, ok := os.LookupEnv("AGENTCC_ADMIN_TOKEN"); ok {
		cfg.Admin.tokenSet = true
		if v != "" {
			cfg.Admin.Token = v
		}
	}
	if v := os.Getenv(EnvAllowInsecureAdminToken); v != "" {
		if allow, err := strconv.ParseBool(strings.ToLower(strings.TrimSpace(v))); err == nil {
			cfg.Admin.AllowInsecureToken = allow
		} else {
			slog.Warn("ignoring unrecognised value; expected true or false",
				"env", EnvAllowInsecureAdminToken, "value", v)
		}
	}
	envDuration("AGENTCC_DEFAULT_REQUEST_TIMEOUT", &cfg.Server.DefaultRequestTimeout)
	envDuration("AGENTCC_MAX_REQUEST_TIMEOUT", &cfg.Server.MaxRequestTimeout)
	envDuration("AGENTCC_SHUTDOWN_TIMEOUT", &cfg.Server.ShutdownTimeout)

	// Control plane env overrides.
	if v := os.Getenv("AGENTCC_CONTROL_PLANE_URL"); v != "" {
		cfg.ControlPlane.URL = v
	}
	if v := os.Getenv("AGENTCC_CONTROL_PLANE_TOKEN"); v != "" {
		cfg.ControlPlane.AdminToken = v
	}
	if v := os.Getenv("AGENTCC_SYNC_ON_STARTUP"); v != "" {
		cfg.ControlPlane.SyncOnStartup = v == "true" || v == "1"
	}
	envDuration("AGENTCC_SYNC_INTERVAL", &cfg.ControlPlane.SyncInterval)
	if v := os.Getenv("AGENTCC_KEY_SYNC_GRACE"); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			cfg.ControlPlane.KeySyncGrace = d
		}
	}
	if v := os.Getenv("AGENTCC_WEBHOOK_SECRET"); v != "" {
		cfg.ControlPlane.WebhookSecret = v
	}
	if v := os.Getenv(EnvAllowPrivateProviderURLs); v != "" {
		// Read as the backend reads it, so " true" or "TRUE" opts in both.
		if allow, err := strconv.ParseBool(strings.ToLower(strings.TrimSpace(v))); err == nil {
			cfg.OrgProviders.AllowPrivateURLs = allow
		} else {
			slog.Warn("ignoring unrecognised value; expected true or false",
				"env", EnvAllowPrivateProviderURLs, "value", v)
		}
	}

	// Auth env overrides. Evaluate the explicit toggle first, then let a present
	// internal API key have the final say: it seeds the key store and forces auth
	// on. A configured key can therefore never leave the gateway open, even if
	// AGENTCC_AUTH_ENABLED=false is also set (fail closed).
	if v := os.Getenv("AGENTCC_AUTH_ENABLED"); v != "" {
		if enabled, err := strconv.ParseBool(v); err == nil {
			cfg.Auth.Enabled = enabled
		} else {
			slog.Warn("ignoring environment variable: not true or false",
				"env", "AGENTCC_AUTH_ENABLED", "value", v)
		}
	}
	if v := os.Getenv("AGENTCC_INTERNAL_API_KEY"); v != "" {
		cfg.Auth.Enabled = true
		// Seed as an "internal" key: byok (the keystore's default type) is barred
		// from the global, FutureAGI-credentialed providers, so a mistyped seed
		// authenticates and then 403s the backend's own LLM route.
		//
		// Skip when this key is already configured (e.g. in config.yaml): the
		// keystore is last-write-wins by hash, so appending here would override
		// — and could re-type — an operator's explicit entry.
		if !authKeyConfigured(cfg.Auth.Keys, v) {
			cfg.Auth.Keys = append(cfg.Auth.Keys, AuthKeyConfig{
				Name:     "internal-backend",
				Key:      v,
				Owner:    "futureagi-backend",
				KeyType:  "internal",
				Metadata: map[string]string{"access_groups": "internal"},
			})
		}
	}

	// Redis state env overrides.
	if v := os.Getenv("AGENTCC_REDIS_ADDRESS"); v != "" {
		cfg.Redis.Address = v
		cfg.Redis.Enabled = true
	}
	if v := os.Getenv("AGENTCC_REDIS_PASSWORD"); v != "" {
		cfg.Redis.Password = v
	}
	envInt("AGENTCC_REDIS_DB", &cfg.Redis.DB)

	if v := os.Getenv("AGENTCC_MANAGED_USER_HASH_KEY"); v != "" {
		cfg.Managed.UpstreamUserHashKey = v
	}

	if v := os.Getenv(EnvMCPStateKeys); v != "" {
		cfg.MCP.StateKeys = splitStateKeys(v)
	}
	if v := os.Getenv(EnvMCPDjangoSecret); v != "" {
		cfg.MCP.Django.InternalTokenSecret = v
	}
	// Added to the file's entries, never replacing them; validateMCPKeys
	// checks every entry as it checks the file's.
	if v := os.Getenv(EnvMCPEgressPrivateAllow); v != "" {
		cfg.MCP.Egress.PrivateAllow = append(cfg.MCP.Egress.PrivateAllow, splitStateKeys(v)...)
	}
}

// splitStateKeys reads EnvMCPStateKeys: comma-separated keys, current first,
// surrounding spaces and empty entries dropped. EnvMCPEgressPrivateAllow is
// read the same way.
func splitStateKeys(v string) []string {
	var keys []string
	for _, k := range strings.Split(v, ",") {
		if k = strings.TrimSpace(k); k != "" {
			keys = append(keys, k)
		}
	}
	return keys
}

// resolveDependentDefaults fills the defaults that depend on other keys or
// sit inside map entries, which DefaultConfig cannot set: the MCP session
// store (redis when Redis is configured) and each managed tool server's kind
// and billing. Validate also accepts the empty values, for a Config built in
// code.
func (c *Config) resolveDependentDefaults() {
	if c.MCP.SessionStore == "" {
		c.MCP.SessionStore = MCPSessionStoreMemory
		if c.redisConfigured() {
			c.MCP.SessionStore = MCPSessionStoreRedis
		}
	}
	for id, s := range c.MCP.ManagedServers {
		if s.Kind == "" {
			s.Kind = ManagedServerKindRemote
		}
		if s.Billing == "" {
			s.Billing = ManagedServerBillingReserve
		}
		c.MCP.ManagedServers[id] = s
	}
}

func (c *Config) redisConfigured() bool {
	return c.Redis.Enabled && c.Redis.Address != ""
}

// Validate checks that the config is valid.
func (c *Config) Validate() error {
	if c.Server.Port < 1 || c.Server.Port > 65535 {
		return fmt.Errorf("server.port must be between 1 and 65535, got %d", c.Server.Port)
	}
	if c.Server.ReadTimeout <= 0 {
		return fmt.Errorf("server.read_timeout must be positive")
	}
	if c.Server.ReadHeaderTimeout <= 0 {
		return fmt.Errorf("server.read_header_timeout must be positive")
	}
	if c.Server.WriteTimeout <= 0 {
		return fmt.Errorf("server.write_timeout must be positive")
	}
	if c.Server.DefaultRequestTimeout <= 0 {
		return fmt.Errorf("server.default_request_timeout must be positive")
	}
	if c.Async.MaxWorkers < 0 || c.Async.MaxWorkersPerOrg < 0 || c.Async.MaxJobTimeout < 0 {
		return fmt.Errorf("async.max_workers, async.max_workers_per_org and async.max_job_timeout must not be negative")
	}
	// A default above the ceiling is a request the gateway would clamp on every
	// call, so it is a configuration mistake rather than something to normalize.
	if c.Server.MaxRequestTimeout > 0 && c.Server.MaxRequestTimeout < c.Server.DefaultRequestTimeout {
		return fmt.Errorf("server.default_request_timeout (%s) is above the ceiling server.max_request_timeout (%s)",
			c.Server.DefaultRequestTimeout, c.Server.MaxRequestTimeout)
	}

	for name, p := range c.Providers {
		if p.BaseURL == "" {
			return fmt.Errorf("provider %q: base_url is required", name)
		}
		if p.APIFormat == "" {
			return fmt.Errorf("provider %q: api_format is required", name)
		}
	}

	if err := c.validateLicenseAuth(); err != nil {
		return err
	}

	// A NaN passes the range checks (every comparison with it is false), and
	// the managed and tool prices are computed from these percentages.
	if math.IsNaN(c.Managed.MarkupPercent) || math.IsInf(c.Managed.MarkupPercent, 0) {
		return fmt.Errorf("managed.markup_percent must be a finite number, got %v", c.Managed.MarkupPercent)
	}
	if math.IsNaN(c.Managed.ToolMarkupPercent) || math.IsInf(c.Managed.ToolMarkupPercent, 0) {
		return fmt.Errorf("managed.tool_markup_percent must be a finite number, got %v", c.Managed.ToolMarkupPercent)
	}
	if c.Managed.MarkupPercent < 0 || c.Managed.MarkupPercent > 100 {
		return fmt.Errorf("managed.markup_percent must be between 0 and 100, got %v", c.Managed.MarkupPercent)
	}
	if c.Managed.CatalogRefresh < 0 {
		return fmt.Errorf("managed.catalog_refresh must not be negative")
	}
	if tm := c.Managed.ToolMarkupPercent; tm != -1 && (tm < 0 || tm > 100) {
		return fmt.Errorf("managed.tool_markup_percent must be -1 (inherit managed.markup_percent) or between 0 and 100, got %v", tm)
	}
	if err := c.validateV2Keys(); err != nil {
		return err
	}
	if err := c.validateMCPKeys(); err != nil {
		return err
	}
	if err := c.validateRunAndToolKeys(); err != nil {
		return err
	}
	if err := c.validateShippedShellRefusals(); err != nil {
		return err
	}

	if err := c.validateAdminTokens(); err != nil {
		return err
	}

	if _, err := ParseTrustedProxies(c.Server.TrustedProxies, c.Server.TrustedHops); err != nil {
		return err
	}

	validLevels := map[string]bool{"debug": true, "info": true, "warn": true, "error": true}
	if !validLevels[strings.ToLower(c.Logging.Level)] {
		return fmt.Errorf("logging.level must be one of debug, info, warn, error; got %q", c.Logging.Level)
	}

	if err := c.validateOTel(); err != nil {
		return err
	}

	return nil
}

// validateV2Keys checks the managed, catalog, generations, idempotency,
// runtime-token and privacy keys: enum values, signs, and a runtime-token
// setup that can verify a token.
func (c *Config) validateV2Keys() error {
	// An empty enum is the default (a Config built without DefaultConfig).
	switch c.Managed.UpstreamKeyMode {
	case "", UpstreamKeyModeShared, UpstreamKeyModePerOrg, UpstreamKeyModePerOrgWithSharedFallback:
	default:
		return fmt.Errorf("managed.upstream_key_mode must be shared, per_org or per_org_with_shared_fallback; got %q", c.Managed.UpstreamKeyMode)
	}
	switch c.Privacy.DefaultContentMode {
	case "", "full", "metadata", "none":
	default:
		return fmt.Errorf("privacy.default_content_mode must be full, metadata or none; got %q", c.Privacy.DefaultContentMode)
	}
	if c.Managed.HoldTTL < 0 || c.Managed.AccountMissingRetryAfter < 0 || c.Managed.ReserveDefaultOutputTokens < 0 ||
		c.Managed.UnpricedReserveUSD < 0 || c.Managed.MaxOverdraftUSD < 0 || c.Managed.ProcessingFeeBps < 0 {
		return fmt.Errorf("managed: hold_ttl, account_missing_retry_after, reserve_default_output_tokens, unpriced_reserve_usd, max_overdraft_usd and processing_fee_bps must not be negative")
	}
	for name, p := range c.Providers {
		if p.UpstreamFeeBps != nil && *p.UpstreamFeeBps < 0 {
			return fmt.Errorf("provider %q: upstream_fee_bps must not be negative", name)
		}
		switch p.CatalogSource {
		case "", "openrouter", "openai_compatible", "price_card":
		default:
			return fmt.Errorf("provider %q: catalog_source must be openrouter, openai_compatible or price_card; got %q", name, p.CatalogSource)
		}
	}
	if c.Catalog.HistorySize < 0 || c.Catalog.KeepSnapshots < 0 || c.Catalog.EndpointsRefresh < 0 || c.Catalog.EndpointsRPS < 0 {
		return fmt.Errorf("catalog: history_size, keep_snapshots, endpoints_refresh and endpoints_rps must not be negative")
	}
	if c.Generations.TTL < 0 || c.Idempotency.TTL < 0 || c.Idempotency.MaxBodyBytes < 0 {
		return fmt.Errorf("generations.ttl, idempotency.ttl and idempotency.max_body_bytes must not be negative")
	}
	rt := c.RuntimeTokens
	if rt.MaxTTL < 0 || rt.MaxTTL > MaxRuntimeTokenTTL {
		return fmt.Errorf("runtime_tokens.max_ttl must be at most %s; got %s", MaxRuntimeTokenTTL, rt.MaxTTL)
	}
	if rt.ClockSkew < 0 {
		return fmt.Errorf("runtime_tokens.clock_skew must not be negative")
	}
	if rt.Enabled {
		if rt.Issuer == "" || rt.Audience == "" || rt.MaxTTL == 0 {
			return fmt.Errorf("runtime_tokens.issuer, audience and max_ttl are required when runtime tokens are enabled")
		}
		if len(rt.PublicKeys) == 0 {
			return fmt.Errorf("runtime_tokens.public_keys needs at least one key when runtime tokens are enabled")
		}
		for i, k := range rt.PublicKeys {
			if k.Kid == "" || k.PublicKey == "" {
				return fmt.Errorf("runtime_tokens.public_keys[%d] needs a kid and a public_key", i)
			}
		}
	}
	return nil
}

// EffectiveUpstreamFeeBps is the provider's upstream fee: upstream_fee_bps
// when set, else 550 (OpenRouter Standard) for an openrouter catalog source
// and 0 for any other source.
func (c *ProviderConfig) EffectiveUpstreamFeeBps() int {
	if c.UpstreamFeeBps != nil {
		return *c.UpstreamFeeBps
	}
	if c.CatalogSource == "openrouter" {
		return 550
	}
	return 0
}

// ToolMarkupBps is the markup on managed tool calls in basis points:
// tool_markup_percent, or markup_percent when it is -1. 5% is
// 500.
func (m ManagedConfig) ToolMarkupBps() int {
	pct := m.ToolMarkupPercent
	if pct < 0 {
		pct = m.MarkupPercent
	}
	if !(pct > 0) {
		return 0
	}
	return int(math.Round(pct * 100))
}

// mcpServerIDPattern is an MCP server id, as org config writes it.
var mcpServerIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,47}$`)

// validMCPEra reports whether era pins a known MCP revision or is empty or
// "auto" (detected).
func validMCPEra(era string) bool {
	return era == "" || era == "auto" || slices.Contains(MCPProtocolVersions(), era)
}

func isHTTPURL(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && (u.Scheme == "http" || u.Scheme == "https") && u.Host != ""
}

// APIRouteRoots are the first path segments of the gateway's own routes.
// The MCP endpoint may not be "/" or sit under any of these roots, so
// every API route keeps its own gate and handler. The
// server's TestAPIRouteRootsCoverEveryRoute keeps this list equal to the
// router's routes. "/deepgram" and "/cartesia" are the speech relays' roots
// (providers/speech.Endpoints).
var APIRouteRoots = []string{"/v1", "/v1beta", "/-", "/a2a", "/.well-known", "/healthz", "/livez", "/readyz", "/deepgram", "/cartesia"}

// mcpEndpointCollision reports why endpoint cannot be the MCP endpoint, ""
// when it can. An empty endpoint takes the default, /mcp.
func mcpEndpointCollision(endpoint string) string {
	if endpoint == "" {
		return ""
	}
	if !strings.HasPrefix(endpoint, "/") {
		return "it must be an absolute path such as /mcp"
	}
	clean := path.Clean(endpoint)
	if clean == "/" {
		return "it would cover every route"
	}
	root := "/" + strings.SplitN(strings.TrimPrefix(clean, "/"), "/", 2)[0]
	if slices.Contains(APIRouteRoots, root) {
		return fmt.Sprintf("it is under the gateway's own %s routes", root)
	}
	return ""
}

// validateMCPKeys checks the MCP keys: the endpoint, a state key whenever MCP
// is on (each at least MinMCPStateKeyBytes), enums, limits, server and
// managed-server shapes, and the egress exceptions. Error messages never print
// a key or secret.
func (c *Config) validateMCPKeys() error {
	m := &c.MCP
	if why := mcpEndpointCollision(m.Endpoint); why != "" {
		return fmt.Errorf("mcp.endpoint %q is refused: %s", m.Endpoint, why)
	}
	for i, k := range m.StateKeys {
		if len(k) < MinMCPStateKeyBytes {
			return fmt.Errorf("mcp.state_keys[%d] is %d bytes; each state key must be at least %d bytes", i, len(k), MinMCPStateKeyBytes)
		}
	}
	if m.Enabled && len(m.StateKeys) == 0 && !m.DevInsecureState {
		return fmt.Errorf("mcp.enabled needs a state key: set %s (comma-separated, current key first, each at least %d bytes) or mcp.state_keys", EnvMCPStateKeys, MinMCPStateKeyBytes)
	}
	if m.MaxBodyBytes <= 0 || m.MaxResultBytes <= 0 || m.Stdio.MaxMessageBytes <= 0 || m.Stdio.StderrMaxLineBytes <= 0 {
		return fmt.Errorf("mcp.max_body_bytes, mcp.max_result_bytes, mcp.stdio.max_message_bytes and mcp.stdio.stderr_max_line_bytes must be positive")
	}
	// mcp.StateCodec.Seal refuses a TTL that is not positive, so Load refuses a
	// zero mcp.approvals.default_ttl when MCP is on.
	if m.Enabled && m.Approvals.DefaultTTL == 0 {
		return fmt.Errorf("mcp.approvals.default_ttl must be positive when mcp.enabled is true")
	}
	if m.Enabled && len(m.Protocol.Versions) == 0 {
		return fmt.Errorf("mcp.protocol.versions needs at least one version when mcp is enabled")
	}
	for _, v := range m.Protocol.Versions {
		if !slices.Contains(MCPProtocolVersions(), v) {
			return fmt.Errorf("mcp.protocol.versions: %q is not one of %s", v, strings.Join(MCPProtocolVersions(), ", "))
		}
	}
	switch m.SessionStore {
	case "", MCPSessionStoreMemory:
	case MCPSessionStoreRedis:
		if !c.redisConfigured() {
			return fmt.Errorf("mcp.session_store is redis, but Redis is not configured (redis.enabled and redis.address)")
		}
	default:
		return fmt.Errorf("mcp.session_store must be memory or redis; got %q", m.SessionStore)
	}
	switch m.Stdio.Launcher {
	case "", "local", "sandbox":
	default:
		return fmt.Errorf("mcp.stdio.launcher must be local or sandbox; got %q", m.Stdio.Launcher)
	}
	if slices.Contains(m.Stdio.BaseEnv, "") {
		return fmt.Errorf("mcp.stdio.base_env has an empty name")
	}
	if m.Stdio.Grace < 0 || m.Approvals.DefaultTTL < 0 || m.OAuthAdapters.RefreshSkew < 0 || m.CallbackMaxCalls < 0 ||
		m.FairUse.OrgRPM < 0 || m.FairUse.KeyRPM < 0 || m.FairUse.MaxConcurrent < 0 {
		return fmt.Errorf("mcp: stdio.grace, approvals.default_ttl, oauth_adapters.refresh_skew, callback_max_calls and fair_use limits must not be negative")
	}
	if m.RateLimit.Replicas < 1 {
		return fmt.Errorf("mcp.ratelimit.replicas must be at least 1; got %d", m.RateLimit.Replicas)
	}
	for _, p := range [][2]string{{"managed", m.ResponsesPassthrough.Managed}, {"byok", m.ResponsesPassthrough.BYOK}} {
		if p[1] != "" && p[1] != "allow" && p[1] != "deny" {
			return fmt.Errorf("mcp.responses_passthrough.%s must be allow or deny; got %q", p[0], p[1])
		}
	}
	for _, u := range [][2]string{{"mcp.public_url", m.PublicURL}, {"mcp.django.base_url", m.Django.BaseURL}} {
		if u[1] != "" && !isHTTPURL(u[1]) {
			return fmt.Errorf("%s must be an absolute http(s) URL", u[0])
		}
	}
	sh := m.Shaping
	if sh.DefaultBudgetBytes < 0 || sh.PreviewBytes < 0 || sh.SpillTTL < 0 || sh.SpillMaxBytes < 0 {
		return fmt.Errorf("mcp.shaping limits must not be negative")
	}
	if sh.DefaultBudgetBytes > m.MaxResultBytes || sh.SpillMaxBytes > m.MaxResultBytes {
		return fmt.Errorf("mcp.shaping.default_budget_bytes (%d) and spill_max_bytes (%d) must not exceed mcp.max_result_bytes (%d), the transport ceiling",
			sh.DefaultBudgetBytes, sh.SpillMaxBytes, m.MaxResultBytes)
	}
	if err := m.validateServers(); err != nil {
		return err
	}
	if err := m.validateManagedServers(); err != nil {
		return err
	}
	for i, entry := range m.Egress.PrivateAllow {
		if err := checkPrivateAllowEntry(entry); err != nil {
			return fmt.Errorf("mcp.egress.private_allow[%d] %q (the config file's entries, then %s's): %w",
				i, entry, EnvMCPEgressPrivateAllow, err)
		}
	}
	return nil
}

// validateServers checks the operator's YAML MCP servers.
func (m *MCPConfig) validateServers() error {
	for _, id := range slices.Sorted(maps.Keys(m.Servers)) {
		s := m.Servers[id]
		if !validMCPEra(s.Era) {
			return fmt.Errorf("mcp.servers.%s.era must be auto or one of %s; got %q", id, strings.Join(MCPProtocolVersions(), ", "), s.Era)
		}
		if s.TimeoutMS < 0 {
			return fmt.Errorf("mcp.servers.%s.timeout_ms must not be negative", id)
		}
		if _, ok := s.Env[""]; ok || slices.Contains(s.EnvFromHost, "") {
			return fmt.Errorf("mcp.servers.%s: env and env_from_host need non-empty names", id)
		}
		for _, h := range s.ForwardHeaders {
			if strings.TrimSpace(h) == "" {
				return fmt.Errorf("mcp.servers.%s.forward_headers has an empty name", id)
			}
			if ForwardHeaderRefused(h) {
				return fmt.Errorf("mcp.servers.%s.forward_headers names %q, which is never forwarded upstream (credentials, MCP session and version headers, x-agentcc-* and x-omega-*)", id, h)
			}
		}
	}
	return nil
}

// validateManagedServers checks each managed tool server's kind, billing and
// price-card reference. Ids are one namespace, written with or without the
// fagi. prefix, so "web" and "fagi.web" are the same server. The card itself
// is resolved later, by the tool catalog.
func (m *MCPConfig) validateManagedServers() error {
	seen := make(map[string]string, len(m.ManagedServers))
	for _, key := range slices.Sorted(maps.Keys(m.ManagedServers)) {
		s := m.ManagedServers[key]
		id := strings.TrimPrefix(key, "fagi.")
		if !mcpServerIDPattern.MatchString(id) {
			return fmt.Errorf("mcp.managed_servers.%s: the id must match %s, with or without the fagi. prefix", key, mcpServerIDPattern)
		}
		if other, dup := seen[id]; dup {
			return fmt.Errorf("mcp.managed_servers.%s and mcp.managed_servers.%s are both fagi.%s; managed server ids are one namespace", other, key, id)
		}
		seen[id] = key
		switch s.Kind {
		case "", ManagedServerKindRemote:
			if !isHTTPURL(s.URL) {
				return fmt.Errorf("mcp.managed_servers.%s: a remote server needs an absolute http(s) url", key)
			}
		case ManagedServerKindBuiltin:
			if s.URL != "" || s.CredentialEnv != "" {
				return fmt.Errorf("mcp.managed_servers.%s: a builtin server has no url and no credential_env", key)
			}
		default:
			return fmt.Errorf("mcp.managed_servers.%s.kind must be remote or builtin; got %q", key, s.Kind)
		}
		billing := s.Billing
		switch billing {
		case "", ManagedServerBillingReserve:
			billing = ManagedServerBillingReserve
		case ManagedServerBillingDelegated:
		default:
			return fmt.Errorf("mcp.managed_servers.%s.billing must be reserve or delegated; got %q", key, s.Billing)
		}
		if !validMCPEra(s.Era) {
			return fmt.Errorf("mcp.managed_servers.%s.era must be auto or one of %s; got %q", key, strings.Join(MCPProtocolVersions(), ", "), s.Era)
		}
		switch s.Transport {
		case "", "http", "sse":
		default:
			return fmt.Errorf("mcp.managed_servers.%s.transport must be http or sse; got %q", key, s.Transport)
		}
		// A reserve server is held at its card's price, so it needs one; a
		// delegated server may name one, and then all of it.
		if billing == ManagedServerBillingReserve || !s.PriceCard.IsZero() {
			for _, f := range [][2]string{{"capability", s.PriceCard.Capability}, {"vendor", s.PriceCard.Vendor}, {"sku", s.PriceCard.SKU}} {
				if strings.TrimSpace(f[1]) == "" {
					return fmt.Errorf("mcp.managed_servers.%s.price_card needs capability, vendor and sku; %s is missing", key, f[0])
				}
			}
		}
	}
	return nil
}

// Cloud metadata services, which mcp.egress.private_allow can never open.
// The dial-time guard (netguard) refuses them as well.
var (
	metadataNets = mustParseCIDRs(
		"169.254.0.0/16",     // link-local: AWS, GCP, Azure, OCI, DigitalOcean metadata at 169.254.169.254
		"fd00:ec2::254/128",  // AWS IMDS over IPv6
		"fd20:ce::254/128",   // GCP metadata over IPv6 (IPv6-only instances)
		"100.100.100.200/32", // Alibaba Cloud
		"168.63.129.16/32",   // Azure WireServer
	)
	linkLocalNet      = metadataNets[0]
	metadataHostnames = map[string]bool{
		"metadata.google.internal":   true,
		"metadata.goog":              true,
		"metadata":                   true,
		"instance-data":              true,
		"instance-data.ec2.internal": true,
	}
)

func mustParseCIDRs(cidrs ...string) []*net.IPNet {
	nets := make([]*net.IPNet, len(cidrs))
	for i, s := range cidrs {
		_, n, err := net.ParseCIDR(s)
		if err != nil {
			panic(err)
		}
		nets[i] = n
	}
	return nets
}

// cidrCovers reports whether n contains ip. Unlike net.IPNet.Contains it
// compares 16-byte forms for an IPv6 range, so a range that spans the
// IPv4-mapped block (::/0, ::/1 ...) covers the IPv4 addresses mapped into it.
func cidrCovers(n *net.IPNet, ip net.IP) bool {
	if n.Contains(ip) {
		return true
	}
	ip16, base := ip.To16(), n.IP.To16()
	if len(n.Mask) != net.IPv6len || ip16 == nil || base == nil {
		return false
	}
	for i := range ip16 {
		if ip16[i]&n.Mask[i] != base[i]&n.Mask[i] {
			return false
		}
	}
	return true
}

// checkPrivateAllowEntry checks one mcp.egress.private_allow entry: a CIDR or
// host[:port]. It refuses an entry inside a metadata range, a metadata
// hostname, and any range that covers part of the link-local block (so
// 0.0.0.0/0 too). A wider private range that only contains a single metadata
// address (100.64.0.0/10 holds 100.100.100.200, fc00::/7 holds fd00:ec2::254
// and fd20:ce::254)
// is accepted: the dial-time guard still refuses that address.
func checkPrivateAllowEntry(entry string) error {
	e := strings.TrimSpace(entry)
	if e == "" {
		return errors.New("is empty")
	}
	errMetadata := errors.New("is a cloud-metadata address, which is never reachable")
	if strings.Contains(e, "/") {
		_, n, err := net.ParseCIDR(e)
		if err != nil {
			return errors.New("is not a valid CIDR")
		}
		if cidrCovers(n, linkLocalNet.IP) {
			return errMetadata
		}
		for _, mn := range metadataNets {
			if mn.Contains(n.IP) {
				return errMetadata
			}
		}
		return nil
	}
	host := e
	if h, port, err := net.SplitHostPort(e); err == nil {
		if p, err := strconv.Atoi(port); err != nil || p < 1 || p > 65535 {
			return errors.New("has an invalid port")
		}
		host = h
	}
	host = strings.TrimSuffix(strings.ToLower(strings.Trim(host, "[]")), ".")
	if host == "" {
		return errors.New("has no host")
	}
	if ip := net.ParseIP(host); ip != nil {
		for _, mn := range metadataNets {
			if mn.Contains(ip) {
				return errMetadata
			}
		}
		return nil
	}
	if metadataHostnames[host] {
		return errMetadata
	}
	return nil
}

// validateRunAndToolKeys checks the global run, identity, security, response,
// telemetry and tool keys.
func (c *Config) validateRunAndToolKeys() error {
	h := c.Budgets.Hold
	switch h.Unpriced {
	case "", BudgetUnpricedReserveDefault, BudgetUnpricedRefuse:
	default:
		return fmt.Errorf("budgets.hold.unpriced must be reserve_default or refuse; got %q", h.Unpriced)
	}
	e := c.Runs.Envelopes
	if h.UnpricedReserveUSD < 0 || e.MaxTreeDepth < 0 || e.LoopWindow < 0 || e.LoopThreshold < 0 || e.FanoutWindowS < 0 ||
		c.Identity.ChainMaxHops < 0 || c.Identity.GrantCacheTTL < 0 {
		return fmt.Errorf("budgets.hold.unpriced_reserve_usd, runs.envelopes and identity limits must not be negative")
	}
	if a := c.Security.AdminListenAddr; a != "" {
		if _, _, err := net.SplitHostPort(a); err != nil {
			return fmt.Errorf("security.admin_listen_addr must be host:port; got %q", a)
		}
	}
	hl := c.Responses.Hollow
	switch hl.Mode {
	case "", "off", "shadow", "retry", "fail":
	default:
		return fmt.Errorf("responses.hollow.mode must be off, shadow, retry or fail; got %q", hl.Mode)
	}
	switch hl.BYOK {
	case "", "flag", "retry":
	default:
		return fmt.Errorf("responses.hollow.byok must be flag or retry; got %q", hl.BYOK)
	}
	if hl.MaxHollowRetries < 0 || hl.AbsorbCapUSDPerOrgDay < 0 || c.Tools.ReserveDefaultUSD < 0 || c.Tools.CacheTTLSeconds < 0 {
		return fmt.Errorf("responses.hollow limits, tools.reserve_default_usd and tools.cache_ttl_seconds must not be negative")
	}
	for _, name := range slices.Sorted(maps.Keys(c.Capabilities.PerCapability)) {
		if c.Capabilities.PerCapability[name].TimeoutMS < 0 {
			return fmt.Errorf("capabilities.%s.timeout_ms must not be negative", name)
		}
	}
	return nil
}

// validateShippedShellRefusals refuses two settings at load: routing.scheduled.enabled, since this gateway does not run scheduled jobs, and a FutureAGI
// guardrail rule that lacks its credentials or eval id.
func (c *Config) validateShippedShellRefusals() error {
	if c.Routing.Scheduled.Enabled {
		return fmt.Errorf("routing.scheduled.enabled must be false: this gateway does not run scheduled jobs")
	}
	for i, rule := range c.Guardrails.Rules {
		if field := missingFutureAGIField(rule.Config); field != "" {
			return fmt.Errorf("guardrails.rules[%d] %q (provider futureagi) is missing %s; a ${VAR} that is not set counts as missing", i, rule.Name, field)
		}
	}
	return nil
}

// missingFutureAGIField names the field a provider: futureagi rule lacks, or
// "" when cfg is complete or another provider's. It reads the keys the
// guardrail reads (guardrails/futureagi, which imports this package): api_key
// and secret_key after the guardrail's own ${VAR} expansion, and an eval id
// from eval_ids or else eval_id. A rule names its own credentials: the
// guardrail reads no process-wide key for it.
func missingFutureAGIField(cfg map[string]interface{}) string {
	if provider, _ := cfg["provider"].(string); provider != "futureagi" {
		return ""
	}
	for _, key := range []string{"api_key", "secret_key"} {
		v, _ := cfg[key].(string)
		if expandWholeEnvRef(v) == "" {
			return key
		}
	}
	switch ids := cfg["eval_ids"].(type) {
	case []interface{}:
		for _, id := range ids {
			if s, ok := id.(string); ok && s != "" {
				return ""
			}
		}
	case []string:
		if slices.ContainsFunc(ids, func(s string) bool { return s != "" }) {
			return ""
		}
	}
	if id, _ := cfg["eval_id"].(string); id != "" {
		return ""
	}
	return "eval_id or eval_ids"
}

// expandWholeEnvRef expands s when it is exactly ${NAME}, as the FutureAGI
// guardrail does.
func expandWholeEnvRef(s string) string {
	if strings.HasPrefix(s, "${") && strings.HasSuffix(s, "}") {
		return os.Getenv(s[2 : len(s)-1])
	}
	return s
}

// validateOTel checks the OTLP exporter settings. Misconfiguration here is
// caught at startup rather than silently degrading to stdout, because a
// collector that never receives spans looks identical to a quiet gateway.
func (c *Config) validateOTel() error {
	if !c.OTel.Enabled {
		return nil
	}
	if strings.ToLower(c.OTel.Exporter) != "otlp" {
		return nil
	}
	if c.OTel.Endpoint == "" {
		return fmt.Errorf("otel.endpoint is required when otel.exporter is %q", "otlp")
	}
	u, err := url.Parse(c.OTel.Endpoint)
	if err != nil || u.Scheme == "" || u.Host == "" {
		return fmt.Errorf("otel.endpoint must be an absolute http(s) URL, got %q", c.OTel.Endpoint)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("otel.endpoint scheme must be http or https, got %q", u.Scheme)
	}
	// An empty header value is almost always an unset ${VAR}. Sending it
	// blindly means the collector 401s and every span is silently discarded,
	// which is indistinguishable from a gateway with no traffic.
	for k, v := range c.OTel.Headers {
		if k == "" {
			return fmt.Errorf("otel.headers has an entry with an empty name")
		}
		if v == "" {
			return fmt.Errorf("otel.headers[%q] is empty; if it references an environment variable, that variable is not set", k)
		}
	}
	switch strings.ToLower(c.OTel.Protocol) {
	case "", "http/protobuf":
		return nil
	case "grpc", "http/json":
		return fmt.Errorf("otel.protocol %q is not supported; use \"http/protobuf\"", c.OTel.Protocol)
	default:
		return fmt.Errorf("otel.protocol must be \"http/protobuf\", got %q", c.OTel.Protocol)
	}
}

func (c *Config) validateLicenseAuth() error {
	cfg := c.LicenseAuth
	if !cfg.Enabled {
		return nil
	}
	if !cfg.RuntimeStateRequired {
		return fmt.Errorf("license_auth.runtime_state_required must be true")
	}
	if !c.Redis.Enabled || c.Redis.Address == "" {
		return fmt.Errorf("license_auth requires enabled Redis with an address")
	}
	if cfg.Issuer == "" || cfg.Audience == "" || cfg.TokenType == "" {
		return fmt.Errorf("license_auth issuer, audience, and token_type are required")
	}
	if cfg.ClockSkewSeconds < 0 {
		return fmt.Errorf("license_auth.clock_skew_seconds must be non-negative")
	}

	keyCount := 0
	if cfg.PublicKey != "" {
		if err := validateLicenseRSAPublicKey(cfg.PublicKey); err != nil {
			return fmt.Errorf("license_auth.public_key: %w", err)
		}
		keyCount++
	}
	seen := make(map[string]struct{}, len(cfg.PublicKeys))
	for _, entry := range cfg.PublicKeys {
		if entry.KID == "" {
			return fmt.Errorf("license_auth.public_keys entries require kid")
		}
		if _, ok := seen[entry.KID]; ok {
			return fmt.Errorf("license_auth.public_keys contains duplicate kid %q", entry.KID)
		}
		seen[entry.KID] = struct{}{}
		if err := validateLicenseRSAPublicKey(entry.PublicKey); err != nil {
			return fmt.Errorf("license_auth.public_keys[%s]: %w", entry.KID, err)
		}
		keyCount++
	}
	if keyCount == 0 {
		return fmt.Errorf("license_auth requires at least one RSA public key")
	}
	return nil
}

func validateLicenseRSAPublicKey(raw string) error {
	block, _ := pem.Decode([]byte(raw))
	if block == nil {
		return fmt.Errorf("invalid PEM public key")
	}
	parsed, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		if _, pkcs1Err := x509.ParsePKCS1PublicKey(block.Bytes); pkcs1Err != nil {
			return err
		}
		return nil
	}
	if _, ok := parsed.(*rsa.PublicKey); !ok {
		return fmt.Errorf("public key is not RSA")
	}
	return nil
}

// Addr returns the listen address.
func (c *Config) Addr() string {
	return fmt.Sprintf("%s:%d", c.Server.Host, c.Server.Port)
}

// DefaultAPIPathPrefix is the version segment assumed for an OpenAI-format
// provider that does not state one.
const DefaultAPIPathPrefix = "/v1"

// EffectiveAPIPathPrefix returns the version segment for this provider,
// normalised to a leading slash and no trailing one.
func (c *ProviderConfig) EffectiveAPIPathPrefix() string {
	if c.APIPathPrefix == nil {
		return DefaultAPIPathPrefix
	}
	return normalizePathPrefix(*c.APIPathPrefix)
}

// EndpointURL builds an upstream URL for a versioned path such as
// "/v1/chat/completions", replacing the caller's assumed "/v1" with whatever
// this provider actually uses.
//
// A base_url that already ends in the prefix keeps it rather than repeating it:
// "https://api.cohere.ai/compatibility/v1" resolves to ".../compatibility/v1/
// chat/completions", not ".../v1/v1/...".
func (c *ProviderConfig) EndpointURL(path string) string {
	return JoinEndpoint(c.BaseURL, c.EffectiveAPIPathPrefix(), path)
}

// JoinEndpoint is EndpointURL for callers holding a base URL rather than a
// config — the registry builds probe URLs before any provider exists.
func JoinEndpoint(baseURL, prefix, path string) string {
	base := strings.TrimRight(baseURL, "/")
	path = trimDefaultPrefix(path)
	if prefix == "" {
		return base + path
	}
	if strings.HasSuffix(base, prefix) {
		return base + path
	}
	return base + prefix + path
}

// trimDefaultPrefix removes the "/v1" the call sites hardcode, but only when it
// is a whole path segment. A blind TrimPrefix eats the "/v1" out of "/v10/models"
// and "/v1beta/models", leaving "0/models" to be re-prefixed into "/v20/models".
// Only the proxy endpoints take a caller-supplied path, so that is where a
// non-"/v1" version segment can actually arrive.
func trimDefaultPrefix(path string) string {
	if path == DefaultAPIPathPrefix {
		return ""
	}
	if strings.HasPrefix(path, DefaultAPIPathPrefix+"/") {
		return strings.TrimPrefix(path, DefaultAPIPathPrefix)
	}
	return path
}

func normalizePathPrefix(prefix string) string {
	prefix = strings.TrimRight(strings.TrimSpace(prefix), "/")
	if prefix == "" {
		return ""
	}
	if !strings.HasPrefix(prefix, "/") {
		prefix = "/" + prefix
	}
	return prefix
}
