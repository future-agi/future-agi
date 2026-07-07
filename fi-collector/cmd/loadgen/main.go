// loadgen fabricates deterministic SDK-shaped OTLP spans and ingests them
// THROUGH the production converter + chwriter in-process (no running collector),
// then writes a manifest describing the seeded dataset. It exists to seed a CH
// `spans` table for the eval-task data-read benchmarks: a fixed seed reproduces
// the exact same trace/span ids so the read benchmarks are stable across runs.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/curatedwriter"
)

// Manifest is the dataset descriptor Tasks 2/3 read to target the seeded rows
// without re-deriving ids. Field names are the wire contract.
type Manifest struct {
	ProjectID             string            `json:"project_id"`
	TraceIDs              []string          `json:"trace_ids"`
	RootSpanIDByTrace     map[string]string `json:"root_span_id_by_trace"`
	SpanCount             int               `json:"span_count"`
	SessionIDs            []string          `json:"session_ids"`
	ObservationTypeCounts map[string]int    `json:"observation_type_counts"`
}

func main() {
	var (
		chURL        = flag.String("ch-url", "http://localhost:8123", "ClickHouse HTTP endpoint")
		projectID    = flag.String("project-id", "", "fi.project_id stamped on every span")
		orgID        = flag.String("org-id", "", "fi.org_id stamped on every span")
		traces       = flag.Int("traces", 1000, "number of traces to fabricate")
		spansPerTr   = flag.Int("spans-per-trace", 8, "spans per trace (1 root + N-1 children)")
		sessions     = flag.Int("sessions", 4, "size of the session-id pool")
		shape        = flag.String("shape", "llm", "span shape")
		seed         = flag.Int64("seed", 42, "PRNG seed (fixed seed → reproducible dataset)")
		timeRange    = flag.Duration("time-range", 720*time.Hour, "span start-time window, back from --end")
		end          = flag.String("end", "2026-01-01T00:00:00Z", "fixed RFC3339 window end (reproducibility anchor)")
		manifestPath = flag.String("manifest", "manifest.json", "manifest output path")
		batchSize    = flag.Int("batch-size", 10000, "spans per ingest round")
		trickle      = flag.Int("trickle", 0, "if >0, pace emission at N spans/sec (batch = 1 trace)")
		otlpEndpoint = flag.String("otlp-endpoint", "", "if set, send via OTLP/gRPC to host:4317 (wire mode; excludes in-process --ch-url ingest)")
	)
	flag.Parse()

	switch *shape {
	case shapeLLM, shapeVoice, shapeAgentDeep, shapeFatAttrs, shapeMixed:
	default:
		fatalf("unsupported shape %q; want llm|voice|agent-deep|fat-attrs|mixed", *shape)
	}

	endTime, err := time.Parse(time.RFC3339, *end)
	if err != nil {
		fatalf("parse --end: %v", err)
	}

	// Wire mode (--otlp-endpoint) and in-process CH ingest are mutually
	// exclusive: exactly one of sender / (w, curated) is set.
	var (
		w       *chwriter.Writer
		curated *curatedwriter.Writer
		sender  *otlpSender
	)
	if *otlpEndpoint != "" {
		sender, err = newOTLPSender(*otlpEndpoint)
		if err != nil {
			fatalf("otlp dial: %v", err)
		}
		defer sender.Close()
	} else {
		w, err = chwriter.New(chwriter.Config{
			URL:            *chURL,
			Database:       "default",
			Table:          "spans",
			DeadLetterFile: filepath.Join(os.TempDir(), "loadgen_dead_letter.jsonl"),
		})
		if err != nil {
			fatalf("chwriter: %v", err)
		}
		defer w.Close()
		curated = curatedwriter.New(w)
	}

	r := rand.New(rand.NewSource(*seed))
	ctx := context.Background()

	manifest := Manifest{
		ProjectID:             *projectID,
		RootSpanIDByTrace:     map[string]string{},
		ObservationTypeCounts: map[string]int{},
	}
	sessionSeen := map[string]struct{}{}
	traceSeen := map[string]struct{}{}

	// Batch in traces. --trickle emits one trace at a time, paced by a ticker
	// sized to N spans/sec (wall-clock; fabrication stays clock-free).
	tracesPerBatch := max(*batchSize/max(*spansPerTr, 1), 1)
	var ticker *time.Ticker
	if *trickle > 0 {
		tracesPerBatch = 1
		ticker = time.NewTicker(time.Second / time.Duration(*trickle))
		defer ticker.Stop()
	}

	remaining := *traces
	for remaining > 0 {
		n := min(tracesPerBatch, remaining)
		remaining -= n
		batch := fabricateBatch(FabricateConfig{
			ProjectID: *projectID, OrgID: *orgID, Shape: *shape,
			Traces: n, SpansPerTrace: *spansPerTr, Sessions: *sessions,
			Start: endTime.Add(-*timeRange), End: endTime,
		}, r)

		// Convert in both modes so the manifest is byte-identical: wire mode
		// derives ids/counts locally while the collector converts server-side.
		rows, ids, err := chexp.ConvertWithIdentities(batch)
		if err != nil {
			fatalf("convert: %v", err)
		}
		if sender != nil {
			if err := sender.Send(ctx, batch); err != nil {
				fatalf("otlp send: %v", err)
			}
		} else {
			if err := w.Insert(ctx, rows); err != nil {
				fatalf("insert: %v", err)
			}
			if err := curated.Write(ctx, ids, time.Now().UTC()); err != nil {
				fmt.Fprintf(os.Stderr, "loadgen: curated write: %v\n", err)
			}
		}

		for _, row := range rows {
			manifest.SpanCount++
			ot, ok := row["observation_type"].(string)
			if !ok {
				fatalf("converter row missing string observation_type (id=%v)", row["id"])
			}
			manifest.ObservationTypeCounts[ot]++
			traceID, ok := row["trace_id"].(string)
			if !ok {
				fatalf("converter row missing string trace_id (id=%v)", row["id"])
			}
			if _, seen := traceSeen[traceID]; !seen {
				traceSeen[traceID] = struct{}{}
				manifest.TraceIDs = append(manifest.TraceIDs, traceID)
			}
			if parent, _ := row["parent_span_id"].(string); parent == "" {
				id, ok := row["id"].(string)
				if !ok {
					fatalf("converter row missing string id (id=%v)", row["id"])
				}
				manifest.RootSpanIDByTrace[traceID] = id
			}
		}
		for _, s := range ids.Sessions() {
			if _, seen := sessionSeen[s.ExternalSessionID]; !seen {
				sessionSeen[s.ExternalSessionID] = struct{}{}
				manifest.SessionIDs = append(manifest.SessionIDs, s.ExternalSessionID)
			}
		}

		if ticker != nil {
			for i := 0; i < len(rows); i++ {
				<-ticker.C
			}
		}
	}

	if err := writeManifest(*manifestPath, manifest); err != nil {
		fatalf("manifest: %v", err)
	}
	fmt.Printf("loadgen: ingested %d spans across %d traces → %s\n",
		manifest.SpanCount, len(manifest.TraceIDs), *manifestPath)
}

// writeManifest serialises the manifest to a temp file and renames it into
// place so a reader never observes a partially-written manifest.
func writeManifest(path string, m Manifest) error {
	body, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, body, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "loadgen: "+format+"\n", args...)
	os.Exit(1)
}
