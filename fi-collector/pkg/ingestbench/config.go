// Package ingestbench measures OTLP acknowledgement separately from ClickHouse visibility.
// It never inserts directly into ClickHouse or retries an ambiguous OTLP export.
package ingestbench

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"math"
	"math/bits"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"
)

// SchemaVersion changes whenever the meaning of a counter changes. v2 split
// whole-request rejections and ambiguous failures out of "unacknowledged",
// decomposed missing spans by acknowledgement outcome and added provenance. v3
// measures read-visible lag with sentinels timed from the SCHEDULED send (a
// lower and an upper bound), adds the server-side insert lag of every stored
// span, the collector backlog series, request latency from the scheduled send,
// missing-span ranges by request and recovery from injected or declared faults.
const SchemaVersion = "fi.otlpbench.v4"

// Wire encodings of the measured path. protobuf and grpc are what production SDK
// exporters use; json is kept for browser-style clients and debugging.
const (
	EncodingJSON     = "json"
	EncodingProtobuf = "protobuf"
	EncodingGRPC     = "grpc"
)

// Config contains no credentials. Header values are supplied separately at runtime.
type Config struct {
	RunID            string        `json:"run_id"`
	Endpoint         string        `json:"endpoint"`
	ClickHouseURL    string        `json:"clickhouse_url"`
	Database         string        `json:"database"`
	Table            string        `json:"table"`
	ProjectID        string        `json:"project_id"`
	ProjectName      string        `json:"project_name"`
	Profile          string        `json:"profile"`
	Payload          string        `json:"payload"`
	Rate             int64         `json:"target_spans_per_second"`
	Duration         time.Duration `json:"duration_ns"`
	MaxDuration      time.Duration `json:"max_duration_ns"`
	MaxBytes         int64         `json:"max_otlp_bytes"`
	BatchSize        int           `json:"batch_size"`
	Concurrency      int           `json:"concurrency"`
	SpansPerTrace    int64         `json:"spans_per_trace"`
	TracesPerSession int64         `json:"traces_per_session"`
	Timeout          time.Duration `json:"request_timeout_ns"`
	Settle           time.Duration `json:"settle_ns"`
	PollInterval     time.Duration `json:"poll_interval_ns"`
	VerifyChunk      int64         `json:"verification_chunk_spans"`
	Execute          bool          `json:"execute"`
	AllowHosts       []string      `json:"allow_hosts"`
	ConfirmRemote    string        `json:"-"`
	ConfirmLoad      string        `json:"-"`
	AllowUnauth      bool          `json:"allow_unauthenticated_fixture"`
	Environment      string        `json:"environment"`
	Topology         string        `json:"topology"`
	Revision         string        `json:"revision"`
	ReadProbes       []ReadProbe   `json:"read_probes,omitempty"`
	Nodes            []Node        `json:"nodes,omitempty"`
	Containers       []string      `json:"containers,omitempty"`

	// Wire path. Encoding is json, protobuf or grpc; Gzip compresses the request
	// body (HTTP) or message (gRPC). Both are recorded in the report.
	Encoding string `json:"encoding"`
	Gzip     bool   `json:"gzip"`

	// Synthetic workload model (see payload.go). The same seed gives the same
	// bytes for the same sequence number and timestamp.
	Seed                  int64   `json:"seed"`
	ErrorRate             float64 `json:"error_rate"`
	TreeDepth             int     `json:"tree_depth"`
	FanOut                int     `json:"fan_out"`
	CohortVariantFraction float64 `json:"cohort_variant_fraction"`
	BytesPerToken         float64 `json:"bytes_per_token"`
	TextPoolBlocks        int     `json:"text_pool_blocks"`

	// Verification budget. Zero means derive a default from the run size.
	// VerifyTimeout bounds the exact-ID verification; the durable lag's extra
	// query per chunk has its own budget (DurableLagTimeout, by default the
	// same size) and never spends the exact-ID verification's.
	VerifyTimeout      time.Duration `json:"verify_timeout_ns"`
	DurableLagTimeout  time.Duration `json:"durable_lag_timeout_ns"`
	VerifyQueryTimeout time.Duration `json:"verify_query_timeout_ns"`
	VerifyQueryRetries int           `json:"verify_query_retries"`
	VerifyMaxMemory    int64         `json:"verify_query_max_memory_bytes"`
	// VerifyCheckpoint is a local path, deliberately not recorded in results.
	VerifyCheckpoint string `json:"-"`

	// The collector under test. The admin /healthz URL, when given, is read for the
	// build and admission limits; a declared row budget is used when it is not.
	CollectorHealthURL      string `json:"collector_health_url,omitempty"`
	CollectorMaxPendingRows int64  `json:"declared_collector_max_pending_rows,omitempty"`
	// CollectorPollInterval is how often /healthz is sampled for the backlog
	// series while the run goes on (only with CollectorHealthURL).
	CollectorPollInterval time.Duration `json:"collector_poll_interval_ns"`

	// MaxSentinels caps the read-visible lag sentinels: the first span of every
	// k-th request, k chosen so the run has at most this many (sentinel.go).
	MaxSentinels int `json:"max_sentinels"`
	// DurableLag adds one query per verification chunk that measures, for every
	// stored span, the server's insert timestamp (created_at) minus the
	// scheduled send of its request.
	DurableLag bool `json:"durable_lag"`
	// DeclaredFaults are disruptions an operator performs outside the tool
	// (for example stopping ClickHouse of an isolated stack). They are recorded
	// as declared, not observed, and analysed like injected ones.
	DeclaredFaults []DeclaredFault `json:"declared_faults,omitempty"`

	DirectProbes DirectProbeConfig `json:"direct_probes"`

	// GeneratorRevision is the git revision of this benchmark checkout; Revision
	// (above) is the revision of the SYSTEM UNDER TEST. They are different things.
	GeneratorRevision string `json:"generator_revision,omitempty"`
	SUTNotes          string `json:"system_under_test_notes,omitempty"`
}

func DefaultConfig() Config {
	return Config{
		Endpoint: "http://127.0.0.1:4318/v1/traces", ClickHouseURL: "http://127.0.0.1:8123",
		Database: "default", Table: "spans", Profile: "smoke", Payload: "mixed",
		Rate: 100, Duration: 5 * time.Second, MaxDuration: time.Minute, MaxBytes: 16 << 20,
		BatchSize: 100, Concurrency: 2, SpansPerTrace: 8, TracesPerSession: 4,
		Timeout: 10 * time.Second, Settle: 15 * time.Second, PollInterval: 100 * time.Millisecond,
		CollectorPollInterval: time.Second, MaxSentinels: 40960, DurableLag: true,
		VerifyChunk: 65536, Environment: "unclassified", Topology: "single-node",
		Encoding: EncodingProtobuf, Seed: 8687, ErrorRate: 0.01, TreeDepth: 4, FanOut: 4,
		CohortVariantFraction: 0.5, BytesPerToken: 4, TextPoolBlocks: 1024,
		VerifyQueryRetries: 2,
		DirectProbes:       DirectProbeConfig{Enabled: true, Interval: 2 * time.Second, MinAge: 10 * time.Second, AfterSettleSamples: 10, QueryTimeout: 10 * time.Second},
	}
}

func NewRunID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}

var identifier = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)
var runIDPattern = regexp.MustCompile(`^[0-9a-f]{16}$`)

// PlannedSpans floors rate * duration exactly, including fractional seconds.
// -1 denotes an invalid or unrepresentable plan; Validate rejects it.
func (c Config) PlannedSpans() int64 {
	if c.Rate < 0 || c.Duration < 0 {
		return -1
	}
	hi, lo := bits.Mul64(uint64(c.Rate), uint64(c.Duration))
	if hi >= uint64(time.Second) {
		return -1
	}
	n, _ := bits.Div64(hi, lo, uint64(time.Second))
	if n > math.MaxInt64 {
		return -1
	}
	return int64(n)
}

// ValidateLoadBounds is shared by load planning and the offline budget command.
func ValidateLoadBounds(rate int64, batchSize, concurrency int) error {
	if rate < 1 || rate > 1000000 {
		return fmt.Errorf("--rate must be 1..1000000 spans/s")
	}
	if batchSize < 1 || batchSize > 10000 {
		return fmt.Errorf("--batch-size must be 1..10000")
	}
	if concurrency < 1 || concurrency > 128 {
		return fmt.Errorf("--concurrency must be 1..128")
	}
	return nil
}

func (c Config) Validate() error {
	if !runIDPattern.MatchString(c.RunID) || c.RunID == "0000000000000000" {
		return fmt.Errorf("run-id must be 16 lowercase hexadecimal characters, nonzero and unique per execution")
	}
	if err := ValidateLoadBounds(c.Rate, c.BatchSize, c.Concurrency); err != nil {
		return err
	}
	if c.Duration <= 0 || c.MaxDuration <= 0 || c.Duration > c.MaxDuration || c.MaxDuration > 24*time.Hour {
		return fmt.Errorf("rate must be 1..1000000 spans/s; duration <= max-duration <= 24h")
	}
	if c.PlannedSpans() < 1 || c.MaxBytes < 1 {
		return fmt.Errorf("positive span/byte budgets required")
	}
	if c.SpansPerTrace < 1 || c.SpansPerTrace > 10000 || c.TracesPerSession < 1 || c.TracesPerSession > 10000 {
		return fmt.Errorf("spans-per-trace and traces-per-session must be 1..10000")
	}
	if c.Timeout <= 0 || c.Timeout > time.Minute || c.Settle < 0 || c.Settle > 10*time.Minute || c.PollInterval < 100*time.Millisecond || c.VerifyChunk < 1 || c.VerifyChunk > 1000000 {
		return fmt.Errorf("invalid timeout, settle, poll interval or verification chunk")
	}
	if !identifier.MatchString(c.Database) || !identifier.MatchString(c.Table) {
		return fmt.Errorf("database and table must be SQL identifiers")
	}
	if c.Payload != "small" && c.Payload != "medium" && c.Payload != "large" && c.Payload != "mixed" {
		return fmt.Errorf("payload must be small, medium, large or mixed")
	}
	if c.Encoding != EncodingJSON && c.Encoding != EncodingProtobuf && c.Encoding != EncodingGRPC {
		return fmt.Errorf("encoding must be json, protobuf or grpc")
	}
	if c.Encoding == EncodingGRPC {
		if _, _, err := grpcTarget(c.Endpoint); err != nil {
			return err
		}
	}
	if !(c.ErrorRate >= 0 && c.ErrorRate <= 1) || !(c.CohortVariantFraction >= 0 && c.CohortVariantFraction <= 1) {
		return fmt.Errorf("error-rate and cohort-variant-fraction must be within 0..1")
	}
	if c.TreeDepth < 1 || c.TreeDepth > 64 || c.FanOut < 1 || c.FanOut > 64 || !(c.BytesPerToken >= 1 && c.BytesPerToken <= 16) || c.TextPoolBlocks < 1 || c.TextPoolBlocks > 65536 {
		return fmt.Errorf("tree-depth and fan-out must be 1..64, bytes-per-token 1..16 and text-pool-blocks 1..65536")
	}
	if c.VerifyTimeout < 0 || c.VerifyTimeout > 72*time.Hour || c.DurableLagTimeout < 0 || c.DurableLagTimeout > 72*time.Hour || c.VerifyQueryTimeout < 0 || c.VerifyQueryTimeout > time.Hour || c.VerifyQueryRetries < 0 || c.VerifyQueryRetries > 10 || (c.VerifyMaxMemory != 0 && (c.VerifyMaxMemory < 64<<20 || c.VerifyMaxMemory > 256<<30)) {
		return fmt.Errorf("verify-timeout and durable-lag-timeout <= 72h, verify-query-timeout <= 1h, verify-query-retries 0..10, verify-max-memory 0 (default) or 64MiB..256GiB")
	}
	if c.CollectorMaxPendingRows < 0 {
		return fmt.Errorf("declared collector max pending rows must not be negative")
	}
	if c.CollectorHealthURL != "" && (c.CollectorPollInterval < 100*time.Millisecond || c.CollectorPollInterval > time.Minute) {
		return fmt.Errorf("collector-poll-interval must be 100ms..1m")
	}
	if c.MaxSentinels < 1 || c.MaxSentinels > 1<<20 {
		return fmt.Errorf("max-sentinels must be 1..1048576")
	}
	if len(c.DeclaredFaults) > 16 {
		return fmt.Errorf("at most 16 declared faults")
	}
	for _, f := range c.DeclaredFaults {
		if err := validateFault(f.Name, f.At, f.Duration, c.Duration); err != nil {
			return err
		}
	}
	if err := c.DirectProbes.validate(); err != nil {
		return err
	}
	if (c.PlannedSpans()+int64(c.BatchSize)-1)/int64(c.BatchSize) > maxLedgerRequests {
		return fmt.Errorf("the plan needs more than %d export requests; raise batch-size", maxLedgerRequests)
	}
	// Bound allocation before constructing any OTLP request (including a
	// dry-run): the first batch's text sizes are computed without building it.
	var previewBytes int64
	for i := int64(0); i < int64(c.BatchSize); i++ {
		p := c.plan(i)
		previewBytes += int64(p.inputBytes+p.outputBytes) + 4096
	}
	if previewBytes > 16<<20 {
		return fmt.Errorf("batch-size/payload can exceed 16MiB; reduce batch-size")
	}
	if _, err := uuid.Parse(c.ProjectID); err != nil || c.ProjectName == "" {
		return fmt.Errorf("project-id UUID and project-name are required (including dry runs)")
	}
	if c.Execute && (c.Rate > 1000 || c.Duration > time.Minute || c.MaxBytes > 64<<20) && c.ConfirmLoad != c.RunID {
		return fmt.Errorf("load above smoke limits requires --confirm-load matching --run-id")
	}
	endpoints := []string{c.Endpoint, c.ClickHouseURL}
	if c.CollectorHealthURL != "" {
		endpoints = append(endpoints, c.CollectorHealthURL)
	}
	for _, p := range c.ReadProbes {
		if err := p.validate(); err != nil {
			return err
		}
		endpoints = append(endpoints, p.URL)
	}
	for _, n := range c.Nodes {
		if n.Name == "" {
			return fmt.Errorf("node name is required")
		}
		endpoints = append(endpoints, n.URL)
	}
	if len(c.ReadProbes) > 4 || len(c.Nodes) > 32 || len(c.Containers) > 32 {
		return fmt.Errorf("at most four application probes and 32 resource nodes/containers")
	}
	seen := map[string]bool{}
	for _, p := range c.ReadProbes {
		if seen[p.Name] {
			return fmt.Errorf("duplicate read probe category %q", p.Name)
		}
		seen[p.Name] = true
	}
	for _, raw := range endpoints {
		if err := c.validateURL(raw); err != nil {
			return err
		}
	}
	return nil
}

func (c Config) validateURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.Fragment != "" {
		return fmt.Errorf("endpoints must be HTTP(S), without userinfo or fragments; credentials belong in environment variables")
	}
	for key := range u.Query() {
		k := strings.ToLower(key)
		if strings.Contains(k, "key") || strings.Contains(k, "secret") || strings.Contains(k, "token") || strings.Contains(k, "password") {
			return fmt.Errorf("credential-like URL parameters are prohibited")
		}
	}
	if localHost(u.Hostname()) {
		return nil
	}
	allowed := false
	for _, host := range c.AllowHosts {
		allowed = allowed || strings.EqualFold(host, u.Hostname())
	}
	if !allowed || (c.Execute && c.ConfirmRemote != c.RunID) {
		return fmt.Errorf("non-loopback endpoints need an exact --allow-host and --confirm-remote matching --run-id")
	}
	return nil
}

func localHost(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// Disable proxies/redirects so neither an environment proxy nor an HTTP redirect
// can forward load or credentials to a host that was not approved.
func (c Config) client() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.MaxIdleConnsPerHost = c.Concurrency + 8
	dialer := &net.Dialer{Timeout: c.Timeout}
	transport.DialContext = func(ctx context.Context, network, addr string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(addr)
		if err != nil {
			return nil, fmt.Errorf("invalid target address")
		}
		if strings.EqualFold(host, "localhost") {
			addr = net.JoinHostPort("127.0.0.1", port)
		}
		return dialer.DialContext(ctx, network, addr)
	}
	return &http.Client{Transport: transport, Timeout: c.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}

// VerifyBudget is the verification time budget for a run of this plan: the
// exact-ID budget plus, with the durable lag, its own budget.
func (c Config) VerifyBudget() time.Duration {
	b := c.effectiveVerifyTimeout(c.PlannedSpans())
	if c.DurableLag {
		b += c.effectiveDurableLagTimeout(c.PlannedSpans())
	}
	return b
}

// DirectProbeBudget bounds the after-settle direct probe rounds.
func (c Config) DirectProbeBudget() time.Duration {
	if !c.DirectProbes.Enabled {
		return 0
	}
	perQuery := c.DirectProbes.QueryTimeout
	if perQuery == 0 {
		perQuery = 10 * time.Second
	}
	return time.Duration(c.DirectProbes.AfterSettleSamples*len(directProbes)) * (perQuery + 10*time.Second)
}

// CheckURL applies the endpoint safety rules (loopback or an approved host,
// no credentials in the URL) to one URL.
func (c Config) CheckURL(raw string) error { return c.validateURL(raw) }

// HTTPClient is the proxy-free, redirect-free client the benchmark uses.
func (c Config) HTTPClient() *http.Client { return c.client() }
