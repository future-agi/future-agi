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

func TestStrictReceiptNullableRecoveryMeasurements(t *testing.T) {
	var r ingestbench.Result
	if err := json.Unmarshal(strictReceiptSeed(t), &r); err != nil {
		t.Fatal(err)
	}
	r.Status = "incomplete"
	r.Recovery = &ingestbench.RecoveryResult{Faults: []ingestbench.FaultResult{{}}}
	raw, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"observed", "null", "absent"} {
		t.Run(mode, func(t *testing.T) {
			var doc map[string]any
			dec := json.NewDecoder(bytes.NewReader(raw))
			dec.UseNumber()
			if err := dec.Decode(&doc); err != nil {
				t.Fatal(err)
			}
			f := doc["recovery"].(map[string]any)["faults"].([]any)[0].(map[string]any)
			for _, key := range []string{"first_acknowledgement_after_end_seconds", "backlog_baseline_rows", "backlog_peak_rows", "backlog_back_to_baseline_after_end_seconds", "earlier_sentinels_all_visible_after_end_seconds", "first_later_sentinel_visible_after_end_seconds", "recovery_seconds", "acknowledged_but_missing_spans_up_to_end"} {
				switch mode {
				case "observed":
					f[key] = 0
				case "null":
					f[key] = nil
				case "absent":
					delete(f, key)
				}
			}
			b, err := json.Marshal(doc)
			if err != nil {
				t.Fatal(err)
			}
			dir := t.TempDir()
			input := filepath.Join(dir, "input.json")
			if err := os.WriteFile(input, b, 0600); err != nil {
				t.Fatal(err)
			}
			var out bytes.Buffer
			if err := command(context.Background(), []string{"report", input}, &out); err != nil || out.Len() == 0 {
				t.Fatalf("nullable report %s: %v", mode, err)
			}
			js, md := filepath.Join(dir, "p.json"), filepath.Join(dir, "p.txt")
			if err := command(context.Background(), []string{"publish", "--min-sustained", "1ns", "--markdown", md, "--json", js, input}, &out); err != nil {
				t.Fatal(err)
			}
			p, err := os.ReadFile(js)
			if err != nil {
				t.Fatal(err)
			}
			var publication struct {
				Headline struct {
					Status string `json:"status"`
				} `json:"headline"`
			}
			if err := json.Unmarshal(p, &publication); err != nil || publication.Headline.Status != "withheld" {
				t.Fatalf("diagnostic headline %s: %s %v", mode, publication.Headline.Status, err)
			}
		})
	}
}
