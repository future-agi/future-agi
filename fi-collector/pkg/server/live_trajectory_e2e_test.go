package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

type liveExpectedSpan struct {
	TraceID    string `json:"trace_id"`
	SpanID     string `json:"span_id"`
	Turn       int    `json:"turn"`
	Input      string `json:"input"`
	Output     string `json:"output"`
	SessionExt string `json:"session_ext"`
	UserExt    string `json:"user_ext"`
}

type liveManifest struct {
	ProjectID              string             `json:"project_id"`
	OrgID                  string             `json:"org_id"`
	PreMigrationTraceID    string             `json:"pre_migration_trace_id"`
	PreMigrationSpanID     string             `json:"pre_migration_span_id"`
	PreMigrationInput      string             `json:"pre_migration_input"`
	PreMigrationOutput     string             `json:"pre_migration_output"`
	PreMigrationSessionExt string             `json:"pre_migration_session_ext"`
	OpenAI25TraceID        string             `json:"openai_25_trace_id"`
	OpenAI25SessionExt     string             `json:"openai_25_session_ext"`
	OpenAI25Spans          []liveExpectedSpan `json:"openai_25_spans"`
	AnthropicTraceID       string             `json:"anthropic_trace_id"`
	AnthropicSpans         []liveExpectedSpan `json:"anthropic_spans"`
	ResponsesTraceID       string             `json:"responses_trace_id"`
	ResponsesSpans         []liveExpectedSpan `json:"responses_spans"`
	OpenRouterTraceID      string             `json:"openrouter_trace_id"`
	OpenRouterSessionExt   string             `json:"openrouter_session_ext"`
	OpenRouterGenSpanID    string             `json:"openrouter_gen_span_id"`
	OpenRouterGenInput     string             `json:"openrouter_gen_input"`
	OpenRouterGenOutput    string             `json:"openrouter_gen_output"`
	MixedRollbackTraceID   string             `json:"mixed_rollback_trace_id"`
	MixedRollbackSpanID    string             `json:"mixed_rollback_span_id"`
	MixedRollbackInput     string             `json:"mixed_rollback_input"`
	MixedRollbackOutput    string             `json:"mixed_rollback_output"`
	FallbackTraceID        string             `json:"fallback_trace_id"`
	FallbackSpanID         string             `json:"fallback_span_id"`
	FallbackInput          string             `json:"fallback_input"`
	FallbackOutput         string             `json:"fallback_output"`
	StressSpansSent        int                `json:"stress_spans_sent"`
	StressDurationMS       int64              `json:"stress_duration_ms"`
	StressThroughputSPS    float64            `json:"stress_throughput_sps"`
	StressP50MS            float64            `json:"stress_p50_ms"`
	StressP95MS            float64            `json:"stress_p95_ms"`
	StressP99MS            float64            `json:"stress_p99_ms"`
	BilledRawBytes         int64              `json:"billed_raw_bytes"`
	BilledStoredBytes      int64              `json:"billed_stored_bytes"`
	BilledSavedBytes       int64              `json:"billed_saved_bytes"`
	BilledNewItems         int                `json:"billed_new_items"`
	BilledReusedItems      int                `json:"billed_reused_items"`
	// Dense + Sparse scale corpus IDs
	Dense50ErrorTraceID      string             `json:"dense50_error_trace_id,omitempty"`
	Dense50ErrorSpans        []liveExpectedSpan `json:"dense50_error_spans,omitempty"`
	Dense50OKTraceID         string             `json:"dense50_ok_trace_id,omitempty"`
	DenseWhaleSessionExt     string             `json:"dense_whale_session_ext,omitempty"`
	DenseWhaleUserExt        string             `json:"dense_whale_user_ext,omitempty"`
	VoiceCallTraceID         string             `json:"voice_call_trace_id,omitempty"`
	VoiceCallSpanID          string             `json:"voice_call_span_id,omitempty"`
	TombstoneTraceID         string             `json:"tombstone_trace_id,omitempty"`
	TombstoneSpanID          string             `json:"tombstone_span_id,omitempty"`
	SparseOld85dTraceID      string             `json:"sparse_old_85d_trace_id,omitempty"`
	SparseOld85dSpanID       string             `json:"sparse_old_85d_span_id,omitempty"`
	SparseOld85dInput        string             `json:"sparse_old_85d_input,omitempty"`
	SparseOld85dOutput       string             `json:"sparse_old_85d_output,omitempty"`
	SparseOldSessionExt      string             `json:"sparse_old_session_ext,omitempty"`
	SparseOldUserExt         string             `json:"sparse_old_user_ext,omitempty"`
	SparseNullTraceID        string             `json:"sparse_null_trace_id,omitempty"`
	SparseEmptyIOTraceID     string             `json:"sparse_empty_io_trace_id,omitempty"`
	SparsePlaintextTraceID   string             `json:"sparse_plaintext_trace_id,omitempty"`
	SparseMalformedTraceID   string             `json:"sparse_malformed_trace_id,omitempty"`
	SparseGiant45KBTraceID   string             `json:"sparse_giant_45kb_trace_id,omitempty"`
	SparseGiant45KBInput     string             `json:"sparse_giant_45kb_input,omitempty"`
	SparseUnknownCostTraceID string             `json:"sparse_unknown_cost_trace_id,omitempty"`
	ScaleTotalSpansSent      int                `json:"scale_total_spans_sent,omitempty"`
	ScaleDurationMS          int64              `json:"scale_duration_ms,omitempty"`
	ScaleThroughputSPS       float64            `json:"scale_throughput_sps,omitempty"`
	ScaleP50MS               float64            `json:"scale_p50_ms,omitempty"`
	ScaleP95MS               float64            `json:"scale_p95_ms,omitempty"`
	ScaleP99MS               float64            `json:"scale_p99_ms,omitempty"`
	// Mega live pipeline (100k+ spans) IDs
	MegaMultimodalTraceID  string   `json:"mega_multimodal_trace_id,omitempty"`
	MegaMultimodalSpanID   string   `json:"mega_multimodal_span_id,omitempty"`
	MegaHalfExtractTraceID string   `json:"mega_half_extract_trace_id,omitempty"`
	MegaHalfExtractSpanID  string   `json:"mega_half_extract_span_id,omitempty"`
	MegaHalfExtractOutput  string   `json:"mega_half_extract_output,omitempty"`
	MegaPromptMetaTraceID  string   `json:"mega_prompt_meta_trace_id,omitempty"`
	MegaPromptMetaSpanID   string   `json:"mega_prompt_meta_span_id,omitempty"`
	MegaAsyncInsertTraceID string   `json:"mega_async_insert_trace_id,omitempty"`
	MegaAsyncInsertSpanID  string   `json:"mega_async_insert_span_id,omitempty"`
	MegaAsyncInsertInput   string   `json:"mega_async_insert_input,omitempty"`
	MegaDatasetTraceIDs    []string `json:"mega_dataset_trace_ids,omitempty"`
	MegaDatasetSpanIDs     []string `json:"mega_dataset_span_ids,omitempty"`
	MegaPipelineSpansSent  int      `json:"mega_pipeline_spans_sent,omitempty"`
	MegaPipelineTracesSent int      `json:"mega_pipeline_traces_sent,omitempty"`
	MegaPipelineDurationMS int64    `json:"mega_pipeline_duration_ms,omitempty"`
	MegaPipelineSPS        float64  `json:"mega_pipeline_sps,omitempty"`
	MegaPipelineP50MS      float64  `json:"mega_pipeline_p50_ms,omitempty"`
	MegaPipelineP95MS      float64  `json:"mega_pipeline_p95_ms,omitempty"`
	MegaPipelineP99MS      float64  `json:"mega_pipeline_p99_ms,omitempty"`
	MegaRawBytes           int64    `json:"mega_raw_bytes,omitempty"`
	MegaStoredBytes        int64    `json:"mega_stored_bytes,omitempty"`
	MegaSavedBytes         int64    `json:"mega_saved_bytes,omitempty"`
	MegaNewItems           int      `json:"mega_new_items,omitempty"`
	MegaReusedItems        int      `json:"mega_reused_items,omitempty"`
}

func makeUUIDBytes(seed uint64) [16]byte {
	var b [16]byte
	b[0] = byte(seed >> 24)
	b[1] = byte(seed >> 16)
	b[2] = byte(seed >> 8)
	b[3] = byte(seed)
	b[6] = 0x40
	b[8] = 0x80
	b[12] = byte(seed >> 24)
	b[13] = byte(seed >> 16)
	b[14] = byte(seed >> 8)
	b[15] = byte(seed)
	return b
}

func formatDashedUUID(b [16]byte) string {
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

func realisticProse(seed uint64, words int) string {
	vocab := []string{
		"checkout", "payment", "invoice", "cluster", "replica", "partition", "latency", "timeout",
		"database", "transaction", "rollback", "telemetry", "pipeline", "scheduler", "allocator", "checkpoint",
		"webhook", "signature", "settlement", "currency", "merchant", "customer", "refund", "idempotency",
		"gateway", "fallback", "circuit", "breaker", "throttle", "histogram", "percentile", "throughput",
		"serializer", "compressor", "canonical", "checksum", "snapshot", "hydration", "projection", "granule",
		"dictionary", "materialized", "compaction", "retention", "partitioned", "streaming", "protobuf", "ingestion",
		"validation", "reconcile", "discrepancy", "audit", "compliance", "isolation", "tenant", "workspace",
		"kubernetes", "deployment", "container", "sidecar", "autoscaler", "observability", "diagnostic", "remediation",
	}
	x := seed ^ 0x9e3779b97f4a7c15
	if x == 0 {
		x = 1
	}
	var sb strings.Builder
	sb.Grow(words * 10)
	for i := 0; i < words; i++ {
		x ^= x << 13
		x ^= x >> 7
		x ^= x << 17
		if i > 0 {
			sb.WriteByte(' ')
		}
		sb.WriteString(vocab[x%uint64(len(vocab))])
		if i%9 == 8 {
			fmt.Fprintf(&sb, "=%x.", x&0xffff)
		}
	}
	return sb.String()
}

func makeSpanIDBytes(seed uint64) [8]byte {
	var b [8]byte
	for i := 0; i < 8; i++ {
		b[i] = byte(seed >> (56 - 8*i))
	}
	if b == ([8]byte{}) {
		b[7] = 1
	}
	return b
}

type liveSpanSpec struct {
	TraceBytes   [16]byte
	SpanBytes    [8]byte
	ParentBytes  [8]byte
	Name         string
	Kind         string
	Model        string
	Provider     string
	Status       ptrace.StatusCode
	StatusMsg    string
	StartTime    time.Time
	EndTime      time.Time
	SessionExt   string
	UserExt      string
	Input        string
	Output       string
	PromptTokens int64
	CompTokens   int64
	ExtraStr     map[string]string
	ExtraNum     map[string]float64
	ExtraBool    map[string]bool
	ExtraArr     map[string][]string
}

func encodeLiveOTLP(projectID, orgID string, specs []liveSpanSpec) ([]byte, error) {
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "checkout-agent-svc")
	rs.Resource().Attributes().PutStr("project_name", "agent-proj")
	rs.Resource().Attributes().PutStr("fi.project_id", projectID)
	rs.Resource().Attributes().PutStr("fi.org_id", orgID)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	ss := rs.ScopeSpans().AppendEmpty()

	for _, s := range specs {
		sp := ss.Spans().AppendEmpty()
		sp.SetTraceID(pcommon.TraceID(s.TraceBytes))
		sp.SetSpanID(pcommon.SpanID(s.SpanBytes))
		if s.ParentBytes != ([8]byte{}) {
			sp.SetParentSpanID(pcommon.SpanID(s.ParentBytes))
		}
		sp.SetName(s.Name)
		sp.SetStartTimestamp(pcommon.NewTimestampFromTime(s.StartTime))
		sp.SetEndTimestamp(pcommon.NewTimestampFromTime(s.EndTime))
		sp.Status().SetCode(s.Status)
		if s.StatusMsg != "" {
			sp.Status().SetMessage(s.StatusMsg)
		}
		attrs := sp.Attributes()
		if s.Kind != "" {
			attrs.PutStr("fi.span.kind", s.Kind)
		}
		if s.Model != "" {
			attrs.PutStr("gen_ai.request.model", s.Model)
		}
		if s.Provider != "" {
			attrs.PutStr("gen_ai.provider.name", s.Provider)
		}
		if s.SessionExt != "" {
			attrs.PutStr("session.id", s.SessionExt)
		}
		if s.UserExt != "" {
			attrs.PutStr("user.id", s.UserExt)
		}
		if s.Input != "" {
			attrs.PutStr("input.value", s.Input)
		}
		if s.Output != "" {
			attrs.PutStr("output.value", s.Output)
		}
		if s.PromptTokens > 0 {
			attrs.PutInt("gen_ai.usage.input_tokens", s.PromptTokens)
		}
		if s.CompTokens > 0 {
			attrs.PutInt("gen_ai.usage.output_tokens", s.CompTokens)
		}
		for k, v := range s.ExtraStr {
			attrs.PutStr(k, v)
		}
		for k, v := range s.ExtraNum {
			attrs.PutDouble(k, v)
		}
		for k, v := range s.ExtraBool {
			attrs.PutBool(k, v)
		}
		for k, vals := range s.ExtraArr {
			sl := attrs.PutEmptySlice(k)
			for _, item := range vals {
				sl.AppendEmpty().SetStr(item)
			}
		}
	}
	return ptraceotlp.NewExportRequestFromTraces(td).MarshalProto()
}

func sendLiveOTLP(srv *Server, orgID, projectID string, wire []byte) error {
	r := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(wire))
	r.Header.Set("Content-Type", "application/x-protobuf")
	r = r.WithContext(auth.WithResolvedContext(r.Context(), &auth.ResolveResult{
		OrgID:       orgID,
		WorkspaceID: "88888888-8888-4888-8888-888888888888",
		Projects:    map[string]string{"agent-proj": projectID},
	}, "live-key"))
	w := httptest.NewRecorder()
	srv.handleHTTPTraces(w, r)
	if w.Code != http.StatusOK {
		return fmt.Errorf("POST /v1/traces status=%d body=%s", w.Code, w.Body.String())
	}
	return nil
}

// TestLiveTrajectoryE2EAndStress runs against a real ClickHouse 25.3 + Redis 7
// instance when FI_LIVE_CH_URL and FI_LIVE_REDIS_ADDR are set; skipped in normal
// unit test runs.
func TestLiveTrajectoryE2EAndStress(t *testing.T) {
	chURL := os.Getenv("FI_LIVE_CH_URL")
	redisAddr := os.Getenv("FI_LIVE_REDIS_ADDR")
	stage := os.Getenv("FI_LIVE_STAGE")
	manifestPath := os.Getenv("FI_LIVE_MANIFEST")
	if chURL == "" || redisAddr == "" || stage == "" {
		t.Skip("set FI_LIVE_CH_URL, FI_LIVE_REDIS_ADDR, FI_LIVE_STAGE to run live ClickHouse+Redis E2E/stress test")
	}

	const (
		projectID = "11111111-1111-4111-8111-111111111111"
		orgID     = "99999999-9999-4999-8999-999999999999"
	)

	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	rdb := redis.NewClient(&redis.Options{Addr: redisAddr})
	defer rdb.Close()
	if err := rdb.Ping(context.Background()).Err(); err != nil {
		t.Fatalf("redis ping %s: %v", redisAddr, err)
	}

	newWriter := func(db string, targetURL string) *chwriter.Writer {
		w, err := chwriter.New(chwriter.Config{
			URL:            targetURL,
			Database:       db,
			Table:          "spans",
			MaxRetries:     3,
			DeadLetterFile: filepath.Join(t.TempDir(), db+"_dlq.jsonl"),
		})
		if err != nil {
			t.Fatalf("chwriter.New(%s): %v", db, err)
		}
		return w
	}

	loadManifest := func() liveManifest {
		var m liveManifest
		if b, err := os.ReadFile(manifestPath); err == nil {
			_ = json.Unmarshal(b, &m)
		}
		m.ProjectID = projectID
		m.OrgID = orgID
		return m
	}
	saveManifest := func(m liveManifest) {
		if manifestPath == "" {
			return
		}
		b, _ := json.MarshalIndent(m, "", "  ")
		if err := os.WriteFile(manifestPath, b, 0o644); err != nil {
			t.Fatalf("write manifest: %v", err)
		}
	}

	switch stage {
	case "pre_migration":
		// Stage 1: Write legacy inline spans BEFORE 028_trajectory_items.sql exists,
		// then keep writing continuously while migration signal file triggers 028!
		w := newWriter("default", chURL)
		defer w.Close()
		srv := New(Config{BatchMaxRows: 200, BatchMaxAge: 100 * time.Millisecond}, w, nil, NoopUsageEmitter{}, NoopMetering{}, WithLogger(log))

		now := time.Now().UTC().Add(-10 * time.Minute)
		m := loadManifest()

		// Deterministic pre-migration trace for exact verification after 028!
		preTid := makeUUIDBytes(0x1001)
		preSid := makeSpanIDBytes(0x100101)
		preIn := `{"messages":[{"role":"system","content":"Legacy prompt"},{"role":"user","content":"Legacy pre-028 question"}]}`
		preOut := `{"choices":[{"index":0,"message":{"role":"assistant","content":"Legacy pre-028 answer"}}]}`
		m.PreMigrationTraceID = formatDashedUUID(preTid)
		m.PreMigrationSpanID = fmt.Sprintf("%016x", preSid)
		m.PreMigrationInput = preIn
		m.PreMigrationOutput = preOut
		m.PreMigrationSessionExt = "sess-legacy-pre028"

		wire, err := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{{
			TraceBytes: preTid, SpanBytes: preSid,
			Name: "legacy-checkout-agent", Kind: "LLM", Model: "gpt-4o", Provider: "openai",
			Status: ptrace.StatusCodeOk, StartTime: now, EndTime: now.Add(150 * time.Millisecond),
			SessionExt: "sess-legacy-pre028", UserExt: "alice_enterprise",
			Input: preIn, Output: preOut, PromptTokens: 120, CompTokens: 45,
			ExtraStr:  map[string]string{"env": "prod", "region": "us-east"},
			ExtraNum:  map[string]float64{"tier_score": 8.0},
			ExtraBool: map[string]bool{"is_vip": true},
		}})
		if err != nil {
			t.Fatalf("encode pre-migration: %v", err)
		}
		if err := sendLiveOTLP(srv, orgID, projectID, wire); err != nil {
			t.Fatalf("send pre-migration: %v", err)
		}

		// Bulk pre-migration spans (1,000 spans across 50 traces, 10 sessions, 5 users).
		for tr := 0; tr < 50; tr++ {
			tid := makeUUIDBytes(uint64(0x2000 + tr))
			rootSid := makeSpanIDBytes(uint64(0x200000 + tr*100))
			specs := make([]liveSpanSpec, 0, 20)
			baseT := now.Add(time.Duration(tr) * time.Second)
			specs = append(specs, liveSpanSpec{
				TraceBytes: tid, SpanBytes: rootSid,
				Name: fmt.Sprintf("pre028-trace-%02d", tr), Kind: "CHAIN",
				Status: ptrace.StatusCodeOk, StartTime: baseT, EndTime: baseT.Add(time.Second),
				SessionExt: fmt.Sprintf("sess-pre-%02d", tr%10),
				UserExt:    fmt.Sprintf("user-pre-%02d", tr%5),
				Input:      fmt.Sprintf("Pre-028 trace %d input", tr),
				Output:     fmt.Sprintf("Pre-028 trace %d output", tr),
			})
			for c := 1; c < 20; c++ {
				specs = append(specs, liveSpanSpec{
					TraceBytes: tid, SpanBytes: makeSpanIDBytes(uint64(0x200000 + tr*100 + c)),
					ParentBytes: rootSid,
					Name:        fmt.Sprintf("pre028-child-%02d", c), Kind: "LLM", Model: "gpt-4o-mini", Provider: "openai",
					Status: ptrace.StatusCodeOk, StartTime: baseT.Add(time.Duration(c) * 20 * time.Millisecond),
					EndTime:      baseT.Add(time.Duration(c+1) * 20 * time.Millisecond),
					SessionExt:   fmt.Sprintf("sess-pre-%02d", tr%10),
					UserExt:      fmt.Sprintf("user-pre-%02d", tr%5),
					Input:        fmt.Sprintf(`{"messages":[{"role":"user","content":"Legacy turn %d"}]}`, c),
					Output:       fmt.Sprintf(`{"choices":[{"index":0,"message":{"role":"assistant","content":"Legacy reply %d"}}]}`, c),
					PromptTokens: 50, CompTokens: 20,
				})
			}
			b, err := encodeLiveOTLP(projectID, orgID, specs)
			if err != nil {
				t.Fatalf("encode bulk pre-028: %v", err)
			}
			if err := sendLiveOTLP(srv, orgID, projectID, b); err != nil {
				t.Fatalf("send bulk pre-028: %v", err)
			}
		}
		srv.drainNow(context.Background())

		// Concurrent live-migration test: if FI_LIVE_MIGRATE_READY / FI_LIVE_MIGRATE_DONE
		// are provided, keep streaming spans + draining while migration 028 runs live!
		readyFile := os.Getenv("FI_LIVE_MIGRATE_READY")
		doneFile := os.Getenv("FI_LIVE_MIGRATE_DONE")
		if readyFile != "" && doneFile != "" {
			_ = os.WriteFile(readyFile, []byte("ready"), 0o644)
			var sentDuring atomic.Int64
			for step := 0; step < 200; step++ {
				if _, err := os.Stat(doneFile); err == nil && step >= 10 {
					break
				}
				tid := makeUUIDBytes(uint64(0x3000 + step))
				sid := makeSpanIDBytes(uint64(0x300000 + step))
				b, _ := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{{
					TraceBytes: tid, SpanBytes: sid,
					Name: "during-migration-span", Kind: "LLM", Model: "gpt-4o", Provider: "openai",
					Status: ptrace.StatusCodeOk, StartTime: time.Now().UTC(), EndTime: time.Now().UTC().Add(10 * time.Millisecond),
					SessionExt: "sess-during-028", UserExt: "alice_enterprise",
					Input:        fmt.Sprintf(`{"messages":[{"role":"user","content":"in-flight span %d"}]}`, step),
					Output:       `{"choices":[{"index":0,"message":{"role":"assistant","content":"ack"}}]}`,
					PromptTokens: 10, CompTokens: 5,
				}})
				if err := sendLiveOTLP(srv, orgID, projectID, b); err != nil {
					t.Fatalf("send during migration failed at step %d: %v", step, err)
				}
				srv.drainNow(context.Background())
				sentDuring.Add(1)
				time.Sleep(25 * time.Millisecond)
			}
			t.Logf("LIVE MIGRATION ZERO-DOWNTIME: sent & drained %d spans concurrently during 028_trajectory_items.sql apply, writer stats=%+v",
				sentDuring.Load(), w.Snapshot())
			if w.Snapshot().BatchesFailed > 0 || w.Snapshot().RowsDeadLettered > 0 {
				t.Fatalf("unexpected failures during live migration: %+v", w.Snapshot())
			}
		}
		saveManifest(m)

	case "dedup_and_stress":
		// Stage 2: Post-028! Spin up TWO live Go pods (Pod A + Pod B) sharing LIVE Redis 7,
		// write corner-case fixtures + 5,000-span concurrent stress workload into BOTH
		// `default` (Phase 0+1 ON + live dedup billing) AND `baseline_inline` (flags OFF).
		rdb.FlushAll(context.Background())

		wDedup := newWriter("default", chURL)
		defer wDedup.Close()
		wBase := newWriter("baseline_inline", chURL)
		defer wBase.Close()

		usage := auth.NewUsageEmitter(rdb, nil, log, 100_000)
		dedupCfg := trajectory.Config{
			CompactSpanAttrs: true,
			Mode:             trajectory.ModeOn,
			BillDedupedBytes: true,
		}.Normalize()

		podA := New(Config{BatchMaxRows: 500, BatchMaxAge: 100 * time.Millisecond, IngestBudgetMiB: 256, InsertConcurrency: 4}, wDedup, nil, usage, NoopMetering{},
			WithLogger(log), WithTrajectory(dedupCfg, trajectory.NewSeenStore(rdb, 200_000, time.Hour, 24*time.Hour)))
		podB := New(Config{BatchMaxRows: 500, BatchMaxAge: 100 * time.Millisecond, IngestBudgetMiB: 256, InsertConcurrency: 4}, wDedup, nil, usage, NoopMetering{},
			WithLogger(log), WithTrajectory(dedupCfg, trajectory.NewSeenStore(rdb, 200_000, time.Hour, 24*time.Hour)))
		baseSrv := New(Config{BatchMaxRows: 500, BatchMaxAge: 100 * time.Millisecond, IngestBudgetMiB: 256, InsertConcurrency: 4}, wBase, nil, NoopUsageEmitter{}, NoopMetering{},
			WithLogger(log))
		startFlushers(t, podA)
		startFlushers(t, podB)
		startFlushers(t, baseSrv)

		m := loadManifest()
		now := time.Now().UTC().Add(-2 * time.Minute)

		sysPrompt := "You are Future AGI's production autonomous coding and checkout reliability agent. " +
			strings.Repeat("Never skip validation; always inspect logs, traces, and checkout state carefully. ", 30)
		tools := make([]map[string]any, 15)
		for i := range tools {
			tools[i] = map[string]any{
				"type": "function",
				"function": map[string]any{
					"name":        fmt.Sprintf("checkout_tool_%02d", i),
					"description": strings.Repeat(fmt.Sprintf("Executes diagnostic step %d against order & payment pipeline. ", i), 12),
					"parameters": map[string]any{
						"type":       "object",
						"properties": map[string]any{"order_id": map[string]any{"type": "string"}, "region": map[string]any{"type": "string"}},
					},
				},
			}
		}

		// ── Corner 1: 25-turn OpenAI Chat trace split across Pod A (even) & Pod B (odd) ──
		oaTid := makeUUIDBytes(0x4001)
		m.OpenAI25TraceID = formatDashedUUID(oaTid)
		m.OpenAI25SessionExt = "sess-openai-25turn"
		m.OpenAI25Spans = nil
		oaMsgs := []map[string]any{{"role": "system", "content": sysPrompt}}
		oaRootSid := makeSpanIDBytes(0x400100)

		for turn := 1; turn <= 25; turn++ {
			userMsg := fmt.Sprintf("Turn %02d user prompt: investigate UNIQUE_CHECKOUT_502 on shard %d (%s)", turn, turn, strings.Repeat("trace-ctx ", 20))
			oaMsgs = append(oaMsgs, map[string]any{"role": "user", "content": userMsg})
			inBytes, _ := json.Marshal(map[string]any{"model": "gpt-4o", "temperature": 0.2, "tools": tools, "messages": oaMsgs})
			asstMsg := map[string]any{"role": "assistant", "content": fmt.Sprintf("Turn %02d diagnosis: fixed retry budget %d (%s)", turn, turn, strings.Repeat("details ", 20))}
			outBytes, _ := json.Marshal(map[string]any{"id": fmt.Sprintf("chatcmpl-%02d", turn), "choices": []any{map[string]any{"index": 0, "message": asstMsg}}})

			sid := makeSpanIDBytes(uint64(0x400100 + turn))
			parent := oaRootSid
			if turn == 1 {
				sid = oaRootSid
				parent = [8]byte{}
			}
			spec := liveSpanSpec{
				TraceBytes: oaTid, SpanBytes: sid, ParentBytes: parent,
				Name: "checkout-agent-turn", Kind: "LLM", Model: "gpt-4o", Provider: "openai",
				Status:     ptrace.StatusCodeOk,
				StartTime:  now.Add(time.Duration(turn) * time.Second),
				EndTime:    now.Add(time.Duration(turn)*time.Second + 350*time.Millisecond),
				SessionExt: "sess-openai-25turn", UserExt: "alice_enterprise",
				Input: string(inBytes), Output: string(outBytes),
				PromptTokens: int64(500 + turn*120), CompTokens: 90,
				ExtraStr: map[string]string{
					"env":                                  "prod",
					"region":                               "us-east",
					"custom.oversized_blob":                strings.Repeat("BIGBLOB_", 2500), // > 16 KB -> overflow
					"llm.input_messages.0.message.role":    "system",
					"llm.input_messages.0.message.content": sysPrompt,
					"llm.input_messages.1.message.role":    "user",
					"llm.input_messages.1.message.content": userMsg,
				},
				ExtraNum:  map[string]float64{"tier_score": 9.5},
				ExtraBool: map[string]bool{"is_vip": true},
				ExtraArr:  map[string][]string{"tags_arr": {"billing", "checkout"}},
			}
			wire, err := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{spec})
			if err != nil {
				t.Fatalf("encode openai turn %d: %v", turn, err)
			}
			targetPod := podA
			if turn%2 == 1 {
				targetPod = podB
			}
			if err := sendLiveOTLP(targetPod, orgID, projectID, wire); err != nil {
				t.Fatalf("send openai turn %d: %v", turn, err)
			}
			m.OpenAI25Spans = append(m.OpenAI25Spans, liveExpectedSpan{
				TraceID: m.OpenAI25TraceID, SpanID: fmt.Sprintf("%016x", sid), Turn: turn,
				Input: string(inBytes), Output: string(outBytes),
				SessionExt: "sess-openai-25turn", UserExt: "alice_enterprise",
			})
			oaMsgs = append(oaMsgs, asstMsg)
		}

		// ── Corner 2: 5-turn Anthropic trace ──
		anTid := makeUUIDBytes(0x4002)
		m.AnthropicTraceID = formatDashedUUID(anTid)
		m.AnthropicSpans = nil
		anMsgs := []map[string]any{}
		for turn := 1; turn <= 5; turn++ {
			anMsgs = append(anMsgs, map[string]any{"role": "user", "content": fmt.Sprintf("Anthropic turn %d question", turn)})
			inBytes, _ := json.Marshal(map[string]any{"model": "claude-sonnet-4-6", "system": sysPrompt, "messages": anMsgs})
			asst := map[string]any{"role": "assistant", "content": fmt.Sprintf("Anthropic turn %d answer", turn)}
			outBytes, _ := json.Marshal([]any{asst})
			sid := makeSpanIDBytes(uint64(0x400200 + turn))
			var parent [8]byte
			if turn > 1 {
				parent = makeSpanIDBytes(0x400201)
			}
			wire, _ := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{{
				TraceBytes: anTid, SpanBytes: sid, ParentBytes: parent,
				Name: "anthropic-agent", Kind: "LLM", Model: "claude-sonnet-4-6", Provider: "anthropic",
				Status: ptrace.StatusCodeOk, StartTime: now.Add(time.Duration(turn) * time.Second),
				EndTime:    now.Add(time.Duration(turn)*time.Second + 200*time.Millisecond),
				SessionExt: "sess-anthropic", UserExt: "bob_startup",
				Input: string(inBytes), Output: string(outBytes), PromptTokens: 400, CompTokens: 80,
			}})
			targetPod := podA
			if turn%2 == 0 {
				targetPod = podB
			}
			if err := sendLiveOTLP(targetPod, orgID, projectID, wire); err != nil {
				t.Fatalf("send anthropic turn %d: %v", turn, err)
			}
			m.AnthropicSpans = append(m.AnthropicSpans, liveExpectedSpan{
				TraceID: m.AnthropicTraceID, SpanID: fmt.Sprintf("%016x", sid), Turn: turn,
				Input: string(inBytes), Output: string(outBytes), SessionExt: "sess-anthropic", UserExt: "bob_startup",
			})
			anMsgs = append(anMsgs, asst)
		}

		// ── Corner 3: 5-turn OpenAI Responses API trace ──
		respTid := makeUUIDBytes(0x4003)
		m.ResponsesTraceID = formatDashedUUID(respTid)
		m.ResponsesSpans = nil
		respIn := []map[string]any{}
		for turn := 1; turn <= 5; turn++ {
			respIn = append(respIn, map[string]any{"role": "user", "content": fmt.Sprintf("Responses API turn %d", turn)})
			inBytes, _ := json.Marshal(map[string]any{"model": "gpt-5", "instructions": sysPrompt, "input": respIn})
			outStr := fmt.Sprintf(`{"output_text":"Responses reply %d"}`, turn)
			sid := makeSpanIDBytes(uint64(0x400300 + turn))
			var parent [8]byte
			if turn > 1 {
				parent = makeSpanIDBytes(0x400301)
			}
			wire, _ := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{{
				TraceBytes: respTid, SpanBytes: sid, ParentBytes: parent,
				Name: "responses-agent", Kind: "LLM", Model: "gpt-5", Provider: "openai",
				Status: ptrace.StatusCodeOk, StartTime: now.Add(time.Duration(turn) * time.Second),
				EndTime:    now.Add(time.Duration(turn)*time.Second + 180*time.Millisecond),
				SessionExt: "sess-responses", UserExt: "bob_startup",
				Input: string(inBytes), Output: outStr, PromptTokens: 300, CompTokens: 60,
			}})
			if err := sendLiveOTLP(podA, orgID, projectID, wire); err != nil {
				t.Fatalf("send responses turn %d: %v", turn, err)
			}
			m.ResponsesSpans = append(m.ResponsesSpans, liveExpectedSpan{
				TraceID: m.ResponsesTraceID, SpanID: fmt.Sprintf("%016x", sid), Turn: turn,
				Input: string(inBytes), Output: outStr, SessionExt: "sess-responses", UserExt: "bob_startup",
			})
			respIn = append(respIn, map[string]any{"role": "assistant", "content": fmt.Sprintf("Responses reply %d", turn)})
		}

		// ── Corner 4: OpenRouter Broadcast shape (empty root + child LLM span) for Session Detail bubble test ──
		orTid := makeUUIDBytes(0x4004)
		orRootSid := makeSpanIDBytes(0x400401)
		orGenSid := makeSpanIDBytes(0x400402)
		orInBytes, _ := json.Marshal(map[string]any{
			"model": "openai/gpt-4o", "tools": tools[:3],
			"messages": []map[string]any{
				{"role": "system", "content": sysPrompt},
				{"role": "user", "content": "OpenRouter session bubble full prompt question"},
			},
		})
		orOutBytes, _ := json.Marshal(map[string]any{
			"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "OpenRouter session bubble full hydrated answer " + strings.Repeat("word ", 120)}}},
		})
		m.OpenRouterTraceID = formatDashedUUID(orTid)
		m.OpenRouterSessionExt = "sess-openrouter-bubbles"
		m.OpenRouterGenSpanID = fmt.Sprintf("%016x", orGenSid)
		m.OpenRouterGenInput = string(orInBytes)
		m.OpenRouterGenOutput = string(orOutBytes)
		wireOR, _ := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{
			{
				TraceBytes: orTid, SpanBytes: orRootSid,
				Name: "Chat Handler", Kind: "CHAIN", Status: ptrace.StatusCodeOk,
				StartTime: now, EndTime: now.Add(time.Second),
				SessionExt: "sess-openrouter-bubbles", UserExt: "alice_enterprise",
			},
			{
				TraceBytes: orTid, SpanBytes: orGenSid, ParentBytes: orRootSid,
				Name: "Generate Response", Kind: "LLM", Model: "openai/gpt-4o", Provider: "openrouter",
				Status: ptrace.StatusCodeOk, StartTime: now.Add(50 * time.Millisecond), EndTime: now.Add(900 * time.Millisecond),
				SessionExt: "sess-openrouter-bubbles", UserExt: "alice_enterprise",
				Input: string(orInBytes), Output: string(orOutBytes), PromptTokens: 900, CompTokens: 250,
			},
		})
		if err := sendLiveOTLP(podB, orgID, projectID, wireOR); err != nil {
			t.Fatalf("send openrouter: %v", err)
		}

		// Drain Pod A & Pod B before rollback test.
		podA.drainNow(context.Background())
		podB.drainNow(context.Background())

		// ── Corner 5: Rollback / Mixed session (inline trace added into existing items_v1 session) ──
		rollbackSrv := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, wDedup, nil, NoopUsageEmitter{}, NoopMetering{}, WithLogger(log))
		mixTid := makeUUIDBytes(0x4005)
		mixSid := makeSpanIDBytes(0x400501)
		mixIn := `{"messages":[{"role":"user","content":"Post-rollback inline turn in same session"}]}`
		mixOut := `{"choices":[{"index":0,"message":{"role":"assistant","content":"Post-rollback inline reply"}}]}`
		m.MixedRollbackTraceID = formatDashedUUID(mixTid)
		m.MixedRollbackSpanID = fmt.Sprintf("%016x", mixSid)
		m.MixedRollbackInput = mixIn
		m.MixedRollbackOutput = mixOut
		wireMix, _ := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{{
			TraceBytes: mixTid, SpanBytes: mixSid,
			Name: "post-rollback-inline", Kind: "LLM", Model: "gpt-4o", Provider: "openai",
			Status: ptrace.StatusCodeOk, StartTime: now.Add(2 * time.Minute), EndTime: now.Add(2*time.Minute + 100*time.Millisecond),
			SessionExt: "sess-openai-25turn", UserExt: "alice_enterprise",
			Input: mixIn, Output: mixOut, PromptTokens: 40, CompTokens: 20,
		}})
		if err := sendLiveOTLP(rollbackSrv, orgID, projectID, wireMix); err != nil {
			t.Fatalf("send rollback mix: %v", err)
		}
		rollbackSrv.drainNow(context.Background())

		// ── Corner 6: Simulated trajectory_items ClickHouse outage -> automatic inline fallback to LIVE spans! ──
		proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if strings.Contains(r.URL.Query().Get("query"), "INTO trajectory_items") {
				http.Error(w, "injected trajectory_items 503 outage", http.StatusServiceUnavailable)
				return
			}
			// Forward spans/curated inserts straight to live ClickHouse!
			body, _ := io.ReadAll(r.Body)
			req, _ := http.NewRequestWithContext(r.Context(), r.Method, chURL+r.URL.RequestURI(), bytes.NewReader(body))
			req.Header = r.Header.Clone()
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				http.Error(w, err.Error(), http.StatusBadGateway)
				return
			}
			defer resp.Body.Close()
			w.WriteHeader(resp.StatusCode)
			_, _ = io.Copy(w, resp.Body)
		}))
		defer proxy.Close()
		wFallback := newWriter("default", proxy.URL)
		defer wFallback.Close()
		fbSeen := trajectory.NewSeenStore(rdb, 10_000, time.Hour, time.Hour)
		fbSrv := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, wFallback, nil, NoopUsageEmitter{}, NoopMetering{},
			WithLogger(log), WithTrajectory(dedupCfg, fbSeen))
		fbTid := makeUUIDBytes(0x4006)
		fbSid := makeSpanIDBytes(0x400601)
		fbInBytes, _ := json.Marshal(map[string]any{
			"model": "gpt-4o",
			"messages": []map[string]any{
				{"role": "system", "content": "Fallback outage unique system prompt " + strings.Repeat("fb ", 80)},
				{"role": "user", "content": "Fallback outage user prompt must survive intact"},
			},
		})
		fbOutBytes, _ := json.Marshal(map[string]any{
			"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "Fallback outage reply survived"}}},
		})
		m.FallbackTraceID = formatDashedUUID(fbTid)
		m.FallbackSpanID = fmt.Sprintf("%016x", fbSid)
		m.FallbackInput = string(fbInBytes)
		m.FallbackOutput = string(fbOutBytes)
		wireFB, _ := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{{
			TraceBytes: fbTid, SpanBytes: fbSid,
			Name: "fallback-outage-span", Kind: "LLM", Model: "gpt-4o", Provider: "openai",
			Status: ptrace.StatusCodeOk, StartTime: now.Add(90 * time.Second), EndTime: now.Add(91 * time.Second),
			SessionExt: "sess-fallback-outage", UserExt: "alice_enterprise",
			Input: string(fbInBytes), Output: string(fbOutBytes), PromptTokens: 100, CompTokens: 30,
		}})
		if err := sendLiveOTLP(fbSrv, orgID, projectID, wireFB); err != nil {
			t.Fatalf("send fallback span: %v", err)
		}
		fbSrv.drainNow(context.Background())

		// ── Stress Test: 100 concurrent multi-turn traces × 25 LLM turns + 25 tool spans = 5,000 spans ──
		// Sent simultaneously into BOTH `default` (2-pod dedup) and `baseline_inline` (flags OFF)
		// to measure real ClickHouse on-disk compression, throughput, latency, and Redis billing!
		rdb.Del(context.Background(), "usage:events")

		const (
			stressTraces = 100
			stressTurns  = 25
			workers      = 16
		)
		startStress := time.Now()
		var latMu sync.Mutex
		latenciesMS := make([]float64, 0, stressTraces*stressTurns)
		var totalSpansSent atomic.Int64

		jobs := make(chan int, stressTraces)
		for tr := 0; tr < stressTraces; tr++ {
			jobs <- tr
		}
		close(jobs)

		var wg sync.WaitGroup
		errCh := make(chan error, workers)
		for wIdx := 0; wIdx < workers; wIdx++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for tr := range jobs {
					tid := makeUUIDBytes(uint64(0x9000 + tr))
					rootSid := makeSpanIDBytes(uint64(0x9000000 + tr*1000))
					sessExt := fmt.Sprintf("sess-stress-%03d", tr%25)
					userExt := fmt.Sprintf("stress_user_%02d", tr%10)
					msgs := []map[string]any{{"role": "system", "content": sysPrompt}}
					baseT := now.Add(time.Duration(tr) * 500 * time.Millisecond)

					for turn := 1; turn <= stressTurns; turn++ {
						msgs = append(msgs, map[string]any{
							"role":    "user",
							"content": fmt.Sprintf("Stress trace %03d turn %02d user prompt: %s", tr, turn, realisticProse(uint64(tr*1000+turn*2), 140)),
						})
						inB, _ := json.Marshal(map[string]any{"model": "claude-sonnet-4-6", "tools": tools, "messages": msgs})
						reply := map[string]any{
							"role":    "assistant",
							"content": fmt.Sprintf("Stress trace %03d turn %02d reply: %s", tr, turn, realisticProse(uint64(tr*1000+turn*2+1), 140)),
						}
						outB, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"index": 0, "message": reply}}})

						llmSid := makeSpanIDBytes(uint64(0x9000000 + tr*1000 + turn*2))
						toolSid := makeSpanIDBytes(uint64(0x9000000 + tr*1000 + turn*2 + 1))
						parent := rootSid
						if turn == 1 {
							llmSid = rootSid
							parent = [8]byte{}
						}
						specs := []liveSpanSpec{
							{
								TraceBytes: tid, SpanBytes: llmSid, ParentBytes: parent,
								Name: "stress-agent-llm", Kind: "LLM", Model: "claude-sonnet-4-6", Provider: "anthropic",
								Status:     ptrace.StatusCodeOk,
								StartTime:  baseT.Add(time.Duration(turn) * 100 * time.Millisecond),
								EndTime:    baseT.Add(time.Duration(turn)*100*time.Millisecond + 80*time.Millisecond),
								SessionExt: sessExt, UserExt: userExt,
								Input: string(inB), Output: string(outB),
								PromptTokens: int64(800 + turn*150), CompTokens: 120,
								ExtraStr: map[string]string{"env": "stress", "workload": "agent_25t"},
							},
							{
								TraceBytes: tid, SpanBytes: toolSid, ParentBytes: llmSid,
								Name: "stress-tool-call", Kind: "TOOL",
								Status:     ptrace.StatusCodeOk,
								StartTime:  baseT.Add(time.Duration(turn)*100*time.Millisecond + 85*time.Millisecond),
								EndTime:    baseT.Add(time.Duration(turn)*100*time.Millisecond + 95*time.Millisecond),
								SessionExt: sessExt, UserExt: userExt,
								Input:  fmt.Sprintf(`{"tool":"search_docs","turn":%d}`, turn),
								Output: fmt.Sprintf(`{"hits":3,"turn":%d}`, turn),
							},
						}
						wire, err := encodeLiveOTLP(projectID, orgID, specs)
						if err != nil {
							errCh <- err
							return
						}
						// Alternate Pod A / Pod B every turn to prove real cross-pod Redis dedup under concurrency!
						pod := podA
						if (tr+turn)%2 == 1 {
							pod = podB
						}
						t0 := time.Now()
						if err := sendLiveOTLP(pod, orgID, projectID, wire); err != nil {
							errCh <- err
							return
						}
						elapsed := float64(time.Since(t0).Microseconds()) / 1000.0
						latMu.Lock()
						latenciesMS = append(latenciesMS, elapsed)
						latMu.Unlock()

						// Also feed baseline_inline (no dedup, no compaction) for apples-to-apples disk measurement.
						if err := sendLiveOTLP(baseSrv, orgID, projectID, wire); err != nil {
							errCh <- err
							return
						}
						totalSpansSent.Add(2)
						msgs = append(msgs, reply)
					}
				}
			}()
		}
		wg.Wait()
		close(errCh)
		for err := range errCh {
			t.Fatalf("stress worker error: %v", err)
		}
		stopAndWait := func(s *Server) {
			select {
			case <-s.stopCh:
			default:
				close(s.stopCh)
			}
			s.wg.Wait()
			s.drainNow(context.Background())
		}
		stopAndWait(podA)
		stopAndWait(podB)
		stopAndWait(baseSrv)
		dur := time.Since(startStress)

		if wDedup.Snapshot().BatchesFailed > 0 || wDedup.Snapshot().RowsDeadLettered > 0 {
			t.Fatalf("dedup writer failures during stress: %+v", wDedup.Snapshot())
		}
		if wBase.Snapshot().BatchesFailed > 0 || wBase.Snapshot().RowsDeadLettered > 0 {
			t.Fatalf("baseline writer failures during stress: %+v", wBase.Snapshot())
		}

		sort.Float64s(latenciesMS)
		pct := func(p float64) float64 {
			if len(latenciesMS) == 0 {
				return 0
			}
			idx := int(float64(len(latenciesMS)-1) * p)
			return latenciesMS[idx]
		}

		// Sum live Redis usage:events stream across Pod A + Pod B!
		entries, err := rdb.XRange(context.Background(), "usage:events", "-", "+").Result()
		if err != nil {
			t.Fatalf("XRange usage:events: %v", err)
		}
		var sumRaw, sumStored, sumSaved int64
		var sumNew, sumReused int
		for _, e := range entries {
			var props struct {
				BillingBasis    string `json:"billing_basis"`
				RawBytes        int64  `json:"raw_bytes"`
				StoredBytes     int64  `json:"stored_bytes"`
				DedupSavedBytes int64  `json:"dedup_saved_bytes"`
				NewItems        int    `json:"new_items"`
				ReusedItems     int    `json:"reused_items"`
			}
			if rawProps, ok := e.Values["properties"].(string); ok {
				if err := json.Unmarshal([]byte(rawProps), &props); err == nil {
					if props.BillingBasis != "deduped_storage" {
						t.Fatalf("unexpected billing_basis: %q", props.BillingBasis)
					}
					sumRaw += props.RawBytes
					sumStored += props.StoredBytes
					sumSaved += props.DedupSavedBytes
					sumNew += props.NewItems
					sumReused += props.ReusedItems
				}
			}
		}

		m.StressSpansSent = int(totalSpansSent.Load())
		m.StressDurationMS = dur.Milliseconds()
		m.StressThroughputSPS = float64(m.StressSpansSent) / dur.Seconds()
		m.StressP50MS = pct(0.50)
		m.StressP95MS = pct(0.95)
		m.StressP99MS = pct(0.99)
		m.BilledRawBytes = sumRaw
		m.BilledStoredBytes = sumStored
		m.BilledSavedBytes = sumSaved
		m.BilledNewItems = sumNew
		m.BilledReusedItems = sumReused
		saveManifest(m)

		t.Logf("LIVE STRESS COMPLETE: %d spans in %s (%.1f spans/s, OTLP handler p50=%.2fms p95=%.2fms p99=%.2fms)",
			m.StressSpansSent, dur.Round(time.Millisecond), m.StressThroughputSPS, m.StressP50MS, m.StressP95MS, m.StressP99MS)
		t.Logf("LIVE CROSS-POD BILLING (Redis usage:events): raw=%d B (%.1f MB) -> billed=%d B (%.1f MB), saved=%.1f%% (%.1fx), new_items=%d, reused_items=%d",
			sumRaw, float64(sumRaw)/1e6, sumStored, float64(sumStored)/1e6,
			100.0*float64(sumSaved)/float64(max(sumRaw, 1)), float64(sumRaw)/float64(max(sumStored, 1)),
			sumNew, sumReused)
		t.Logf("WRITER STATS dedup=%+v", wDedup.Snapshot())

	case "dense_sparse_scale":
		// Scale + Dense & Sparse multi-partition corpus (~16,000 spans across 4 monthly partitions,
		// 10-minute high-density whale burst + 90-day sparse long-tail + voice + errors + unicode + edge shapes).
		rdb.FlushAll(context.Background())

		wDedup := newWriter("default", chURL)
		defer wDedup.Close()

		usage := auth.NewUsageEmitter(rdb, nil, log, 200_000)
		dedupCfg := trajectory.Config{
			CompactSpanAttrs: true,
			Mode:             trajectory.ModeOn,
			BillDedupedBytes: true,
		}.Normalize()

		podA := New(Config{BatchMaxRows: 1000, BatchMaxAge: 100 * time.Millisecond, IngestBudgetMiB: 512, InsertConcurrency: 4}, wDedup, nil, usage, NoopMetering{},
			WithLogger(log), WithTrajectory(dedupCfg, trajectory.NewSeenStore(rdb, 300_000, time.Hour, 24*time.Hour)))
		podB := New(Config{BatchMaxRows: 1000, BatchMaxAge: 100 * time.Millisecond, IngestBudgetMiB: 512, InsertConcurrency: 4}, wDedup, nil, usage, NoopMetering{},
			WithLogger(log), WithTrajectory(dedupCfg, trajectory.NewSeenStore(rdb, 300_000, time.Hour, 24*time.Hour)))
		startFlushers(t, podA)
		startFlushers(t, podB)

		m := loadManifest()
		now := time.Now().UTC().Add(-2 * time.Minute)
		sysPrompt := "Dense/Sparse scale verification system prompt: " + realisticProse(777, 180)
		tools := make([]map[string]any, 12)
		for i := range tools {
			tools[i] = map[string]any{
				"type": "function",
				"function": map[string]any{
					"name":        fmt.Sprintf("scale_tool_%02d", i),
					"description": realisticProse(uint64(800+i), 40),
					"parameters":  map[string]any{"type": "object", "properties": map[string]any{"id": map[string]any{"type": "string"}}},
				},
			}
		}

		// ── 1. Dense 50-turn ERROR coding agent trace (100 spans) ──
		errTid := makeUUIDBytes(0x50001)
		m.Dense50ErrorTraceID = formatDashedUUID(errTid)
		m.Dense50ErrorSpans = nil
		errMsgs := []map[string]any{{"role": "system", "content": sysPrompt}}
		errRootSid := makeSpanIDBytes(0x50001000)
		mediumAttr := "MEDIUM_6KB_FILTERABLE_" + strings.Repeat("M", 6000)
		oversizedBlob := "OVERSIZED_20KB_NEEDLE_" + strings.Repeat("Z", 20000)
		for turn := 1; turn <= 50; turn++ {
			errMsgs = append(errMsgs, map[string]any{
				"role":    "user",
				"content": fmt.Sprintf("Dense50 turn %02d DENSE50_ERROR_NEEDLE %s", turn, realisticProse(uint64(5000+turn), 90)),
			})
			inB, _ := json.Marshal(map[string]any{"model": "gpt-4o", "tools": tools, "messages": errMsgs})
			asst := map[string]any{
				"role":    "assistant",
				"content": fmt.Sprintf("Dense50 turn %02d fix %s", turn, realisticProse(uint64(6000+turn), 80)),
			}
			outB, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"index": 0, "message": asst}}})

			llmSid := makeSpanIDBytes(uint64(0x50001000 + turn*2))
			toolSid := makeSpanIDBytes(uint64(0x50001000 + turn*2 + 1))
			parent := errRootSid
			st := ptrace.StatusCodeOk
			stMsg := ""
			if turn == 1 {
				llmSid = errRootSid
				parent = [8]byte{}
			}
			if turn == 50 {
				st = ptrace.StatusCodeError
				stMsg = "downstream payment timeout on turn 50"
			}
			specs := []liveSpanSpec{
				{
					TraceBytes: errTid, SpanBytes: llmSid, ParentBytes: parent,
					Name: "dense-50turn-error-whale", Kind: "LLM", Model: "gpt-4o", Provider: "openai",
					Status: st, StatusMsg: stMsg,
					StartTime:  now.Add(time.Duration(turn) * 150 * time.Millisecond),
					EndTime:    now.Add(time.Duration(turn)*150*time.Millisecond + 450*time.Millisecond),
					SessionExt: "sess-dense-whale", UserExt: "user-dense-whale",
					Input: string(inB), Output: string(outB),
					PromptTokens: int64(900 + turn*100), CompTokens: 110,
					ExtraStr: map[string]string{
						"env":                   "prod",
						"region":                "us-east",
						"team":                  "payments",
						"unicode_note":          "🔥 日本語 résumé €100",
						"empty_tag":             "",
						"custom.medium_note":    mediumAttr,
						"custom.oversized_blob": oversizedBlob,
					},
					ExtraNum:  map[string]float64{"tier_score": 98.5},
					ExtraBool: map[string]bool{"is_vip": true},
					ExtraArr:  map[string][]string{"tags_arr": {"billing", "checkout"}},
				},
				{
					TraceBytes: errTid, SpanBytes: toolSid, ParentBytes: llmSid,
					Name: "payment-db-tool", Kind: "TOOL",
					Status:     ptrace.StatusCodeOk,
					StartTime:  now.Add(time.Duration(turn)*150*time.Millisecond + 460*time.Millisecond),
					EndTime:    now.Add(time.Duration(turn)*150*time.Millisecond + 490*time.Millisecond),
					SessionExt: "sess-dense-whale", UserExt: "user-dense-whale",
					Input: fmt.Sprintf(`{"query":"SELECT * FROM orders WHERE id=%d"}`, turn), Output: `{"rows":1}`,
					ExtraStr: map[string]string{"env": "prod", "region": "us-east", "team": "payments"},
				},
			}
			wire, err := encodeLiveOTLP(projectID, orgID, specs)
			if err != nil {
				t.Fatalf("encode dense50 err turn %d: %v", turn, err)
			}
			pod := podA
			if turn%2 == 1 {
				pod = podB
			}
			if err := sendLiveOTLP(pod, orgID, projectID, wire); err != nil {
				t.Fatalf("send dense50 err turn %d: %v", turn, err)
			}
			if turn == 1 || turn == 25 || turn == 50 {
				m.Dense50ErrorSpans = append(m.Dense50ErrorSpans, liveExpectedSpan{
					TraceID: m.Dense50ErrorTraceID, SpanID: fmt.Sprintf("%016x", llmSid), Turn: turn,
					Input: string(inB), Output: string(outB), SessionExt: "sess-dense-whale", UserExt: "user-dense-whale",
				})
			}
			errMsgs = append(errMsgs, asst)
		}

		// ── 2. Dense 50-turn OK trace (staging / eu-west / is_vip=false) ──
		okTid := makeUUIDBytes(0x50002)
		m.Dense50OKTraceID = formatDashedUUID(okTid)
		okMsgs := []map[string]any{{"role": "system", "content": sysPrompt}}
		okRootSid := makeSpanIDBytes(0x50002000)
		for turn := 1; turn <= 50; turn++ {
			okMsgs = append(okMsgs, map[string]any{"role": "user", "content": fmt.Sprintf("Staging search turn %02d %s", turn, realisticProse(uint64(7000+turn), 70))})
			inB, _ := json.Marshal(map[string]any{"model": "claude-sonnet-4-6", "tools": tools[:4], "messages": okMsgs})
			asst := map[string]any{"role": "assistant", "content": fmt.Sprintf("Staging search answer %02d", turn)}
			outB, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"index": 0, "message": asst}}})
			llmSid := makeSpanIDBytes(uint64(0x50002000 + turn))
			parent := okRootSid
			if turn == 1 {
				llmSid = okRootSid
				parent = [8]byte{}
			}
			wire, _ := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{{
				TraceBytes: okTid, SpanBytes: llmSid, ParentBytes: parent,
				Name: "dense-50turn-staging-search", Kind: "LLM", Model: "claude-sonnet-4-6", Provider: "anthropic",
				Status:     ptrace.StatusCodeOk,
				StartTime:  now.Add(time.Duration(turn) * 120 * time.Millisecond),
				EndTime:    now.Add(time.Duration(turn)*120*time.Millisecond + 220*time.Millisecond),
				SessionExt: "sess-staging-search", UserExt: "user-staging-ops",
				Input: string(inB), Output: string(outB), PromptTokens: 400, CompTokens: 60,
				ExtraStr:  map[string]string{"env": "staging", "region": "eu-west", "team": "search"},
				ExtraNum:  map[string]float64{"tier_score": 42.0},
				ExtraBool: map[string]bool{"is_vip": false},
				ExtraArr:  map[string][]string{"tags_arr": {"search", "rag"}},
			}})
			if err := sendLiveOTLP(podB, orgID, projectID, wire); err != nil {
				t.Fatalf("send dense50 ok turn %d: %v", turn, err)
			}
			okMsgs = append(okMsgs, asst)
		}

		// ── 3. Voice Call CONVERSATION root span + Tombstoned trace ──
		vcTid := makeUUIDBytes(0x50003)
		vcSid := makeSpanIDBytes(0x50003001)
		m.VoiceCallTraceID = formatDashedUUID(vcTid)
		m.VoiceCallSpanID = fmt.Sprintf("%016x", vcSid)
		vcIn, _ := json.Marshal(map[string]any{"messages": []map[string]any{{"role": "user", "content": "Hello I need help with my invoice"}}})
		vcOut, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "Happy to help with your invoice!"}}}})
		wireVC, _ := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{{
			TraceBytes: vcTid, SpanBytes: vcSid,
			Name: "voice-support-call", Kind: "CONVERSATION", Model: "gpt-4o-realtime", Provider: "vapi",
			Status: ptrace.StatusCodeOk, StartTime: now.Add(5 * time.Second), EndTime: now.Add(65 * time.Second),
			SessionExt: "sess-voice-call", UserExt: "user-voice-caller",
			Input: string(vcIn), Output: string(vcOut), PromptTokens: 300, CompTokens: 200,
			ExtraStr: map[string]string{
				"env":         "prod",
				"call.status": "completed",
				"call_logs":   strings.Repeat(`{"msg":"heavy_call_log_payload"}`, 200),
			},
			ExtraNum: map[string]float64{"call.total_turns": 8, "call.talk_ratio": 0.62},
		}})
		if err := sendLiveOTLP(podA, orgID, projectID, wireVC); err != nil {
			t.Fatalf("send voice call: %v", err)
		}

		// Tombstone candidate trace (will be soft-deleted with higher _version in Python verify!)
		tombTid := makeUUIDBytes(0x50004)
		tombSid := makeSpanIDBytes(0x50004001)
		m.TombstoneTraceID = formatDashedUUID(tombTid)
		m.TombstoneSpanID = fmt.Sprintf("%016x", tombSid)
		wireTomb, _ := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{{
			TraceBytes: tombTid, SpanBytes: tombSid,
			Name: "tombstone-deleted-trace", Kind: "LLM", Model: "gpt-4o", Provider: "openai",
			Status: ptrace.StatusCodeOk, StartTime: now.Add(10 * time.Second), EndTime: now.Add(11 * time.Second),
			SessionExt: "sess-tombstone", UserExt: "user-tombstone",
			Input:        `{"messages":[{"role":"user","content":"TOMBSTONE_MUST_NOT_APPEAR"}]}`,
			Output:       `{"choices":[{"index":0,"message":{"role":"assistant","content":"gone"}}]}`,
			PromptTokens: 50, CompTokens: 20,
			ExtraStr: map[string]string{"env": "prod", "tomb_marker": "should_be_deleted"},
		}})
		if err := sendLiveOTLP(podA, orgID, projectID, wireTomb); err != nil {
			t.Fatalf("send tombstone trace: %v", err)
		}

		// ── 4. VERY SPARSE corpus spread across 90 days (4 monthly ClickHouse partitions!) ──
		sp85Tid := makeUUIDBytes(0x60001)
		sp85Sid := makeSpanIDBytes(0x60001001)
		sp85In, _ := json.Marshal(map[string]any{
			"model": "gpt-4o-mini", "tools": tools[:2],
			"messages": []map[string]any{
				{"role": "system", "content": sysPrompt},
				{"role": "user", "content": "SPARSE_85D_NEEDLE: verify 3-month retention query"},
			},
		})
		sp85Out, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "SPARSE_85D_REPLY verified"}}}})
		m.SparseOld85dTraceID = formatDashedUUID(sp85Tid)
		m.SparseOld85dSpanID = fmt.Sprintf("%016x", sp85Sid)
		m.SparseOld85dInput = string(sp85In)
		m.SparseOld85dOutput = string(sp85Out)
		m.SparseOldSessionExt = "sess-sparse-85d"
		m.SparseOldUserExt = "user-sparse-85d"

		giant45KBIn, _ := json.Marshal(map[string]any{
			"model": "claude-sonnet-4-6",
			"messages": []map[string]any{
				{"role": "system", "content": sysPrompt},
				{"role": "user", "content": "GIANT_45KB_NEEDLE " + realisticProse(9991, 4200)},
			},
		})
		m.SparseNullTraceID = formatDashedUUID(makeUUIDBytes(0x60002))
		m.SparseEmptyIOTraceID = formatDashedUUID(makeUUIDBytes(0x60003))
		m.SparsePlaintextTraceID = formatDashedUUID(makeUUIDBytes(0x60004))
		m.SparseMalformedTraceID = formatDashedUUID(makeUUIDBytes(0x60005))
		m.SparseGiant45KBTraceID = formatDashedUUID(makeUUIDBytes(0x60006))
		m.SparseGiant45KBInput = string(giant45KBIn)
		m.SparseUnknownCostTraceID = formatDashedUUID(makeUUIDBytes(0x60007))

		sparseSpecs := []liveSpanSpec{
			{
				TraceBytes: sp85Tid, SpanBytes: sp85Sid,
				Name: "sparse-85d-archive-trace", Kind: "LLM", Model: "gpt-4o-mini", Provider: "openai",
				Status: ptrace.StatusCodeOk, StartTime: now.Add(-85 * 24 * time.Hour), EndTime: now.Add(-85*24*time.Hour + 300*time.Millisecond),
				SessionExt: "sess-sparse-85d", UserExt: "user-sparse-85d",
				Input: string(sp85In), Output: string(sp85Out), PromptTokens: 150, CompTokens: 40,
				ExtraStr:  map[string]string{"env": "canary", "region": "ap-south", "team": "archive"},
				ExtraNum:  map[string]float64{"tier_score": 7.5},
				ExtraBool: map[string]bool{"is_vip": true},
				ExtraArr:  map[string][]string{"tags_arr": {"legacy", "archive"}},
			},
			{
				TraceBytes: makeUUIDBytes(0x60002), SpanBytes: makeSpanIDBytes(0x60002001),
				Name: "sparse-null-sess-user", Kind: "LLM", Model: "gpt-4o", Provider: "openai",
				Status: ptrace.StatusCodeOk, StartTime: now.Add(-60 * 24 * time.Hour), EndTime: now.Add(-60*24*time.Hour + 100*time.Millisecond),
				Input:        `{"messages":[{"role":"user","content":"Null session and user prompt"}]}`,
				Output:       `{"choices":[{"index":0,"message":{"role":"assistant","content":"Null session reply"}}]}`,
				PromptTokens: 30, CompTokens: 15,
			},
			{
				TraceBytes: makeUUIDBytes(0x60003), SpanBytes: makeSpanIDBytes(0x60003001),
				Name: "sparse-empty-io", Kind: "CHAIN",
				Status: ptrace.StatusCodeOk, StartTime: now.Add(-45 * 24 * time.Hour), EndTime: now.Add(-45*24*time.Hour + 10*time.Millisecond),
				SessionExt: "sess-sparse-empty", UserExt: "user-sparse-empty",
			},
			{
				TraceBytes: makeUUIDBytes(0x60004), SpanBytes: makeSpanIDBytes(0x60004001),
				Name: "sparse-plaintext", Kind: "LLM", Model: "gpt-4o", Provider: "openai",
				Status: ptrace.StatusCodeOk, StartTime: now.Add(-30 * 24 * time.Hour), EndTime: now.Add(-30*24*time.Hour + 50*time.Millisecond),
				Input: "Plain non-JSON question: what is 2+2?", Output: "Plain non-JSON answer: 4",
				PromptTokens: 12, CompTokens: 4,
			},
			{
				TraceBytes: makeUUIDBytes(0x60005), SpanBytes: makeSpanIDBytes(0x60005001),
				Name: "sparse-malformed-json", Kind: "LLM", Model: "gpt-4o", Provider: "openai",
				Status: ptrace.StatusCodeError, StatusMsg: "malformed JSON upstream",
				StartTime: now.Add(-20 * 24 * time.Hour), EndTime: now.Add(-20*24*time.Hour + 50*time.Millisecond),
				Input: "{broken: json [not closed", Output: "{also broken",
				PromptTokens: 5, CompTokens: 0,
			},
			{
				TraceBytes: makeUUIDBytes(0x60006), SpanBytes: makeSpanIDBytes(0x60006001),
				Name: "sparse-giant-45kb", Kind: "LLM", Model: "claude-sonnet-4-6", Provider: "anthropic",
				Status: ptrace.StatusCodeOk, StartTime: now.Add(-10 * 24 * time.Hour), EndTime: now.Add(-10*24*time.Hour + 800*time.Millisecond),
				SessionExt: "sess-sparse-giant", UserExt: "user-sparse-giant",
				Input: string(giant45KBIn), Output: `{"choices":[{"index":0,"message":{"role":"assistant","content":"Giant 45KB reply"}}]}`,
				PromptTokens: 11000, CompTokens: 30,
			},
			{
				TraceBytes: makeUUIDBytes(0x60007), SpanBytes: makeSpanIDBytes(0x60007001),
				Name: "sparse-unknown-cost", Kind: "LLM", Model: "custom-local-llama", Provider: "vllm",
				Status: ptrace.StatusCodeOk, StartTime: now.Add(-5 * 24 * time.Hour), EndTime: now.Add(-5*24*time.Hour + 120*time.Millisecond),
				Input:        `{"messages":[{"role":"user","content":"Unknown cost test"}]}`,
				Output:       `{"choices":[{"index":0,"message":{"role":"assistant","content":"ok"}}]}`,
				PromptTokens: 20, CompTokens: 10,
				ExtraStr: map[string]string{"fi.cost.kind": "unknown"},
			},
		}
		for day := 1; day <= 45; day++ {
			ts := now.Add(-time.Duration(day*2) * 24 * time.Hour)
			sparseSpecs = append(sparseSpecs, liveSpanSpec{
				TraceBytes: makeUUIDBytes(uint64(0x61000 + day)), SpanBytes: makeSpanIDBytes(uint64(0x6100000 + day)),
				Name: fmt.Sprintf("sparse-day-%02d", day), Kind: "LLM", Model: "gpt-4o-mini", Provider: "openai",
				Status: ptrace.StatusCodeOk, StartTime: ts, EndTime: ts.Add(80 * time.Millisecond),
				SessionExt: fmt.Sprintf("sess-sparse-tail-%02d", day), UserExt: fmt.Sprintf("user-sparse-tail-%02d", day),
				Input:        fmt.Sprintf(`{"messages":[{"role":"user","content":"Sparse day %d query"}]}`, day),
				Output:       fmt.Sprintf(`{"choices":[{"index":0,"message":{"role":"assistant","content":"Sparse day %d answer"}}]}`, day),
				PromptTokens: 40, CompTokens: 15,
				ExtraStr:  map[string]string{"env": "prod", "region": "us-west", "team": "longtail"},
				ExtraNum:  map[string]float64{"tier_score": float64(day)},
				ExtraBool: map[string]bool{"is_vip": day%5 == 0},
			})
		}
		for _, sp := range sparseSpecs {
			wire, err := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{sp})
			if err != nil {
				t.Fatalf("encode sparse %s: %v", sp.Name, err)
			}
			if err := sendLiveOTLP(podA, orgID, projectID, wire); err != nil {
				t.Fatalf("send sparse %s: %v", sp.Name, err)
			}
		}

		// ── 5. VERY DENSE HIGH-VOLUME SCALE BURST: 15,000 spans (300 traces × 25 LLM + 25 Tool) across 16 workers! ──
		// First 30 traces pack into sess-dense-whale / user-dense-whale (1,500 spans in one session/user!).
		m.DenseWhaleSessionExt = "sess-dense-whale"
		m.DenseWhaleUserExt = "user-dense-whale"

		const (
			scaleTraces  = 300
			scaleTurns   = 25
			scaleWorkers = 16
		)
		startScale := time.Now()
		var latMu sync.Mutex
		latenciesMS := make([]float64, 0, scaleTraces*scaleTurns)
		var totalSpansSent atomic.Int64

		jobs := make(chan int, scaleTraces)
		for tr := 0; tr < scaleTraces; tr++ {
			jobs <- tr
		}
		close(jobs)

		envs := []string{"prod", "staging", "canary"}
		regions := []string{"us-east", "eu-west", "ap-south"}
		teams := []string{"payments", "search", "support"}
		models := []string{"gpt-4o", "claude-sonnet-4-6", "gemini-2.5-pro"}
		providers := []string{"openai", "anthropic", "google"}

		var wg sync.WaitGroup
		errCh := make(chan error, scaleWorkers)
		for wIdx := 0; wIdx < scaleWorkers; wIdx++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for tr := range jobs {
					tid := makeUUIDBytes(uint64(0x70000 + tr))
					rootSid := makeSpanIDBytes(uint64(0x70000000 + tr*1000))
					sessExt := fmt.Sprintf("sess-dense-%03d", tr%40)
					userExt := fmt.Sprintf("user-dense-%02d", tr%20)
					if tr < 30 {
						sessExt = "sess-dense-whale"
						userExt = "user-dense-whale"
					}
					env := envs[tr%len(envs)]
					reg := regions[tr%len(regions)]
					team := teams[tr%len(teams)]
					mod := models[tr%len(models)]
					prov := providers[tr%len(providers)]
					baseT := now.Add(time.Duration(tr) * 1500 * time.Millisecond)
					msgs := []map[string]any{{"role": "system", "content": sysPrompt}}

					for turn := 1; turn <= scaleTurns; turn++ {
						msgs = append(msgs, map[string]any{
							"role":    "user",
							"content": fmt.Sprintf("Scale tr=%03d turn=%02d %s", tr, turn, realisticProse(uint64(tr*100+turn), 45)),
						})
						inB, _ := json.Marshal(map[string]any{"model": mod, "tools": tools[:6], "messages": msgs})
						reply := map[string]any{
							"role":    "assistant",
							"content": fmt.Sprintf("Scale reply tr=%03d turn=%02d %s", tr, turn, realisticProse(uint64(tr*100+turn+50), 40)),
						}
						outB, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"index": 0, "message": reply}}})

						llmSid := makeSpanIDBytes(uint64(0x70000000 + tr*1000 + turn*2))
						toolSid := makeSpanIDBytes(uint64(0x70000000 + tr*1000 + turn*2 + 1))
						parent := rootSid
						if turn == 1 {
							llmSid = rootSid
							parent = [8]byte{}
						}
						st := ptrace.StatusCodeOk
						if tr%15 == 0 && turn == scaleTurns {
							st = ptrace.StatusCodeError
						}
						specs := []liveSpanSpec{
							{
								TraceBytes: tid, SpanBytes: llmSid, ParentBytes: parent,
								Name: "scale-dense-agent", Kind: "LLM", Model: mod, Provider: prov,
								Status:     st,
								StartTime:  baseT.Add(time.Duration(turn) * 30 * time.Millisecond),
								EndTime:    baseT.Add(time.Duration(turn)*30*time.Millisecond + time.Duration(50+(tr%20)*20)*time.Millisecond),
								SessionExt: sessExt, UserExt: userExt,
								Input: string(inB), Output: string(outB),
								PromptTokens: int64(500 + turn*80), CompTokens: 90,
								ExtraStr:  map[string]string{"env": env, "region": reg, "team": team},
								ExtraNum:  map[string]float64{"tier_score": float64(10 + (tr % 90))},
								ExtraBool: map[string]bool{"is_vip": tr%3 == 0},
								ExtraArr:  map[string][]string{"tags_arr": {team, env}},
							},
							{
								TraceBytes: tid, SpanBytes: toolSid, ParentBytes: llmSid,
								Name: "scale-dense-tool", Kind: "TOOL",
								Status:     ptrace.StatusCodeOk,
								StartTime:  baseT.Add(time.Duration(turn)*30*time.Millisecond + 10*time.Millisecond),
								EndTime:    baseT.Add(time.Duration(turn)*30*time.Millisecond + 25*time.Millisecond),
								SessionExt: sessExt, UserExt: userExt,
								Input:    fmt.Sprintf(`{"tool":"lookup","turn":%d}`, turn),
								Output:   `{"ok":true}`,
								ExtraStr: map[string]string{"env": env, "region": reg, "team": team},
							},
						}
						wire, err := encodeLiveOTLP(projectID, orgID, specs)
						if err != nil {
							errCh <- err
							return
						}
						pod := podA
						if (tr+turn)%2 == 1 {
							pod = podB
						}
						t0 := time.Now()
						if err := sendLiveOTLP(pod, orgID, projectID, wire); err != nil {
							errCh <- err
							return
						}
						el := float64(time.Since(t0).Microseconds()) / 1000.0
						latMu.Lock()
						latenciesMS = append(latenciesMS, el)
						latMu.Unlock()
						totalSpansSent.Add(2)
						msgs = append(msgs, reply)
					}
				}
			}()
		}
		wg.Wait()
		close(errCh)
		for err := range errCh {
			t.Fatalf("scale worker error: %v", err)
		}

		stopAndWait := func(s *Server) {
			select {
			case <-s.stopCh:
			default:
				close(s.stopCh)
			}
			s.wg.Wait()
			s.drainNow(context.Background())
		}
		stopAndWait(podA)
		stopAndWait(podB)
		dur := time.Since(startScale)

		if wDedup.Snapshot().BatchesFailed > 0 || wDedup.Snapshot().RowsDeadLettered > 0 {
			t.Fatalf("scale writer failures: %+v", wDedup.Snapshot())
		}
		sort.Float64s(latenciesMS)
		pct := func(p float64) float64 {
			if len(latenciesMS) == 0 {
				return 0
			}
			return latenciesMS[int(float64(len(latenciesMS)-1)*p)]
		}
		m.ScaleTotalSpansSent = int(totalSpansSent.Load()) + 150 + len(sparseSpecs) + 2
		m.ScaleDurationMS = dur.Milliseconds()
		m.ScaleThroughputSPS = float64(totalSpansSent.Load()) / dur.Seconds()
		m.ScaleP50MS = pct(0.50)
		m.ScaleP95MS = pct(0.95)
		m.ScaleP99MS = pct(0.99)
		saveManifest(m)
		t.Logf("DENSE+SPARSE SCALE INGEST COMPLETE: %d burst spans in %s (%.1f spans/s, p50=%.2fms p95=%.2fms p99=%.2fms), writer=%+v",
			totalSpansSent.Load(), dur.Round(time.Millisecond), m.ScaleThroughputSPS, m.ScaleP50MS, m.ScaleP95MS, m.ScaleP99MS, wDedup.Snapshot())

	case "mega_pipeline":
		// High-volume live pipeline stage: pushes 100,000+ OTLP spans through 2
		// real collector pods sharing Redis 7 + ClickHouse 25.3 (Phase 0 + Phase 1 ON),
		// PLUS targeted probes for every audited edge case (multimodal items_v1 past
		// 512B, half-extracted items_v1, gen_ai.prompt.template.variables preservation,
		// AsyncInsert=true synchronous trajectory_items durability, JSON maps/arrays,
		// 6KB custom strings, 20KB overflow strings, and dataset multi-span trees).
		wDedup := newWriter("default", chURL)
		defer wDedup.Close()
		wAsync, err := chwriter.New(chwriter.Config{
			URL:            chURL,
			Database:       "default",
			Table:          "spans",
			AsyncInsert:    true,
			MaxRetries:     3,
			DeadLetterFile: filepath.Join(t.TempDir(), "async_dlq.jsonl"),
		})
		if err != nil {
			t.Fatalf("chwriter async: %v", err)
		}
		defer wAsync.Close()

		tCfg := trajectory.Config{
			Mode:             trajectory.ModeOn,
			CompactSpanAttrs: true,
			BillDedupedBytes: true,
			LRUSize:          250_000,
			SessionTTL:       2 * time.Hour,
			ProjectTTL:       24 * time.Hour,
		}
		rdb.FlushAll(context.Background())
		usage := auth.NewUsageEmitter(rdb, nil, log, 500_000)
		seenA := trajectory.NewSeenStore(rdb, tCfg.LRUSize, tCfg.SessionTTL, tCfg.ProjectTTL)
		seenB := trajectory.NewSeenStore(rdb, tCfg.LRUSize, tCfg.SessionTTL, tCfg.ProjectTTL)
		podA := New(Config{BatchMaxRows: 1000, BatchMaxAge: 100 * time.Millisecond, IngestBudgetMiB: 256, InsertConcurrency: 4}, wDedup, nil, usage, NoopMetering{}, WithLogger(log), WithTrajectory(tCfg, seenA))
		podB := New(Config{BatchMaxRows: 1000, BatchMaxAge: 100 * time.Millisecond, IngestBudgetMiB: 256, InsertConcurrency: 4}, wDedup, nil, usage, NoopMetering{}, WithLogger(log), WithTrajectory(tCfg, seenB))
		podAsync := New(Config{BatchMaxRows: 200, BatchMaxAge: 50 * time.Millisecond, IngestBudgetMiB: 64}, wAsync, nil, NoopUsageEmitter{}, NoopMetering{}, WithLogger(log), WithTrajectory(tCfg, seenA))
		startFlushers(t, podA)
		startFlushers(t, podB)
		startFlushers(t, podAsync)

		m := loadManifest()
		now := time.Now().UTC().Add(-2 * time.Minute)

		// 1. Targeted Edge Probes (multimodal >512B, half-extracted, prompt template metadata, AsyncInsert=true)
		// 1a. Multimodal items_v1 span with data:image/png;base64 past byte 512 (tests activity_by_trace_ids!).
		mmTid := makeUUIDBytes(0x700101)
		mmRoot := makeSpanIDBytes(0x70010100)
		mmChild := makeSpanIDBytes(0x70010101)
		mmInputObj := map[string]any{
			"messages": []map[string]any{
				{"role": "system", "content": "System instructions " + strings.Repeat("policy ", 120)},
				{"role": "user", "content": []map[string]any{
					{"type": "text", "text": "Inspect this screenshot after long preamble: " + strings.Repeat("context ", 100)},
					{"type": "image_url", "image_url": map[string]any{"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"}},
				}},
			},
		}
		mmInB, _ := json.Marshal(mmInputObj)
		mmOutB := []byte(`{"choices":[{"index":0,"message":{"role":"assistant","content":"Screenshot verified cleanly."}}]}`)
		m.MegaMultimodalTraceID = formatDashedUUID(mmTid)
		m.MegaMultimodalSpanID = fmt.Sprintf("%016x", mmChild)

		// 1b. Half-extracted items_v1 span (messages array input -> extracted; plain text 3KB output -> stays inline!).
		heTid := makeUUIDBytes(0x700102)
		heSid := makeSpanIDBytes(0x70010201)
		heInB, _ := json.Marshal(map[string]any{
			"messages": []map[string]any{
				{"role": "system", "content": "Half-extract test prompt"},
				{"role": "user", "content": "Return plain text report"},
			},
		})
		heOut := "PLAIN_TEXT_REPORT_START:" + strings.Repeat("abcdef0123 ", 300) + ":PLAIN_TEXT_REPORT_END"
		m.MegaHalfExtractTraceID = formatDashedUUID(heTid)
		m.MegaHalfExtractSpanID = fmt.Sprintf("%016x", heSid)
		m.MegaHalfExtractOutput = heOut

		// 1c. Prompt template metadata preservation (gen_ai.prompt.name + gen_ai.prompt.template.variables + gen_ai.prompt.0.content) + JSON map attr!
		pmTid := makeUUIDBytes(0x700103)
		pmSid := makeSpanIDBytes(0x70010301)
		pmChild := makeSpanIDBytes(0x70010302)
		pmInB, _ := json.Marshal(map[string]any{
			"messages": []map[string]any{
				{"role": "system", "content": "Template system prompt v42"},
				{"role": "user", "content": "Render template with gold tier"},
			},
			"tools": []map[string]any{
				{"type": "function", "function": map[string]any{"name": "lookup_tier", "parameters": map[string]any{"type": "object"}}},
			},
		})
		pmOutB := []byte(`{"choices":[{"index":0,"message":{"role":"assistant","content":"Rendered gold template."}}]}`)
		m.MegaPromptMetaTraceID = formatDashedUUID(pmTid)
		m.MegaPromptMetaSpanID = fmt.Sprintf("%016x", pmSid)

		// Build targeted OTLP payload manually for map attribute support on pmSid.
		tdEdge := ptrace.NewTraces()
		rsEdge := tdEdge.ResourceSpans().AppendEmpty()
		rsEdge.Resource().Attributes().PutStr("service.name", "checkout-agent-svc")
		rsEdge.Resource().Attributes().PutStr("project_name", "agent-proj")
		rsEdge.Resource().Attributes().PutStr("fi.project_id", projectID)
		rsEdge.Resource().Attributes().PutStr("fi.org_id", orgID)
		ssEdge := rsEdge.ScopeSpans().AppendEmpty()
		addEdgeSpan := func(s liveSpanSpec, extraMap map[string]string) {
			sp := ssEdge.Spans().AppendEmpty()
			sp.SetTraceID(pcommon.TraceID(s.TraceBytes))
			sp.SetSpanID(pcommon.SpanID(s.SpanBytes))
			if s.ParentBytes != ([8]byte{}) {
				sp.SetParentSpanID(pcommon.SpanID(s.ParentBytes))
			}
			sp.SetName(s.Name)
			sp.SetStartTimestamp(pcommon.NewTimestampFromTime(s.StartTime))
			sp.SetEndTimestamp(pcommon.NewTimestampFromTime(s.EndTime))
			sp.Status().SetCode(s.Status)
			a := sp.Attributes()
			a.PutStr("fi.span.kind", s.Kind)
			if s.Model != "" {
				a.PutStr("gen_ai.request.model", s.Model)
			}
			if s.Provider != "" {
				a.PutStr("gen_ai.provider.name", s.Provider)
			}
			if s.SessionExt != "" {
				a.PutStr("session.id", s.SessionExt)
			}
			if s.UserExt != "" {
				a.PutStr("user.id", s.UserExt)
			}
			if s.Input != "" {
				a.PutStr("input.value", s.Input)
			}
			if s.Output != "" {
				a.PutStr("output.value", s.Output)
			}
			a.PutInt("gen_ai.usage.input_tokens", s.PromptTokens)
			a.PutInt("gen_ai.usage.output_tokens", s.CompTokens)
			for k, v := range s.ExtraStr {
				a.PutStr(k, v)
			}
			for k, v := range s.ExtraNum {
				a.PutDouble(k, v)
			}
			for k, v := range s.ExtraBool {
				a.PutBool(k, v)
			}
			for k, vals := range s.ExtraArr {
				sl := a.PutEmptySlice(k)
				for _, item := range vals {
					sl.AppendEmpty().SetStr(item)
				}
			}
			if extraMap != nil {
				mp := a.PutEmptyMap("custom.map_meta")
				for mk, mv := range extraMap {
					mp.PutStr(mk, mv)
				}
			}
		}
		addEdgeSpan(liveSpanSpec{
			TraceBytes: mmTid, SpanBytes: mmRoot, Name: "multimodal-root", Kind: "AGENT",
			Status: ptrace.StatusCodeOk, StartTime: now, EndTime: now.Add(500 * time.Millisecond),
			SessionExt: "sess-mega-mm", UserExt: "user-mega-mm", Input: "check image", Output: "checked",
			PromptTokens: 10, CompTokens: 5,
		}, nil)
		addEdgeSpan(liveSpanSpec{
			TraceBytes: mmTid, SpanBytes: mmChild, ParentBytes: mmRoot, Name: "multimodal-llm", Kind: "LLM",
			Model: "gpt-4o", Provider: "openai", Status: ptrace.StatusCodeOk,
			StartTime: now.Add(50 * time.Millisecond), EndTime: now.Add(400 * time.Millisecond),
			SessionExt: "sess-mega-mm", UserExt: "user-mega-mm", Input: string(mmInB), Output: string(mmOutB),
			PromptTokens: 450, CompTokens: 60,
		}, nil)
		addEdgeSpan(liveSpanSpec{
			TraceBytes: heTid, SpanBytes: heSid, Name: "half-extract-llm", Kind: "LLM",
			Model: "gpt-4o", Provider: "openai", Status: ptrace.StatusCodeOk,
			StartTime: now.Add(1 * time.Second), EndTime: now.Add(1200 * time.Millisecond),
			SessionExt: "sess-mega-he", UserExt: "user-mega-he", Input: string(heInB), Output: heOut,
			PromptTokens: 180, CompTokens: 600,
		}, nil)
		addEdgeSpan(liveSpanSpec{
			TraceBytes: pmTid, SpanBytes: pmSid, Name: "prompt-meta-root", Kind: "LLM",
			Model: "gpt-4o", Provider: "openai", Status: ptrace.StatusCodeOk,
			StartTime: now.Add(2 * time.Second), EndTime: now.Add(2400 * time.Millisecond),
			SessionExt: "sess-mega-pm", UserExt: "user-mega-pm", Input: string(pmInB), Output: string(pmOutB),
			PromptTokens: 320, CompTokens: 40,
			ExtraStr: map[string]string{
				"gen_ai.prompt.name":               "checkout-template-v42",
				"gen_ai.prompt.template.variables": `{"tier":"gold","locale":"en-US"}`,
				"gen_ai.prompt.0.content":          "indexed prompt blob stripped on items_v1",
				"env":                              "prod",
				"custom.medium_6kb":                "MEDIUM_6KB_ANCHOR_" + strings.Repeat("x", 6000),
				"custom.oversized_20kb":            "OVER_20KB_BLOB_" + strings.Repeat("z", 20000),
				"unicode.label":                    "東京-scale-check",
			},
			ExtraNum:  map[string]float64{"tier_score": 99.5},
			ExtraBool: map[string]bool{"is_vip": true},
			ExtraArr:  map[string][]string{"tags_arr": {"mega-gold", "checkout-v42"}},
		}, map[string]string{"plan": "enterprise", "region": "ap-northeast"})
		addEdgeSpan(liveSpanSpec{
			TraceBytes: pmTid, SpanBytes: pmChild, ParentBytes: pmSid, Name: "prompt-meta-child-llm", Kind: "LLM",
			Model: "claude-3-5-sonnet", Provider: "anthropic", Status: ptrace.StatusCodeOk,
			StartTime: now.Add(2100 * time.Millisecond), EndTime: now.Add(2350 * time.Millisecond),
			SessionExt: "sess-mega-pm", UserExt: "user-mega-pm", Input: string(pmInB), Output: string(pmOutB),
			PromptTokens: 330, CompTokens: 45,
			ExtraStr: map[string]string{"gen_ai.prompt.name": "checkout-child-v42"},
		}, nil)
		edgeWire, err := ptraceotlp.NewExportRequestFromTraces(tdEdge).MarshalProto()
		if err != nil {
			t.Fatalf("encode edge traces: %v", err)
		}
		if err := sendLiveOTLP(podA, orgID, projectID, edgeWire); err != nil {
			t.Fatalf("send edge traces: %v", err)
		}

		// 1d. AsyncInsert=true probe: proves InsertTable forces async_insert=0!
		aiTid := makeUUIDBytes(0x700104)
		aiSid := makeSpanIDBytes(0x70010401)
		aiInB, _ := json.Marshal(map[string]any{
			"messages": []map[string]any{
				{"role": "system", "content": "Async insert sync-item contract prompt"},
				{"role": "user", "content": "Verify trajectory_items commits synchronously"},
			},
		})
		m.MegaAsyncInsertTraceID = formatDashedUUID(aiTid)
		m.MegaAsyncInsertSpanID = fmt.Sprintf("%016x", aiSid)
		m.MegaAsyncInsertInput = string(aiInB)
		aiWire, _ := encodeLiveOTLP(projectID, orgID, []liveSpanSpec{{
			TraceBytes: aiTid, SpanBytes: aiSid, Name: "async-insert-probe", Kind: "LLM",
			Model: "gpt-4o", Provider: "openai", Status: ptrace.StatusCodeOk,
			StartTime: now.Add(3 * time.Second), EndTime: now.Add(3200 * time.Millisecond),
			SessionExt: "sess-mega-async", UserExt: "user-mega-async",
			Input: string(aiInB), Output: `{"choices":[{"index":0,"message":{"role":"assistant","content":"Sync confirmed"}}]}`,
			PromptTokens: 90, CompTokens: 20,
		}})
		if err := sendLiveOTLP(podAsync, orgID, projectID, aiWire); err != nil {
			t.Fatalf("send async probe: %v", err)
		}

		// 2. Massive Live Pipeline Burst: 100,000 OTLP spans across 2,500 traces
		//    (20 turns x 2 spans/turn = 40 spans/trace = 100,000 spans!) through
		//    2 concurrent collector pods + shared Redis 7 + ClickHouse 25.3!
		const (
			megaWorkers = 10
			megaTraces  = 2500
			megaTurns   = 20
		)
		sharedTools := make([]map[string]any, 6)
		for i := range sharedTools {
			sharedTools[i] = map[string]any{
				"type": "function",
				"function": map[string]any{
					"name":        fmt.Sprintf("mega_tool_%02d", i),
					"description": fmt.Sprintf("Enterprise tool %d: %s", i, realisticProse(uint64(5000+i), 25)),
					"parameters":  map[string]any{"type": "object", "properties": map[string]any{"id": map[string]any{"type": "string"}}},
				},
			}
		}
		sharedSys := "MEGA_SYSTEM_PROMPT: " + realisticProse(0xbeef01, 140)
		envs := []string{"prod", "staging", "canary"}
		regions := []string{"us-east", "eu-west", "ap-south"}
		models := []string{"gpt-4o", "claude-3-5-sonnet", "gemini-2.5-pro"}
		providers := []string{"openai", "anthropic", "google"}

		var dsMu sync.Mutex
		var totalSent atomic.Int64
		var latMu sync.Mutex
		latencies := make([]float64, 0, megaTraces*2)
		startMega := time.Now()
		errCh := make(chan error, megaWorkers)
		var wg sync.WaitGroup
		perWorker := megaTraces / megaWorkers
		for wIdx := 0; wIdx < megaWorkers; wIdx++ {
			wID := wIdx
			wg.Add(1)
			go func() {
				defer wg.Done()
				for k := 0; k < perWorker; k++ {
					tr := wID*perWorker + k
					tid := makeUUIDBytes(uint64(0x900000 + tr))
					rootSid := makeSpanIDBytes(uint64(0x90000000 + tr*100))
					sessExt := fmt.Sprintf("sess-mega-%04d", tr%250)
					userExt := fmt.Sprintf("user-mega-%03d", tr%100)
					env := envs[tr%len(envs)]
					reg := regions[tr%len(regions)]
					mod := models[tr%len(models)]
					prov := providers[tr%len(providers)]
					baseT := now.Add(-time.Duration(tr%120) * time.Second)

					msgs := make([]map[string]any, 0, megaTurns*2+1)
					msgs = append(msgs, map[string]any{"role": "system", "content": sharedSys})
					// Pack 40 spans per trace into 2 OTLP requests (20 spans each) for realistic batching.
					allSpecs := make([]liveSpanSpec, 0, megaTurns*2)
					for turn := 1; turn <= megaTurns; turn++ {
						msgs = append(msgs, map[string]any{
							"role":    "user",
							"content": fmt.Sprintf("Mega trace %d turn %d: %s", tr, turn, realisticProse(uint64(tr*100+turn), 16)),
						})
						inB, _ := json.Marshal(map[string]any{"model": mod, "tools": sharedTools, "messages": msgs})
						reply := map[string]any{
							"role":    "assistant",
							"content": fmt.Sprintf("Mega reply tr=%d turn=%d: %s", tr, turn, realisticProse(uint64(tr*100+turn+7777), 16)),
						}
						outB, _ := json.Marshal(map[string]any{"choices": []map[string]any{{"index": 0, "message": reply}}})
						llmSid := makeSpanIDBytes(uint64(0x90000000 + tr*100 + turn*2))
						toolSid := makeSpanIDBytes(uint64(0x90000000 + tr*100 + turn*2 + 1))
						parent := rootSid
						if turn == 1 {
							llmSid = rootSid
							parent = [8]byte{}
						}
						st := ptrace.StatusCodeOk
						if tr%50 == 0 && turn == megaTurns {
							st = ptrace.StatusCodeError
						}
						allSpecs = append(allSpecs,
							liveSpanSpec{
								TraceBytes: tid, SpanBytes: llmSid, ParentBytes: parent,
								Name: "mega-agent-llm", Kind: "LLM", Model: mod, Provider: prov,
								Status:     st,
								StartTime:  baseT.Add(time.Duration(turn) * 15 * time.Millisecond),
								EndTime:    baseT.Add(time.Duration(turn)*15*time.Millisecond + 40*time.Millisecond),
								SessionExt: sessExt, UserExt: userExt,
								Input: string(inB), Output: string(outB),
								PromptTokens: int64(400 + turn*50), CompTokens: 70,
								ExtraStr:  map[string]string{"env": env, "region": reg, "gen_ai.prompt.name": fmt.Sprintf("mega-prompt-%02d", tr%10)},
								ExtraNum:  map[string]float64{"tier_score": float64(1 + tr%100)},
								ExtraBool: map[string]bool{"is_vip": tr%4 == 0},
							},
							liveSpanSpec{
								TraceBytes: tid, SpanBytes: toolSid, ParentBytes: llmSid,
								Name: "mega-agent-tool", Kind: "TOOL", Status: ptrace.StatusCodeOk,
								StartTime:  baseT.Add(time.Duration(turn)*15*time.Millisecond + 5*time.Millisecond),
								EndTime:    baseT.Add(time.Duration(turn)*15*time.Millisecond + 12*time.Millisecond),
								SessionExt: sessExt, UserExt: userExt,
								Input:  fmt.Sprintf(`{"tool":"mega_tool_%02d","tr":%d,"turn":%d}`, turn%6, tr, turn),
								Output: `{"ok":true}`,
							},
						)
						msgs = append(msgs, reply)
					}
					if tr < 60 {
						dsMu.Lock()
						m.MegaDatasetTraceIDs = append(m.MegaDatasetTraceIDs, formatDashedUUID(tid))
						for _, sp := range allSpecs {
							m.MegaDatasetSpanIDs = append(m.MegaDatasetSpanIDs, fmt.Sprintf("%016x", sp.SpanBytes))
						}
						dsMu.Unlock()
					}
					for chunk := 0; chunk < len(allSpecs); chunk += 20 {
						end := min(chunk+20, len(allSpecs))
						wire, err := encodeLiveOTLP(projectID, orgID, allSpecs[chunk:end])
						if err != nil {
							errCh <- err
							return
						}
						pod := podA
						if (tr+chunk)%2 == 1 {
							pod = podB
						}
						t0 := time.Now()
						if err := sendLiveOTLP(pod, orgID, projectID, wire); err != nil {
							errCh <- err
							return
						}
						el := float64(time.Since(t0).Microseconds()) / 1000.0
						latMu.Lock()
						latencies = append(latencies, el)
						latMu.Unlock()
						totalSent.Add(int64(end - chunk))
					}
				}
			}()
		}
		wg.Wait()
		close(errCh)
		for err := range errCh {
			t.Fatalf("mega worker error: %v", err)
		}

		stopPod := func(s *Server) {
			select {
			case <-s.stopCh:
			default:
				close(s.stopCh)
			}
			s.wg.Wait()
			s.drainNow(context.Background())
		}
		stopPod(podA)
		stopPod(podB)
		stopPod(podAsync)
		dur := time.Since(startMega)

		if wDedup.Snapshot().BatchesFailed > 0 || wDedup.Snapshot().RowsDeadLettered > 0 {
			t.Fatalf("mega writer failures: %+v", wDedup.Snapshot())
		}
		if wAsync.Snapshot().BatchesFailed > 0 || wAsync.Snapshot().RowsDeadLettered > 0 {
			t.Fatalf("async writer failures: %+v", wAsync.Snapshot())
		}
		sort.Float64s(latencies)
		pct := func(p float64) float64 {
			if len(latencies) == 0 {
				return 0
			}
			return latencies[int(float64(len(latencies)-1)*p)]
		}
		entries, err := rdb.XRange(context.Background(), "usage:events", "-", "+").Result()
		if err != nil {
			t.Fatalf("XRange usage:events: %v", err)
		}
		var totRaw, totStored, totSaved int64
		var totNew, totReused int
		for _, e := range entries {
			var props struct {
				BillingBasis    string `json:"billing_basis"`
				RawBytes        int64  `json:"raw_bytes"`
				StoredBytes     int64  `json:"stored_bytes"`
				DedupSavedBytes int64  `json:"dedup_saved_bytes"`
				NewItems        int    `json:"new_items"`
				ReusedItems     int    `json:"reused_items"`
			}
			if rawProps, ok := e.Values["properties"].(string); ok {
				if err := json.Unmarshal([]byte(rawProps), &props); err == nil {
					if props.BillingBasis != "deduped_storage" {
						t.Fatalf("unexpected billing_basis: %q", props.BillingBasis)
					}
					totRaw += props.RawBytes
					totStored += props.StoredBytes
					totSaved += props.DedupSavedBytes
					totNew += props.NewItems
					totReused += props.ReusedItems
				}
			}
		}
		m.MegaPipelineSpansSent = int(totalSent.Load()) + 6
		m.MegaPipelineTracesSent = megaTraces + 4
		m.MegaPipelineDurationMS = dur.Milliseconds()
		m.MegaPipelineSPS = float64(totalSent.Load()) / dur.Seconds()
		m.MegaPipelineP50MS = pct(0.50)
		m.MegaPipelineP95MS = pct(0.95)
		m.MegaPipelineP99MS = pct(0.99)
		m.MegaRawBytes = totRaw
		m.MegaStoredBytes = totStored
		m.MegaSavedBytes = totSaved
		m.MegaNewItems = totNew
		m.MegaReusedItems = totReused
		saveManifest(m)
		t.Logf("MEGA PIPELINE COMPLETE: %d spans (%d traces) in %s (%.1f spans/s, p50=%.2fms p95=%.2fms p99=%.2fms), raw=%d stored=%d saved=%d (%.1f%%), new=%d reused=%d",
			m.MegaPipelineSpansSent, m.MegaPipelineTracesSent, dur.Round(time.Millisecond),
			m.MegaPipelineSPS, m.MegaPipelineP50MS, m.MegaPipelineP95MS, m.MegaPipelineP99MS,
			totRaw, totStored, totSaved, float64(totSaved)*100.0/float64(max(totRaw, 1)), totNew, totReused)

	default:
		t.Fatalf("unknown FI_LIVE_STAGE %q", stage)
	}
}
