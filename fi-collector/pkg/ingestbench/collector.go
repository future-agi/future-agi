package ingestbench

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"strings"
	"time"
)

// CollectorLimits mirrors the "limits" object of the collector's /healthz.
type CollectorLimits struct {
	MaxPendingRows   int64 `json:"max_pending_rows"`
	BatchMaxRows     int64 `json:"batch_max_rows"`
	BatchMaxAgeMS    int64 `json:"batch_max_age_ms"`
	GRPCMaxRecvMiB   int64 `json:"grpc_max_recv_mib"`
	MaxHTTPBodyBytes int64 `json:"max_http_body_bytes"`
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

// Defaults of the fi-collector when /healthz is not available to report them.
var collectorDefaultLimits = CollectorLimits{MaxPendingRows: 20000, BatchMaxRows: 5000, BatchMaxAgeMS: 5000, GRPCMaxRecvMiB: 16, MaxHTTPBodyBytes: 16 << 20}

// CollectorInfo is what the run knows about the collector under test.
type CollectorInfo struct {
	HealthURL string           `json:"health_url,omitempty"`
	Status    string           `json:"health_status,omitempty"`
	Build     map[string]any   `json:"build,omitempty"`
	Runtime   map[string]any   `json:"runtime,omitempty"`
	Limits    *CollectorLimits `json:"limits,omitempty"`
	// Writer is the collector writer's insert mode and retry policy as its
	// /healthz reports them: what an acknowledged insert meant for the run.
	Writer                 *WriterPolicy `json:"writer,omitempty"`
	DeclaredMaxPendingRows int64         `json:"declared_max_pending_rows,omitempty"`
	Budget                 BudgetCheck   `json:"budget_check"`
	Error                  string        `json:"error,omitempty"`
}

// BudgetCheck compares the collector's pending-row budget with what the load
// profile needs. The requirement is a HEURISTIC, not a measurement: rows that
// wait for the next flush trigger, rows arriving during a two-second insert
// allowance, and every in-flight request arriving at once.
type BudgetCheck struct {
	Method               string `json:"method"`
	RequiredPendingRows  int64  `json:"required_pending_rows"`
	AvailablePendingRows int64  `json:"available_pending_rows"`
	Source               string `json:"available_source"`
	Sufficient           bool   `json:"sufficient"`
	BatchFits            bool   `json:"batch_fits_budget"`
	RecommendedSetting   string `json:"recommended_setting"`
}

const insertAllowance = 2 * time.Second

// RequiredPendingRows is the heuristic row budget for a profile.
func RequiredPendingRows(rate int64, batchSize, concurrency int, limits CollectorLimits) int64 {
	trigger := min(float64(limits.BatchMaxRows), float64(rate)*float64(limits.BatchMaxAgeMS)/1000)
	if limits.BatchMaxRows <= 0 {
		trigger = float64(rate) * float64(limits.BatchMaxAgeMS) / 1000
	}
	return int64(math.Ceil(trigger+float64(rate)*insertAllowance.Seconds())) + int64(concurrency)*int64(batchSize)
}

// CheckBudget fills a BudgetCheck from the reported limits, or from a declared
// budget, or from the collector defaults when neither is known.
func CheckBudget(rate int64, batchSize, concurrency int, reported *CollectorLimits, declared int64) BudgetCheck {
	limits, source := collectorDefaultLimits, "collector default (NOT observed; pass --collector-health-url)"
	if reported != nil {
		limits, source = *reported, "collector /healthz"
	} else if declared > 0 {
		limits.MaxPendingRows, source = declared, "declared by operator (not observed)"
	}
	b := BudgetCheck{Method: "heuristic: min(batch_max_rows, rate*batch_max_age) + rate*2s insert allowance + concurrency*batch_size",
		RequiredPendingRows: RequiredPendingRows(rate, batchSize, concurrency, limits), AvailablePendingRows: limits.MaxPendingRows, Source: source}
	b.Sufficient = b.AvailablePendingRows >= b.RequiredPendingRows
	b.BatchFits = int64(batchSize) <= b.AvailablePendingRows
	b.RecommendedSetting = fmt.Sprintf("FI_MAX_PENDING_ROWS=%d (server.max_pending_rows)", b.RequiredPendingRows)
	return b
}

// Warnings are the loud, human-readable consequences of a budget check.
func (b BudgetCheck) Warnings() []string {
	var out []string
	if !b.BatchFits {
		out = append(out, fmt.Sprintf("batch size exceeds the collector budget of %d rows (%s): each request is admitted only while the collector's buffer is below the budget and is refused as retryable otherwise (collectors that predate this rule refuse it as non-retryable: HTTP 413 / gRPC INVALID_ARGUMENT)", b.AvailablePendingRows, b.Source))
	}
	if !b.Sufficient {
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
	info.Status, info.Build, info.Runtime, info.Limits, info.Writer = health.Status, health.Build, health.Runtime, health.Limits, health.Writer
	if info.Limits == nil {
		info.Error = "collector /healthz does not report limits (collector predates limit reporting)"
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
