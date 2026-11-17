package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/ingestbench"
)

// Exercise main's real exit/output path with synthetic consistency controls,
// never measured throughput. A clean label cannot override recorded failures.
func TestCLIPublicationChecksEvidenceBeyondCompletionLabel(t *testing.T) {
	latency := ingestbench.Percentiles{Count: 1, P50: 10, P95: 10, P99: 10, P999: 10, Max: 10}
	applicationError := func(r *ingestbench.Result) {
		p := &r.Reads[0]
		p.Status, p.Errors, p.FailureLatency = "errors", 1, latency
		p.HTTPStatuses = map[int]int64{500: 1}
	}
	fault := func(r *ingestbench.Result) {
		r.Config.DeclaredFaults = []ingestbench.DeclaredFault{{Name: "synthetic-test-fault", Duration: 100 * time.Millisecond}}
	}
	for _, tc := range []struct {
		name, reason string
		mutate       func(*ingestbench.Result)
	}{
		{"positive-control", "", func(*ingestbench.Result) {}},
		{"application-error-clean-label", "application read", applicationError},
		{"application-error-failed-label", "application read", func(r *ingestbench.Result) {
			applicationError(r)
			r.Status = "measured-with-errors"
		}},
		{"direct-error-clean-label", "direct ClickHouse read", func(r *ingestbench.Result) {
			p := &r.DirectReads[0]
			p.Status, p.Errors, p.FailureLatency = "errors", 1, latency
		}},
		{"declared-fault-without-recovery", "injected or declared faults", fault},
		{"declared-fault-with-recovery", "injected or declared faults", func(r *ingestbench.Result) {
			fault(r)
			r.Recovery = &ingestbench.RecoveryResult{}
		}},
		{"censored-not-failed-control", "", func(r *ingestbench.Result) {
			r.Reads[0].Censored, r.Reads[0].CensoredElapsed = 1, latency
			r.DirectReads[0].Censored, r.DirectReads[0].CensoredElapsed = 1, latency
		}},
		{"no-probe-control", "", func(r *ingestbench.Result) {
			r.Reads, r.DirectReads = nil, nil
		}},
		{"failed-no-probe-diagnostic", "not a clean measured-profile-complete", func(r *ingestbench.Result) {
			r.Status = "measured-with-errors"
			r.Reads, r.DirectReads = nil, nil
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var r ingestbench.Result
			if err := json.Unmarshal(strictReceiptSeed(t), &r); err != nil {
				t.Fatal(err)
			}
			tc.mutate(&r)
			raw, err := json.Marshal(r)
			if err != nil {
				t.Fatal(err)
			}
			dir := t.TempDir()
			input, md, js := filepath.Join(dir, "input.json"), filepath.Join(dir, "publication.txt"), filepath.Join(dir, "publication.json")
			if err := os.WriteFile(input, raw, 0600); err != nil {
				t.Fatal(err)
			}
			if out, err := repairProcess("report", input).CombinedOutput(); err != nil || len(out) == 0 {
				t.Fatalf("report must retain diagnostic: %v %s", err, out)
			}
			if out, err := repairProcess("publish", "--min-sustained", "500ms", "--markdown", md, "--json", js, input).CombinedOutput(); err != nil {
				t.Fatalf("publish must retain diagnostic: %v %s", err, out)
			}
			body, err := os.ReadFile(js)
			if err != nil {
				t.Fatal(err)
			}
			var p ingestbench.Publication
			if err := json.Unmarshal(body, &p); err != nil {
				t.Fatal(err)
			}
			text, err := os.ReadFile(md)
			if err != nil {
				t.Fatal(err)
			}
			if tc.reason == "" {
				if p.Headline.Status != "published" || len(p.Headline.WithheldReasons) != 0 || !strings.Contains(string(text), "**Sustained the offered") {
					t.Fatalf("positive control rejected: %+v", p.Headline)
				}
			} else if p.Headline.Status != "withheld" || p.Headline.SustainedOfferedSpansPerSecond != nil || p.Headline.AchievedAcknowledgedSpansPerSecond != nil || !strings.Contains(strings.Join(p.Headline.WithheldReasons, "\n"), tc.reason) || !strings.Contains(string(text), tc.reason) || strings.Contains(string(text), "**Sustained the offered") {
				t.Fatalf("contradictory evidence earned a headline or lost its reason: %+v", p.Headline)
			}
			if len(r.Config.DeclaredFaults) > 0 && r.Recovery == nil && (!strings.Contains(string(text), "faults declared but no recovery measurements recorded") || strings.Contains(string(text), "no fault in this run")) {
				t.Fatal("missing recovery block erased the declared fault")
			}
		})
	}
}
