package config

import (
	"fmt"
	"os"
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
	CORS          CORSConfig                `yaml:"cors" json:"cors"`
	Assistants    AssistantsConfig          `yaml:"assistants" json:"assistants"`
	MCP           MCPConfig                 `yaml:"mcp" json:"mcp"`
	A2A           A2AConfig                 `yaml:"a2a" json:"a2a"`
	Redis         RedisStateConfig          `yaml:"redis" json:"redis"`
}

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
	Enabled           bool          `yaml:"enabled" json:"enabled"`
	RunTimeout        time.Duration `yaml:"run_timeout" json:"run_timeout"`
	StreamReadTimeout time.Duration `yaml:"stream_read_timeout" json:"stream_read_timeout"`
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
}

// MCPAuthConfig holds auth settings for an upstream MCP server.
type MCPAuthConfig struct {
	Type   string `yaml:"type" json:"type"`     // "bearer", "api_key", "none"
	Token  string `yaml:"token" json:"token"`   // for bearer
	Header string `yaml:"header" json:"header"` // for api_key
	Key    string `yaml:"key" json:"key"`       // for api_key
}

// PrivacyConfig controls message redaction in logs.
type PrivacyConfig struct {
	Enabled  bool                  `yaml:"enabled" json:"enabled"`
	Mode     string                `yaml:"mode" json:"mode"` // "full", "patterns", "none"
	Patterns []RedactPatternConfig `yaml:"redact_patterns" json:"redact_patterns"`
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
	ReadTimeout           time.Duration `yaml:"read_timeout" json:"read_timeout"`
	WriteTimeout          time.Duration `yaml:"write_timeout" json:"write_timeout"`
	IdleTimeout           time.Duration `yaml:"idle_timeout" json:"idle_timeout"`
	ShutdownTimeout       time.Duration `yaml:"shutdown_timeout" json:"shutdown_timeout"`
	MaxRequestBodySize    int64         `yaml:"max_request_body_size" json:"max_request_body_size"`
	DefaultRequestTimeout time.Duration `yaml:"default_request_timeout" json:"default_request_timeout"`
}

type ProviderConfig struct {
	BaseURL        string            `yaml:"base_url" json:"base_url"`
	APIKey         string            `yaml:"api_key" json:"-"`
	APIFormat      string            `yaml:"api_format" json:"api_format"`
	DefaultTimeout time.Duration     `yaml:"default_timeout" json:"default_timeout"`
	MaxConcurrent  int               `yaml:"max_concurrent" json:"max_concurrent"`
	ConnPoolSize   int               `yaml:"conn_pool_size" json:"conn_pool_size"`
	Headers        map[string]string `yaml:"headers" json:"headers"`
	Models         []string          `yaml:"models" json:"models"`
	Priority       int               `yaml:"priority" json:"priority"`
	Weight         int               `yaml:"weight" json:"weight"`

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
	CredentialsFile string `yaml:"credentials_file" json:"-"`
	AWSSessionToken string `yaml:"aws_session_token" json:"-"`
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
	Enabled          bool                       `yaml:"enabled" json:"enabled"`
	CustomPricing    map[string]CustomPricing   `yaml:"custom_pricing" json:"custom_pricing"`
	AliasCostFactors map[string]float64         `yaml:"alias_cost_factors" json:"alias_cost_factors"`
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
	Reflexion      ReflexionConfig          `yaml:"reflexion" json:"reflexion"`
}

// ReflexionConfig enables guardrail reflexion: when a post-stage guardrail blocks
// the model's response, the block reason is injected back into the conversation and
// the model is re-called (bounded by MaxAttempts). Disabled by default.
type ReflexionConfig struct {
	Enabled          bool   `yaml:"enabled" json:"enabled"`
	MaxAttempts      int    `yaml:"max_attempts" json:"max_attempts"` // default 3; hard cap 5
	FeedbackTemplate string `yaml:"feedback_template" json:"feedback_template"`
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
	Exporter    string            `yaml:"exporter" json:"exporter"` // "stdout"
	SampleRate  float64           `yaml:"sample_rate" json:"sample_rate"`
	Attributes  map[string]string `yaml:"attributes" json:"attributes"`
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
	TrustedProxies int      `yaml:"trusted_proxies" json:"trusted_proxies"` // number of trusted proxy hops for X-Forwarded-For
	Allow          []string `yaml:"allow" json:"allow"`                     // allowed IPs/CIDRs (empty = all allowed)
	Deny           []string `yaml:"deny" json:"deny"`                       // denied IPs/CIDRs (takes precedence over allow)
}

// DefaultConfig returns sensible defaults.
func DefaultConfig() *Config {
	return &Config{
		Server: ServerConfig{
			Port:                  8080,
			Host:                  "0.0.0.0",
			ReadTimeout:           5 * time.Second,
			WriteTimeout:          300 * time.Second,
			IdleTimeout:           120 * time.Second,
			ShutdownTimeout:       30 * time.Second,
			MaxRequestBodySize:    50 * 1024 * 1024, // 50MB
			DefaultRequestTimeout: 60 * time.Second,
		},
		Providers:    make(map[string]ProviderConfig),
		ModelMap:     make(map[string]string),
		CostTracking: CostTrackingConfig{Enabled: true},
		RateLimiting: RateLimitConfig{Enabled: true},
		Cache: CacheConfig{
			Enabled:    false,
			DefaultTTL: 5 * time.Minute,
			MaxEntries: 10000,
		},
		Guardrails: GuardrailsConfig{
			Enabled:        false, // opt-in: set guardrails.enabled: true in YAML to activate
			FailOpen:       true,
			DefaultTimeout: 5 * time.Second,
			Reflexion: ReflexionConfig{
				// Enabled is false by default; MaxAttempts:3 is the default count used
				// when a deployment sets reflexion.enabled: true without specifying
				// max_attempts explicitly.
				MaxAttempts: 3,
			},
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
			Enabled:           true,
			RunTimeout:        300 * time.Second,
			StreamReadTimeout: 60 * time.Second,
		},
		MCP: MCPConfig{
			Enabled:         false,
			Endpoint:        "/mcp",
			MaxAgentDepth:   10,
			ToolCallTimeout: 30 * time.Second,
			SessionTTL:      30 * time.Minute,
			Separator:       "_",
		},
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
	// Expand ${VAR} and $VAR references from environment variables.
	expanded := os.ExpandEnv(string(data))
	return yaml.Unmarshal([]byte(expanded), cfg)
}

func loadFromEnv(cfg *Config) {
	if v := os.Getenv("AGENTCC_PORT"); v != "" {
		if port, err := strconv.Atoi(v); err == nil {
			cfg.Server.Port = port
		}
	}
	if v := os.Getenv("AGENTCC_HOST"); v != "" {
		cfg.Server.Host = v
	}
	if v := os.Getenv("AGENTCC_LOG_LEVEL"); v != "" {
		cfg.Logging.Level = v
	}
	if v := os.Getenv("AGENTCC_ADMIN_TOKEN"); v != "" {
		cfg.Admin.Token = v
	}
	if v := os.Getenv("AGENTCC_DEFAULT_REQUEST_TIMEOUT"); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			cfg.Server.DefaultRequestTimeout = d
		}
	}
	if v := os.Getenv("AGENTCC_SHUTDOWN_TIMEOUT"); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			cfg.Server.ShutdownTimeout = d
		}
	}

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
	if v := os.Getenv("AGENTCC_WEBHOOK_SECRET"); v != "" {
		cfg.ControlPlane.WebhookSecret = v
	}

	// Redis state env overrides.
	if v := os.Getenv("AGENTCC_REDIS_ADDRESS"); v != "" {
		cfg.Redis.Address = v
		cfg.Redis.Enabled = true
	}
	if v := os.Getenv("AGENTCC_REDIS_PASSWORD"); v != "" {
		cfg.Redis.Password = v
	}
	if v := os.Getenv("AGENTCC_REDIS_DB"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			cfg.Redis.DB = n
		}
	}
}

// Validate checks that the config is valid.
func (c *Config) Validate() error {
	if c.Server.Port < 1 || c.Server.Port > 65535 {
		return fmt.Errorf("server.port must be between 1 and 65535, got %d", c.Server.Port)
	}
	if c.Server.ReadTimeout <= 0 {
		return fmt.Errorf("server.read_timeout must be positive")
	}
	if c.Server.WriteTimeout <= 0 {
		return fmt.Errorf("server.write_timeout must be positive")
	}
	if c.Server.DefaultRequestTimeout <= 0 {
		return fmt.Errorf("server.default_request_timeout must be positive")
	}

	for name, p := range c.Providers {
		if p.BaseURL == "" {
			return fmt.Errorf("provider %q: base_url is required", name)
		}
		if p.APIFormat == "" {
			return fmt.Errorf("provider %q: api_format is required", name)
		}
	}

	validLevels := map[string]bool{"debug": true, "info": true, "warn": true, "error": true}
	if !validLevels[strings.ToLower(c.Logging.Level)] {
		return fmt.Errorf("logging.level must be one of debug, info, warn, error; got %q", c.Logging.Level)
	}

	return nil
}

// Addr returns the listen address.
func (c *Config) Addr() string {
	return fmt.Sprintf("%s:%d", c.Server.Host, c.Server.Port)
}
