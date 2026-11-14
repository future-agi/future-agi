package ingestbench

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestByteAdmissionNeverClaimsRowSufficiencyOrCrashBound(t *testing.T) {
	limits := CollectorLimits{AdmissionModel: "byte-leases-v1", IngestBudgetBytes: 3 << 20, WirePoolBytes: 1 << 20, WorkPoolBytes: 2 << 20, GRPCMaxRecvMiB: 0.75, GRPCMaxRecvBytes: 786432}
	b := CheckBudget(15000, 500, 16, &limits, 999999999)
	if b.Error != "" || b.Assessment != "not-assessed" || b.Sufficient || b.BatchFits || b.RequiredPendingRows != 0 || b.AvailablePendingRows != 0 || strings.Contains(b.RecommendedSetting, "FI_MAX_PENDING_ROWS=") {
		t.Fatalf("row sizing invented for byte leases: %+v", b)
	}
	rec := RecoveryResult{Faults: []FaultResult{{Name: "restart"}}}
	rec.attachVerification(testConfig(), Verification{Complete: true}, BacklogResult{}, b)
	if rec.LossWithinBound != nil || rec.LossBoundSource != "" || rec.LossBound != 0 {
		t.Fatalf("invented row crash bound: %+v", rec)
	}
	limits.WirePoolBytes = math.MaxInt64
	if got := CheckBudget(100, 4, 2, &limits, 0); got.Error == "" {
		t.Fatal("impossible byte pools accepted")
	}
	limits.AdmissionModel = "future-model"
	if got := CheckBudget(100, 4, 2, &limits, 0); got.Error == "" || got.Sufficient {
		t.Fatal("unknown model treated as legacy sufficient")
	}
}
func TestByteHealthPreservesActualLimitsPressureAndFractionalReceiveCap(t *testing.T) {
	body := []byte(`{"status":"ok","limits":{"admission_model":"byte-leases-v1","ingest_budget_bytes":3145728,"wire_pool_bytes":1048576,"work_pool_bytes":2097152,"grpc_max_recv_mib":0.75,"grpc_max_recv_bytes":786432},"ingest":{"budget_bytes":3145728,"wire":{"in_use_bytes":17},"work":{"in_use_bytes":1234},"rejected":{"grpc/work_busy/Unavailable":2}},"admission":{"buffered_rows":4,"pending_rows":1,"max_pending_rows":0},"stats":{"RowsInserted":2}}`)
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(body) }))
	defer ts.Close()
	info := FetchCollectorHealth(context.Background(), http.DefaultClient, ts.URL)
	if info.Error != "" || info.Limits == nil || info.Limits.GRPCMaxRecvMiB != 0.75 || info.Limits.GRPCMaxRecvBytes != 786432 || len(info.Ingest) == 0 {
		t.Fatalf("actual limits lost: %+v", info)
	}
	sample := ParseCollectorHealth(200, body)
	if !sample.OK || sample.WorkUsedBytes != 1234 || sample.WireUsedBytes != 17 || sample.IngestBudgetBytes != 3<<20 || sample.BufferedRows != 4 {
		t.Fatalf("sample lost byte pressure: %+v", sample)
	}
	raw, err := json.Marshal(sample)
	if err != nil || !strings.Contains(string(raw), "grpc/work_busy/Unavailable") {
		t.Fatalf("refusal evidence discarded: %s %v", raw, err)
	}
	result := summariseBacklog([]CollectorSample{sample}, 1, 0, time.Second, 1, 4, time.Second)
	if result.BytePoolSamples != 1 || result.PeakWorkBytes != 1234 || result.PeakWireBytes != 17 || result.AtBudgetSamples != 0 {
		t.Fatalf("row cap inferred from byte sample: %+v", result)
	}
}
