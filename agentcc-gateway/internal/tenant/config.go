// Package tenant provides per-organization gateway configuration.
// Each org can have its own provider API keys, guardrail pipeline,
// routing strategy, cache settings, and rate limits. The gateway is
// shared infrastructure, but per-request behavior is determined by
// the org that owns the API key.
package tenant

// OrgConfig holds the merged configuration for a single organization.
type OrgConfig struct {
	Providers    map[string]*ProviderConfig `json:"providers,omitempty"`
	Guardrails   *GuardrailConfig           `json:"guardrails,omitempty"`
	Routing      *RoutingConfig             `json:"routing,omitempty"`
	Cache        *CacheConfig               `json:"cache,omitempty"`
	RateLimiting *RateLimitConfig           `json:"rate_limiting,omitempty"`
	Budgets      *BudgetsConfig             `json:"budgets,omitempty"`
	CostTracking *CostTrackingConfig        `json:"cost_tracking,omitempty"`
	IPACL        *IPACLConfig               `json:"ip_acl,omitempty"`
	Alerting     *AlertingConfig            `json:"alerting,omitempty"`
	Privacy      *PrivacyConfig             `json:"privacy,omitempty"`
	ToolPolicy   *ToolPolicyConfig          `json:"tool_policy,omitempty"`
	MCP           *MCPOrgConfig              `json:"mcp,omitempty"`
	Audit         *AuditOrgConfig            `json:"audit,omitempty"`
	A2A           *A2AOrgConfig              `json:"a2a,omitempty"`
	ModelDatabase *ModelDatabaseOrgConfig    `json:"model_database,omitempty"`
	ModelMap       map[string]string          `json:"model_map,omitempty"`
}

// ProviderConfig holds per-org provider settings.
type ProviderConfig struct {
	APIKey         string   `json:"api_key"`
	BaseURL        string   `json:"base_url,omitempty"`
	APIFormat      string   `json:"api_format,omitempty"`
	Models         []string `json:"models,omitempty"`
	Timeout        int      `json:"timeout,omitempty"` // seconds
	Weight         float64  `json:"weight,omitempty"`
	Enabled        bool     `json:"enabled"`
	MaxConcurrent  int      `json:"max_concurrent,omitempty"`
	ConnPoolSize   int      `json:"conn_pool_size,omitempty"`

	// AWS Bedrock credentials (per-org, pushed from Django).
	AWSAccessKeyID     string `json:"aws_access_key_id,omitempty"`
	AWSSecretAccessKey string `json:"aws_secret_access_key,omitempty"`
	AWSRegion          string `json:"aws_region,omitempty"`
	AWSSessionToken    string `json:"aws_session_token,omitempty"`
}

// HasCredentials returns true if the provider has any form of authentication configured.
func (p *ProviderConfig) HasCredentials() bool {
	return p.APIKey != "" || (p.AWSAccessKeyID != "" && p.AWSSecretAccessKey != "")
}

// GuardrailConfig holds per-org guardrail pipeline settings.
type GuardrailConfig struct {
	PipelineMode string                     `json:"pipeline_mode,omitempty"` // "parallel" or "sequential"
	FailOpen     bool                       `json:"fail_open,omitempty"`
	TimeoutMs    int                        `json:"timeout_ms,omitempty"`
	Checks       map[string]*GuardrailCheck `json:"checks,omitempty"`
}

// GuardrailCheck configures a single guardrail check for an org.
type GuardrailCheck struct {
	Enabled             bool                   `json:"enabled"`
	Action              string                 `json:"action,omitempty"` // "block", "warn", "mask", "log"
	ConfidenceThreshold float64                `json:"confidence_threshold,omitempty"`
	Config              map[string]interface{} `json:"config,omitempty"` // check-specific settings
}

// RoutingConfig holds per-org routing strategy settings.
type RoutingConfig struct {
	Strategy            string              `json:"strategy,omitempty"` // "round_robin", "weighted", "least_latency", "cost_optimized", "fastest"
	FallbackEnabled     bool                `json:"fallback_enabled,omitempty"`
	FallbackStatusCodes []int               `json:"fallback_on_status_codes,omitempty"`
	ModelFallbacks      map[string][]string  `json:"model_fallbacks,omitempty"`
	ConditionalRoutes   []ConditionalRoute   `json:"conditional_routes,omitempty"`
	DefaultModel        string              `json:"default_model,omitempty"`

	// Advanced routing features (Phase 12A)
	Complexity   *ComplexityRoutingConfig `json:"complexity,omitempty"`
	Fastest      *FastestResponseConfig   `json:"fastest,omitempty"`
	Scheduled    *ScheduledConfig         `json:"scheduled,omitempty"`
	Adaptive     *AdaptiveRoutingConfig   `json:"adaptive,omitempty"`
	ProviderLock *ProviderLockConfig      `json:"provider_lock,omitempty"`
	AccessGroups map[string]*AccessGroup  `json:"access_groups,omitempty"`

	// Reliability features
	Failover       *FailoverConfig          `json:"failover,omitempty"`
	CircuitBreaker *CircuitBreakerConfig    `json:"circuit_breaker,omitempty"`
	Retry          *RetryConfig             `json:"retry,omitempty"`
	Mirror         *MirrorConfig            `json:"mirror,omitempty"`
	ModelTimeouts  map[string]string        `json:"model_timeouts,omitempty"` // model -> duration string e.g. "30s"
}

// ConditionalRoute defines a metadata-based routing rule.
type ConditionalRoute struct {
	Field    string      `json:"field"`
	Operator string      `json:"operator"` // "$eq", "$ne", "$in", "$regex", etc.
	Value    interface{} `json:"value"`
	Target   string      `json:"target"`
}

// ComplexityRoutingConfig routes requests to different models based on prompt complexity.
type ComplexityRoutingConfig struct {
	Enabled     bool                           `json:"enabled"`
	DefaultTier string                         `json:"default_tier,omitempty"`
	Tiers       map[string]*ComplexityTier     `json:"tiers,omitempty"`
	Weights     map[string]float64             `json:"weights,omitempty"` // signal weights for scoring
}

// ComplexityTier maps a complexity tier to a model/provider.
type ComplexityTier struct {
	MaxScore int    `json:"max_score"`
	Model    string `json:"model"`
	Provider string `json:"provider,omitempty"`
}

// FastestResponseConfig sends requests to multiple providers and returns the first response.
type FastestResponseConfig struct {
	Enabled           bool     `json:"enabled"`
	MaxConcurrent     int      `json:"max_concurrent,omitempty"`
	CancelDelay       string   `json:"cancel_delay,omitempty"` // duration string e.g. "100ms"
	ExcludedProviders []string `json:"excluded_providers,omitempty"`
}

// ScheduledConfig allows scheduling completion requests for future execution.
type ScheduledConfig struct {
	Enabled          bool   `json:"enabled"`
	MaxPendingJobs   int    `json:"max_pending_jobs,omitempty"`
	ResultTTL        string `json:"result_ttl,omitempty"`         // duration string
	MaxScheduleAhead string `json:"max_schedule_ahead,omitempty"` // duration string
	RetryAttempts    int    `json:"retry_attempts,omitempty"`
	WorkerCount      int    `json:"worker_count,omitempty"`
}

// AdaptiveRoutingConfig dynamically adjusts provider weights based on real-time performance.
type AdaptiveRoutingConfig struct {
	Enabled          bool                `json:"enabled"`
	LearningRequests int                 `json:"learning_requests,omitempty"`
	UpdateInterval   string              `json:"update_interval,omitempty"` // duration string
	SmoothingFactor  float64             `json:"smoothing_factor,omitempty"`
	MinWeight        float64             `json:"min_weight,omitempty"`
	SignalWeights    *SignalWeights      `json:"signal_weights,omitempty"`
}

// SignalWeights configures how different performance signals affect adaptive routing weights.
type SignalWeights struct {
	Latency   float64 `json:"latency"`
	ErrorRate float64 `json:"error_rate"`
	Cost      float64 `json:"cost"`
}

// ProviderLockConfig restricts which providers can be used for an org.
type ProviderLockConfig struct {
	Enabled          bool     `json:"enabled"`
	AllowedProviders []string `json:"allowed_providers,omitempty"`
	DenyProviders    []string `json:"deny_providers,omitempty"`
}

// AccessGroup defines a named group of models that can be assigned to API keys.
type AccessGroup struct {
	Models      []string          `json:"models,omitempty"`
	Description string            `json:"description,omitempty"`
	Aliases     map[string]string `json:"aliases,omitempty"`
}

// FailoverConfig controls provider failover behavior per org.
type FailoverConfig struct {
	Enabled           bool   `json:"enabled"`
	MaxAttempts       int    `json:"max_attempts,omitempty"`
	OnStatusCodes     []int  `json:"on_status_codes,omitempty"`
	OnTimeout         bool   `json:"on_timeout,omitempty"`
	PerAttemptTimeout string `json:"per_attempt_timeout,omitempty"` // duration string e.g. "30s"
}

// CircuitBreakerConfig controls circuit breaker behavior per org.
type CircuitBreakerConfig struct {
	Enabled          bool   `json:"enabled"`
	FailureThreshold int    `json:"failure_threshold,omitempty"`
	SuccessThreshold int    `json:"success_threshold,omitempty"`
	Cooldown         string `json:"cooldown,omitempty"` // duration string e.g. "30s"
	OnStatusCodes    []int  `json:"on_status_codes,omitempty"`
}

// RetryConfig controls retry behavior per org.
type RetryConfig struct {
	Enabled       bool    `json:"enabled"`
	MaxRetries    int     `json:"max_retries,omitempty"`
	InitialDelay  string  `json:"initial_delay,omitempty"` // duration string
	MaxDelay      string  `json:"max_delay,omitempty"`     // duration string
	Multiplier    float64 `json:"multiplier,omitempty"`
	OnStatusCodes []int   `json:"on_status_codes,omitempty"`
	OnTimeout     bool    `json:"on_timeout,omitempty"`
}

// MirrorConfig controls traffic mirroring per org.
type MirrorConfig struct {
	Enabled bool          `json:"enabled"`
	Rules   []MirrorRule  `json:"rules,omitempty"`
}

// MirrorRule defines a single traffic mirroring rule.
type MirrorRule struct {
	SourceModel    string  `json:"source_model"`
	TargetProvider string  `json:"target_provider"`
	TargetModel    string  `json:"target_model"`
	SampleRate     float64 `json:"sample_rate"`
}

// CacheConfig holds per-org cache settings.
type CacheConfig struct {
	Enabled    bool              `json:"enabled"`
	Backend    string            `json:"backend,omitempty"`    // "memory", "disk", "redis", "s3", "azure-blob", "gcs"
	DefaultTTL int               `json:"default_ttl,omitempty"` // seconds
	MaxEntries int               `json:"max_entries,omitempty"`

	// L1 backend-specific configs
	Disk      *DiskCacheConfig      `json:"disk,omitempty"`
	Redis     *RedisCacheConfig     `json:"redis,omitempty"`
	S3        *S3CacheConfig        `json:"s3,omitempty"`
	AzureBlob *AzureBlobCacheConfig `json:"azure_blob,omitempty"`
	GCS       *GCSCacheConfig       `json:"gcs,omitempty"`

	// L2 semantic cache
	Semantic *SemanticCacheConfig `json:"semantic,omitempty"`

	// Edge caching
	Edge *EdgeCacheConfig `json:"edge,omitempty"`
}

// DiskCacheConfig holds disk-based cache settings.
type DiskCacheConfig struct {
	Directory       string `json:"directory,omitempty"`
	MaxSizeBytes    int64  `json:"max_size_bytes,omitempty"`
	Compress        bool   `json:"compress,omitempty"`
	CleanupInterval string `json:"cleanup_interval,omitempty"` // duration string
}

// RedisCacheConfig holds Redis cache settings.
type RedisCacheConfig struct {
	Address   string   `json:"address,omitempty"`
	Addresses []string `json:"addresses,omitempty"` // cluster/sentinel
	Password  string   `json:"password,omitempty"`
	DB        int      `json:"db,omitempty"`
	Mode      string   `json:"mode,omitempty"` // "single", "sentinel", "cluster"
	PoolSize  int      `json:"pool_size,omitempty"`
	TLS       bool     `json:"tls,omitempty"`
	KeyPrefix string   `json:"key_prefix,omitempty"`
	Compress  bool     `json:"compress,omitempty"`
}

// S3CacheConfig holds S3 cache settings.
type S3CacheConfig struct {
	Bucket         string `json:"bucket,omitempty"`
	Prefix         string `json:"prefix,omitempty"`
	Region         string `json:"region,omitempty"`
	AccessKeyID    string `json:"access_key_id,omitempty"`
	SecretAccessKey string `json:"secret_access_key,omitempty"`
	Compress       bool   `json:"compress,omitempty"`
}

// AzureBlobCacheConfig holds Azure Blob Storage cache settings.
type AzureBlobCacheConfig struct {
	Container        string `json:"container,omitempty"`
	Prefix           string `json:"prefix,omitempty"`
	ConnectionString string `json:"connection_string,omitempty"`
	SASToken         string `json:"sas_token,omitempty"`
	Compress         bool   `json:"compress,omitempty"`
}

// GCSCacheConfig holds Google Cloud Storage cache settings.
type GCSCacheConfig struct {
	Bucket          string `json:"bucket,omitempty"`
	Prefix          string `json:"prefix,omitempty"`
	Project         string `json:"project,omitempty"`
	CredentialsFile string `json:"credentials_file,omitempty"`
	Compress        bool   `json:"compress,omitempty"`
}

// SemanticCacheConfig holds L2 semantic/vector cache settings.
type SemanticCacheConfig struct {
	Enabled    bool              `json:"enabled"`
	Backend    string            `json:"backend,omitempty"` // "qdrant", "weaviate", "pinecone"
	Threshold  float64           `json:"threshold,omitempty"`
	Dimensions int               `json:"dimensions,omitempty"`
	MaxEntries int               `json:"max_entries,omitempty"`
	Qdrant     *QdrantConfig     `json:"qdrant,omitempty"`
	Weaviate   *WeaviateConfig   `json:"weaviate,omitempty"`
	Pinecone   *PineconeConfig   `json:"pinecone,omitempty"`
}

// QdrantConfig holds Qdrant vector DB settings.
type QdrantConfig struct {
	URL        string `json:"url,omitempty"`
	Collection string `json:"collection,omitempty"`
	APIKey     string `json:"api_key,omitempty"`
}

// WeaviateConfig holds Weaviate vector DB settings.
type WeaviateConfig struct {
	URL    string `json:"url,omitempty"`
	Class  string `json:"class,omitempty"`
	APIKey string `json:"api_key,omitempty"`
}

// PineconeConfig holds Pinecone vector DB settings.
type PineconeConfig struct {
	URL    string `json:"url,omitempty"`
	APIKey string `json:"api_key,omitempty"`
}

// EdgeCacheConfig holds CDN edge caching settings.
type EdgeCacheConfig struct {
	Enabled         bool     `json:"enabled"`
	DefaultTTL      int      `json:"default_ttl,omitempty"` // seconds
	MaxSize         int      `json:"max_size,omitempty"`    // bytes
	CacheableModels []string `json:"cacheable_models,omitempty"`
	RequireOptIn    bool     `json:"require_opt_in,omitempty"`
}

// RateLimitConfig holds per-org rate limiting settings.
type RateLimitConfig struct {
	Enabled     bool `json:"enabled"`
	GlobalRPM   int  `json:"global_rpm,omitempty"`
	GlobalTPM   int  `json:"global_tpm,omitempty"`
	PerKeyRPM   int  `json:"per_key_rpm,omitempty"`
	PerKeyTPM   int  `json:"per_key_tpm,omitempty"`
	PerModelRPM int  `json:"per_model_rpm,omitempty"`
	PerUserRPM  int  `json:"per_user_rpm,omitempty"`
}

// BudgetsConfig holds per-org spend budget settings with hierarchical levels.
type BudgetsConfig struct {
	Enabled       bool    `json:"enabled"`
	DefaultPeriod string  `json:"default_period,omitempty"` // "daily", "weekly", "monthly", "total"
	WarnThreshold float64 `json:"warn_threshold,omitempty"` // 0-1, e.g. 0.8 = 80%
	OrgLimit      float64 `json:"org_limit,omitempty"`      // org-wide spend limit in USD (flat, backward compat)
	OrgPeriod     string  `json:"org_period,omitempty"`     // overrides default_period for org
	HardLimit     bool    `json:"hard_limit,omitempty"`     // block when exceeded vs warn

	// Hierarchical budget levels.
	Teams map[string]*BudgetLevelConfig `json:"teams,omitempty"`
	Users map[string]*BudgetLevelConfig `json:"users,omitempty"`
	Keys  map[string]*BudgetLevelConfig `json:"keys,omitempty"`
	Tags  map[string]*BudgetLevelConfig `json:"tags,omitempty"`
}

// BudgetLevelConfig defines a spend budget at any hierarchy level.
type BudgetLevelConfig struct {
	Limit    float64            `json:"limit"`
	Period   string             `json:"period,omitempty"`     // overrides default_period
	Hard     *bool              `json:"hard,omitempty"`       // nil = default true
	PerModel map[string]float64 `json:"per_model,omitempty"`
}

// CostTrackingConfig holds per-org cost tracking settings.
type CostTrackingConfig struct {
	Enabled       bool                      `json:"enabled"`
	CustomPricing map[string]*CustomPricing `json:"custom_pricing,omitempty"` // model -> pricing override
}

// CustomPricing holds custom per-model pricing for an org.
type CustomPricing struct {
	InputPerMTok  float64 `json:"input_per_mtok"`
	OutputPerMTok float64 `json:"output_per_mtok"`
}

// IPACLConfig holds per-org IP access control settings.
type IPACLConfig struct {
	Enabled bool     `json:"enabled"`
	Allow   []string `json:"allow,omitempty"` // allowed IPs/CIDRs
	Deny    []string `json:"deny,omitempty"`  // denied IPs/CIDRs
}

// AlertingConfig holds per-org alerting settings.
type AlertingConfig struct {
	Enabled  bool                `json:"enabled"`
	Rules    []*AlertRuleConfig  `json:"rules,omitempty"`
	Channels []*AlertChannelConfig `json:"channels,omitempty"`
}

// AlertRuleConfig configures a single alert rule for an org.
type AlertRuleConfig struct {
	Name      string  `json:"name"`
	Metric    string  `json:"metric"`              // error_count, request_count, cost_total, latency_avg, tokens_total
	Condition string  `json:"condition"`            // ">=", ">", "<=", "<", "=="
	Threshold float64 `json:"threshold"`
	Window    string  `json:"window,omitempty"`     // duration string e.g. "5m"
	Cooldown  string  `json:"cooldown,omitempty"`   // duration string e.g. "15m"
	Channels  []string `json:"channels,omitempty"`  // channel names to notify
}

// AlertChannelConfig configures an alert notification channel.
type AlertChannelConfig struct {
	Name    string            `json:"name"`
	Type    string            `json:"type"`              // "webhook", "slack", "log"
	URL     string            `json:"url,omitempty"`
	Headers map[string]string `json:"headers,omitempty"`
}

// PrivacyConfig holds per-org PII redaction settings.
type PrivacyConfig struct {
	Enabled  bool                    `json:"enabled"`
	Mode     string                  `json:"mode,omitempty"` // "full", "patterns", "none"
	Patterns []*RedactPatternConfig  `json:"redact_patterns,omitempty"`
}

// RedactPatternConfig defines a named regex pattern for PII redaction.
type RedactPatternConfig struct {
	Name    string `json:"name"`
	Pattern string `json:"pattern"`
}

// ToolPolicyConfig holds per-org tool/function calling policy.
type ToolPolicyConfig struct {
	Enabled            bool     `json:"enabled"`
	MaxToolsPerRequest int      `json:"max_tools_per_request,omitempty"`
	DefaultAction      string   `json:"default_action,omitempty"` // "strip" or "reject"
	Allow              []string `json:"allow,omitempty"`
	Deny               []string `json:"deny,omitempty"`
}

// MCPOrgConfig holds per-org MCP (Model Context Protocol) settings.
type MCPOrgConfig struct {
	Enabled    bool                          `json:"enabled"`
	Servers    map[string]*MCPServerOrgConfig `json:"servers,omitempty"`
	Guardrails *MCPGuardrailOrgConfig        `json:"guardrails,omitempty"`
}

// MCPServerOrgConfig configures a single upstream MCP server for an org.
type MCPServerOrgConfig struct {
	URL           string            `json:"url,omitempty"`
	Transport     string            `json:"transport,omitempty"` // "http" or "stdio"
	Auth          *MCPAuthOrgConfig `json:"auth,omitempty"`
	ToolsCacheTTL string            `json:"tools_cache_ttl,omitempty"` // duration string e.g. "5m"
}

// MCPAuthOrgConfig holds auth settings for an upstream MCP server.
type MCPAuthOrgConfig struct {
	Type   string `json:"type,omitempty"`   // "bearer", "api_key", "none"
	Token  string `json:"token,omitempty"`  // for bearer
	Header string `json:"header,omitempty"` // for api_key
	Key    string `json:"key,omitempty"`    // for api_key
}

// MCPGuardrailOrgConfig controls per-org MCP tool call guardrails.
type MCPGuardrailOrgConfig struct {
	Enabled         bool           `json:"enabled"`
	BlockedTools    []string       `json:"blocked_tools,omitempty"`
	AllowedServers  []string       `json:"allowed_servers,omitempty"`
	ValidateInputs  bool           `json:"validate_inputs"`
	ValidateOutputs bool           `json:"validate_outputs"`
	ToolRateLimits  map[string]int `json:"tool_rate_limits,omitempty"`
}

// AuditOrgConfig holds per-org audit logging settings.
type AuditOrgConfig struct {
	Enabled     bool               `json:"enabled"`
	MinSeverity string             `json:"min_severity,omitempty"` // info, warn, error, critical
	Categories  []string           `json:"categories,omitempty"`
	Sinks       []*AuditSinkConfig `json:"sinks,omitempty"`
}

// AuditSinkConfig defines an audit log sink for an org.
type AuditSinkConfig struct {
	Type    string            `json:"type"`              // stdout, file, webhook
	Path    string            `json:"path,omitempty"`    // for file sink
	URL     string            `json:"url,omitempty"`     // for webhook sink
	Headers map[string]string `json:"headers,omitempty"` // for webhook sink
}

// A2AOrgConfig holds per-org A2A (Agent-to-Agent) protocol settings.
type A2AOrgConfig struct {
	Enabled bool                        `json:"enabled"`
	Card    *A2ACardConfig              `json:"card,omitempty"`
	Agents  map[string]*A2AAgentConfig  `json:"agents,omitempty"`
}

// A2ACardConfig holds per-org agent card customization.
type A2ACardConfig struct {
	Name        string `json:"name,omitempty"`
	Description string `json:"description,omitempty"`
	Version     string `json:"version,omitempty"`
}

// A2AAgentConfig configures a single external A2A agent per-org.
type A2AAgentConfig struct {
	URL         string            `json:"url"`
	Auth        *A2AAuthConfig    `json:"auth,omitempty"`
	Description string            `json:"description,omitempty"`
	Skills      []*A2ASkillConfig `json:"skills,omitempty"`
}

// A2AAuthConfig holds auth settings for an external A2A agent.
type A2AAuthConfig struct {
	Type   string `json:"type,omitempty"`   // "bearer", "api_key", "none"
	Token  string `json:"token,omitempty"`
	Header string `json:"header,omitempty"`
}

// A2ASkillConfig defines a skill of an external A2A agent.
type A2ASkillConfig struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
}

// ModelDatabaseOrgConfig holds per-org model database overrides.
type ModelDatabaseOrgConfig struct {
	Overrides map[string]*ModelOverrideOrgConfig `json:"overrides,omitempty"`
}

// ModelOverrideOrgConfig allows overriding model metadata per-org.
// Pointer fields are optional — nil means "don't override".
type ModelOverrideOrgConfig struct {
	MaxInputTokens  *int                   `json:"max_input_tokens,omitempty"`
	MaxOutputTokens *int                   `json:"max_output_tokens,omitempty"`
	Pricing         *PricingOverrideOrg    `json:"pricing,omitempty"`
	Capabilities    *CapabilityOverrideOrg `json:"capabilities,omitempty"`
}

// PricingOverrideOrg allows overriding per-token pricing per-org.
type PricingOverrideOrg struct {
	InputPerToken       *float64 `json:"input_per_token,omitempty"`
	OutputPerToken      *float64 `json:"output_per_token,omitempty"`
	CachedInputPerToken *float64 `json:"cached_input_per_token,omitempty"`
	BatchInputPerToken  *float64 `json:"batch_input_per_token,omitempty"`
	BatchOutputPerToken *float64 `json:"batch_output_per_token,omitempty"`
}

// CapabilityOverrideOrg allows overriding capability flags per-org.
type CapabilityOverrideOrg struct {
	FunctionCalling   *bool `json:"function_calling,omitempty"`
	ParallelToolCalls *bool `json:"parallel_tool_calls,omitempty"`
	Vision            *bool `json:"vision,omitempty"`
	AudioInput        *bool `json:"audio_input,omitempty"`
	AudioOutput       *bool `json:"audio_output,omitempty"`
	PDFInput          *bool `json:"pdf_input,omitempty"`
	Streaming         *bool `json:"streaming,omitempty"`
	ResponseSchema    *bool `json:"response_schema,omitempty"`
	SystemMessages    *bool `json:"system_messages,omitempty"`
	PromptCaching     *bool `json:"prompt_caching,omitempty"`
	Reasoning         *bool `json:"reasoning,omitempty"`
}
