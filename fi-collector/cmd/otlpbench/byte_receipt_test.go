package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/ingestbench"
)

func byteCLIReceipt(t *testing.T) ingestbench.Result {
	t.Helper()
	var r ingestbench.Result
	if err := json.Unmarshal(strictReceiptSeed(t), &r); err != nil {
		t.Fatal(err)
	}
	r.Provenance.Collector.Limits = &ingestbench.CollectorLimits{AdmissionModel: "byte-leases-v1", IngestBudgetBytes: 3 << 20, WirePoolBytes: 1 << 20, WorkPoolBytes: 2 << 20}
	r.Provenance.Collector.Budget = ingestbench.CheckBudget(r.Config.Rate, r.Config.BatchSize, r.Config.Concurrency, r.Provenance.Collector.Limits, 0)
	return r
}

func TestByteCLIRejectsSingleFieldMutationsBeforeOutputs(t *testing.T) {
	for _, tc := range []struct {
		name, field string
		mutate      func(*ingestbench.Result)
	}{
		{"sample-subset", "byte_pool_samples", func(r *ingestbench.Result) { r.Backlog.BytePoolSamples = 1 }},
		{"pool-partition", "byte-pool", func(r *ingestbench.Result) { r.Provenance.Collector.Limits.WirePoolBytes++ }},
		{"sufficiency", "budget_check", func(r *ingestbench.Result) { r.Provenance.Collector.Budget.Sufficient = true }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := byteCLIReceipt(t)
			tc.mutate(&r)
			raw, err := json.Marshal(r)
			if err != nil {
				t.Fatal(err)
			}
			refuseReceipt(t, raw, tc.field)
		})
	}
}

func TestByteCLIValidAndHistoricalReceipts(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		r := byteCLIReceipt(t)
		if legacy {
			var historical ingestbench.Result
			if err := json.Unmarshal(strictReceiptSeed(t), &historical); err != nil {
				t.Fatal(err)
			}
			r = historical
		}
		raw, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		dir := t.TempDir()
		input := filepath.Join(dir, "input.json")
		if err := os.WriteFile(input, raw, 0600); err != nil {
			t.Fatal(err)
		}
		for _, args := range [][]string{
			{"report", input},
			{"publish", "--markdown", filepath.Join(dir, "out.txt"), "--json", filepath.Join(dir, "out.json"), input},
		} {
			var out bytes.Buffer
			if err := command(context.Background(), args, &out); err != nil || out.Len() == 0 {
				t.Fatalf("legacy=%v %s: %v, output=%d", legacy, args[0], err, out.Len())
			}
		}
	}
}
