package ingestbench

import (
	"context"
	"encoding/json"
	"fmt"
	"math/big"
	"net/http"
	"strings"
	"time"
)

// CollectorLimits mirrors the "limits" object of the collector's /healthz.
type CollectorLimits struct {
	// AdmissionModel distinguishes current byte leases from historical row receipts.
	AdmissionModel    string  `json:"admission_model,omitempty"`
	IngestBudgetBytes int64   `json:"ingest_budget_bytes,omitempty"`
	WirePoolBytes     int64   `json:"wire_pool_bytes,omitempty"`
	WorkPoolBytes     int64   `json:"work_pool_bytes,omitempty"`
	GRPCMaxRecvBytes  int64   `json:"grpc_max_recv_bytes,omitempty"`
	MaxWaitMS         int64   `json:"max_wait_ms,omitempty"`
	InsertConcurrency int     `json:"insert_concurrency,omitempty"`
	GRPCGzip          bool    `json:"grpc_gzip,omitempty"`
	MaxPendingRows    int64   `json:"max_pending_rows"`
	BatchMaxRows      int64   `json:"batch_max_rows"`
	BatchMaxAgeMS     int64   `json:"batch_max_age_ms"`
	GRPCMaxRecvMiB    float64 `json:"grpc_max_recv_mib"`
	MaxHTTPBodyBytes  int64   `json:"max_http_body_bytes"`
}

// WriterPolicy mirrors the "writer" object of the collector's /healthz.
type WriterPolicy struct {
	Table            string `json:"table"`
	InsertSettings   string `json:"insert_settings"`
	MaxRetries       int    `json:"max_retries"`
	InitialBackoffMS int64  `json:"initial_backoff_ms"`
	MaxBackoffMS     int64  `json:"max_backoff_ms"`
	RequestTimeoutMS int64  `json:"request_timeout_ms"`
	DeadLetter       bool   `json:"dead_letter_after_retries"`
}

// Historical row-collector defaults for unobserved legacy plans, not current
// byte-budget defaults and not a measured target configuration.
var collectorDefaultLimits = CollectorLimits{MaxPendingRows: 0, BatchMaxRows: 5000, BatchMaxAgeMS: 5000, GRPCMaxRecvMiB: 16, MaxHTTPBodyBytes: 16 << 20}

// CollectorInfo is what the run knows about the collector under test.
type CollectorInfo struct {
	HealthURL string           `json:"health_url,omitempty"`
	Status    string           `json:"health_status,omitempty"`
	Build     map[string]any   `json:"build,omitempty"`
	Runtime   map[string]any   `json:"runtime,omitempty"`
	Limits    *CollectorLimits `json:"limits,omitempty"`
	// Writer is the collector writer's insert mode and retry policy as its
	// /healthz reports them: what an acknowledged insert meant for the run.
	Ingest                 json.RawMessage `json:"ingest,omitempty"`
	Writer                 *WriterPolicy   `json:"writer,omitempty"`
	DeclaredMaxPendingRows int64           `json:"declared_max_pending_rows,omitempty"`
	Budget                 BudgetCheck     `json:"budget_check"`
	Error                  string          `json:"error,omitempty"`
}

// BudgetCheck compares the collector's pending-row budget with what the load
// profile needs. The requirement is a HEURISTIC, not a measurement: rows that
// wait for the next flush trigger, rows arriving during a two-second insert
// allowance, and every in-flight request arriving at once.
type BudgetCheck struct {
	AdmissionModel       string `json:"admission_model,omitempty"`
	Assessment           string `json:"assessment,omitempty"`
	Method               string `json:"method"`
	RequiredPendingRows  int64  `json:"required_pending_rows"`
	AvailablePendingRows int64  `json:"available_pending_rows"`
	Source               string `json:"available_source"`
	Sufficient           bool   `json:"sufficient"`
	BatchFits            bool   `json:"batch_fits_budget"`
	RecommendedSetting   string `json:"recommended_setting"`
	Error                string `json:"error,omitempty"`
}

const insertAllowance = 2 * time.Second

// RequiredPendingRows is the heuristic row budget, or -1 for invalid inputs.
func RequiredPendingRows(rate int64, batchSize, concurrency int, limits CollectorLimits) int64 {
	n, err := requiredPendingRows(rate, batchSize, concurrency, limits)
	if err != nil {
		return -1
	}
	return n
}

func requiredPendingRows(rate int64, batchSize, concurrency int, limits CollectorLimits) (int64, error) {
	if err := ValidateLoadBounds(rate, batchSize, concurrency); err != nil {
		return 0, err
	}
	if limits.BatchMaxAgeMS < 0 || limits.BatchMaxRows < 0 || limits.MaxPendingRows < 0 {
		return 0, fmt.Errorf("collector limits batch_max_age_ms, batch_max_rows and max_pending_rows must be non-negative")
	}
	// The reported flush age is not bounded by the CLI. Keep multiplication,
	// ceiling and addition wide until representability has been checked.
	trigger := new(big.Int).Mul(big.NewInt(rate), big.NewInt(limits.BatchMaxAgeMS))
	trigger.Add(trigger, big.NewInt(999)).Quo(trigger, big.NewInt(1000))
	if limits.BatchMaxRows > 0 && trigger.Cmp(big.NewInt(limits.BatchMaxRows)) > 0 {
		trigger.SetInt64(limits.BatchMaxRows)
	}
	trigger.Add(trigger, big.NewInt(rate*int64(insertAllowance/time.Second)))
	trigger.Add(trigger, big.NewInt(int64(concurrency)*int64(batchSize)))
	if !trigger.IsInt64() {
		return 0, fmt.Errorf("collector batch_max_age_ms produces an unrepresentable required_pending_rows")
	}
	return trigger.Int64(), nil
}

// CheckBudget fills a BudgetCheck from the reported limits, or from a declared
// budget for legacy collectors. Byte models are observed but never sized by rows.
func CheckBudget(rate int64, batchSize, concurrency int, reported *CollectorLimits, declared int64) BudgetCheck {
	if reported != nil && reported.AdmissionModel == "byte-leases-v1" {
		b := BudgetCheck{AdmissionModel: reported.AdmissionModel, Assessment: "not-assessed", Source: "collector /healthz", Method: "observed byte leases; row-count sizing does not establish byte-budget sufficiency", RecommendedSetting: "measure representative wire/work lease pressure and exporter deadlines; tune FI_INGEST_BUDGET_MIB and batch sizes on an isolated target"}
		if err := ValidateLoadBounds(rate, batchSize, concurrency); err != nil {
			b.Error = err.Error()
		}
		if err := validateByteLimits(*reported); err != nil {
			b.Error = err.Error()
		}
		return b
	}
	if reported != nil && reported.AdmissionModel != "" {
		return BudgetCheck{AdmissionModel: reported.AdmissionModel, Assessment: "not-assessed", Source: "collector /healthz", Error: "unknown collector admission model; cannot assess budget"}
	}
	limits, source := collectorDefaultLimits, "collector default (NOT observed; pass --collector-health-url)"
	if reported != nil {
		limits, source = *reported, "collector /healthz"
	} else if declared > 0 {
		limits.MaxPendingRows, source = declared, "declared by operator (not observed)"
	}
	b := BudgetCheck{AdmissionModel: "legacy-row-count", Assessment: "heuristic-only", Method: "heuristic: min(batch_max_rows, rate*batch_max_age) + rate*2s insert allowance + concurrency*batch_size",
		AvailablePendingRows: limits.MaxPendingRows, Source: source}
	var err error
	b.RequiredPendingRows, err = requiredPendingRows(rate, batchSize, concurrency, limits)
	if err != nil {
		b.Error = err.Error()
		return b
	}
	b.Sufficient = b.AvailablePendingRows >= b.RequiredPendingRows
	b.BatchFits = b.AvailablePendingRows > 0 && int64(batchSize) <= b.AvailablePendingRows
	b.RecommendedSetting = fmt.Sprintf("legacy row collector only: pending budget >= %d rows; FI_MAX_PENDING_ROWS is obsolete on current byte-budget collectors", b.RequiredPendingRows)
	return b
}

// Warnings are the loud, human-readable consequences of a budget check.
func (b BudgetCheck) Warnings() []string {
	if b.Error != "" {
		return []string{"collector budget unavailable: " + b.Error}
	}
	if b.AdmissionModel == "byte-leases-v1" {
		return []string{"collector uses byte leases: row estimates cannot assess memory sufficiency or capacity; " + b.RecommendedSetting}
	}
	var out []string
	if b.AvailablePendingRows == 0 {
		out = append(out, fmt.Sprintf("legacy row cap unavailable or disabled (%s): byte admission is unknown, not unbounded memory proof. %s. Fetch current /healthz; this historical heuristic does not bound process memory", b.Source, b.RecommendedSetting))
	}
	if b.AvailablePendingRows > 0 && !b.BatchFits {
		out = append(out, fmt.Sprintf("batch size exceeds the collector budget of %d rows (%s): each request is admitted only while the collector's buffer is below the budget and is refused as retryable otherwise (collectors that predate this rule refuse it as non-retryable: HTTP 413 / gRPC INVALID_ARGUMENT)", b.AvailablePendingRows, b.Source))
	}
	if b.AvailablePendingRows > 0 && !b.Sufficient {
		out = append(out, fmt.Sprintf("collector pending-row budget %d (%s) is smaller than the %d this profile needs by the documented heuristic; expect 429/503 whole-request rejections. Set %s on the collector under test before a capacity run",
			b.AvailablePendingRows, b.Source, b.RequiredPendingRows, b.RecommendedSetting))
	}
	return out
}

// FetchCollectorHealth reads the collector's admin /healthz (read-only GET).
func FetchCollectorHealth(ctx context.Context, client *http.Client, healthURL string) CollectorInfo {
	info := CollectorInfo{HealthURL: healthURL}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, healthURL, nil)
	if err != nil {
		info.Error = "invalid health URL"
		return info
	}
	resp, err := client.Do(req)
	if err != nil {
		info.Error = "collector health request failed (URL omitted)"
		return info
	}
	defer resp.Body.Close()
	body, err := readBounded(resp.Body, 1<<20)
	if err != nil {
		info.Error = "collector health response unreadable or oversized"
		return info
	}
	var health struct {
		Ingest  json.RawMessage  `json:"ingest"`
		Status  string           `json:"status"`
		Build   map[string]any   `json:"build"`
		Runtime map[string]any   `json:"runtime"`
		Limits  *CollectorLimits `json:"limits"`
		Writer  *WriterPolicy    `json:"writer"`
	}
	if err := json.Unmarshal(body, &health); err != nil {
		info.Error = fmt.Sprintf("collector health HTTP %d is not JSON", resp.StatusCode)
		return info
	}
	info.Ingest = health.Ingest
	info.Status, info.Build, info.Runtime, info.Limits, info.Writer = health.Status, health.Build, health.Runtime, health.Limits, health.Writer
	if info.Limits == nil {
		info.Error = "collector /healthz does not report limits (collector predates limit reporting)"
	} else if info.Limits.AdmissionModel == "byte-leases-v1" {
		if err := validateByteLimits(*info.Limits); err != nil {
			// Keep the failed observation reportable, but do not promote impossible
			// health values into typed measurement evidence or publication disclosures.
			info.Error = "collector /healthz: " + err.Error()
			info.Limits = nil
		}
	}
	return info
}

// collectorRevision is the revision the collector binary reports, if any.
func collectorRevision(build map[string]any) string {
	if build == nil {
		return ""
	}
	if v, _ := build["configured_revision"].(string); v != "" {
		return v
	}
	v, _ := build["vcs_revision"].(string)
	if v != "" {
		if modified, _ := build["vcs_modified"].(string); modified == "true" {
			v += "-modified"
		}
	}
	return v
}

// revisionsMatch accepts an abbreviated SHA on either side.
func revisionsMatch(a, b string) bool {
	a, b = strings.TrimSuffix(strings.ToLower(a), "-modified"), strings.TrimSuffix(strings.ToLower(b), "-modified")
	if len(a) < 7 || len(b) < 7 {
		return a == b
	}
	return strings.HasPrefix(a, b) || strings.HasPrefix(b, a)
}
