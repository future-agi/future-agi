// Package ingestbench measures OTLP acknowledgement separately from ClickHouse visibility.
// It never inserts directly into ClickHouse or retries an ambiguous OTLP export.
package ingestbench

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"
)

const SchemaVersion = "fi.otlpbench.v1"

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
}

func DefaultConfig() Config {
	return Config{
		Endpoint: "http://127.0.0.1:4318/v1/traces", ClickHouseURL: "http://127.0.0.1:8123",
		Database: "default", Table: "spans", Profile: "smoke", Payload: "mixed",
		Rate: 100, Duration: 5 * time.Second, MaxDuration: time.Minute, MaxBytes: 16 << 20,
		BatchSize: 100, Concurrency: 2, SpansPerTrace: 8, TracesPerSession: 4,
		Timeout: 10 * time.Second, Settle: 15 * time.Second, PollInterval: time.Second,
		VerifyChunk: 65536, Environment: "unclassified", Topology: "single-node",
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

func (c Config) PlannedSpans() int64 {
	return int64(float64(c.Rate) * c.Duration.Seconds())
}

func (c Config) Validate() error {
	if !runIDPattern.MatchString(c.RunID) || c.RunID == "0000000000000000" {
		return fmt.Errorf("run-id must be 16 lowercase hexadecimal characters, nonzero and unique per execution")
	}
	if c.Rate < 1 || c.Rate > 1000000 || c.Duration <= 0 || c.MaxDuration <= 0 || c.Duration > c.MaxDuration || c.MaxDuration > 24*time.Hour {
		return fmt.Errorf("rate must be 1..1000000 spans/s; duration <= max-duration <= 24h")
	}
	if c.PlannedSpans() < 1 || c.MaxBytes < 1 || c.BatchSize < 1 || c.BatchSize > 10000 || c.Concurrency < 1 || c.Concurrency > 128 {
		return fmt.Errorf("positive span/byte budgets required; batch-size 1..10000 and concurrency 1..128")
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
	// Bound allocation before constructing any OTLP request (including a dry-run).
	var previewBytes int64
	for i := int64(0); i < int64(c.BatchSize); i++ {
		previewBytes += int64(len(c.payload(i))) + 4096
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
