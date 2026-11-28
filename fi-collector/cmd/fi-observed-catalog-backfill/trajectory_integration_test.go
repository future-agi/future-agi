package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// trajectorySchemaSQL runs one statement in database on the isolated local
// ClickHouse.
func trajectorySchemaSQL(t *testing.T, origin, database, sql string) string {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, origin+"?"+url.Values{"database": {database}}.Encode(), strings.NewReader(sql))
	if err != nil {
		t.Fatal(err)
	}
	req.SetBasicAuth("test", "test")
	response, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil || response.StatusCode != 200 || strings.Contains(string(body), "DB::Exception") {
		t.Fatalf("local fixture SQL failed: %s (%v)", body, err)
	}
	return string(body)
}

// trajectorySourceDatabase is an owned database with the canonical spans
// table (002, without its deployment TTL) and, when withTrajectory, 040's
// trajectory_items table and spans pointer columns from the rendered v2
// schema (FI_LIVE_V2_SCHEMA).
func trajectorySourceDatabase(t *testing.T, withTrajectory bool) (string, string) {
	t.Helper()
	origin, database := localClickHouse(t)
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "futureagi", "tracer", "services", "clickhouse", "v2", "schema", "002_spans_v2.sql"))
	if err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, line := range strings.Split(string(raw), "\n") {
		line, _, _ = strings.Cut(line, "--")
		lines = append(lines, line)
	}
	spans, _, found := strings.Cut(strings.Join(lines, "\n"), "\nTTL ")
	if !found {
		t.Fatal("canonical spans statement boundary changed")
	}
	trajectorySchemaSQL(t, origin, database, spans+"\nSETTINGS deduplicate_merge_projection_mode = 'rebuild'")
	if !withTrajectory {
		return origin, database
	}
	path := os.Getenv("FI_LIVE_V2_SCHEMA")
	if path == "" {
		if os.Getenv("FI_LIVE_STAGE") != "" {
			t.Fatal("FI_LIVE_STAGE is set: FI_LIVE_V2_SCHEMA is required (not skipping)")
		}
		t.Skip("set FI_LIVE_V2_SCHEMA (render_v2_schema.py) for the trajectory backfill integration test")
	}
	rendered, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var schema struct {
		Files map[string][]string `json:"files"`
	}
	if err := json.Unmarshal(rendered, &schema); err != nil || len(schema.Files["040_trajectory_items.sql"]) == 0 {
		t.Fatalf("rendered v2 schema lacks 040: %v", err)
	}
	for _, stmt := range schema.Files["040_trajectory_items.sql"] {
		if stmt = strings.TrimSuffix(strings.TrimSpace(stmt), ";"); stmt != "" {
			trajectorySchemaSQL(t, origin, database, stmt)
		}
	}
	return origin, database
}

func trajectoryWriter(t *testing.T, origin, database string) *chwriter.Writer {
	t.Helper()
	w, err := chwriter.New(chwriter.Config{URL: origin, Database: database, Table: "spans", Username: "test", Password: "test",
		MaxRetries: 1, InitialBackoff: 10 * time.Millisecond, RequestTimeout: 10 * time.Second,
		DeadLetterFile: filepath.Join(t.TempDir(), "dlq.jsonl")})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { w.Close() })
	return w
}

// TestClickHouseTrajectoryBackfillCatalogEqualsDedupOffTwin: the same span is
// stored once by the dedup converter (items_v1 row plus trajectory_items, in
// a database with 040) and once by the dedup-off converter (a database with
// dev's spans only). Backfilling both through the real source reader gives
// the same catalog batch: the promoted input.value/output.value keys are
// catalogued and the sentinels are not.
func TestClickHouseTrajectoryBackfillCatalogEqualsDedupOffTwin(t *testing.T) {
	origin, dedupDB := trajectorySourceDatabase(t, true)
	_, baseDB := trajectorySourceDatabase(t, false)
	td := trajectorySpan(t)
	devTraces := ptrace.NewTraces()
	td.CopyTo(devTraces)
	res, err := chexp.ConvertWithOptions(context.Background(), td, nil, chexp.ConvertOptions{
		Trajectory: trajectory.Config{Mode: trajectory.ModeOn}.Normalize(), Gate: trajectory.FixedGate(true),
	})
	if err != nil || res.Trajectory == nil || res.Rows[0]["payload_encoding"] != trajectory.EncodingItemsV1 {
		t.Fatalf("dedup conversion: %v", err)
	}
	dev, _, err := chexp.ConvertWithIdentities(context.Background(), devTraces, nil)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	wDedup := trajectoryWriter(t, origin, dedupDB)
	if err := wDedup.InsertTable(ctx, trajectory.ItemsTable, res.Trajectory.ToRows(time.Now().UTC())); err != nil {
		t.Fatal(err)
	}
	if err := wDedup.Insert(ctx, res.Rows); err != nil {
		t.Fatal(err)
	}
	if err := trajectoryWriter(t, origin, baseDB).Insert(ctx, dev); err != nil {
		t.Fatal(err)
	}

	scope := exampleScope()
	backfill := func(database string) (observedcatalog.Batch, *sourceReader) {
		reader, err := newSourceReader(origin, database, "test", "test")
		if err != nil {
			t.Fatal(err)
		}
		keys, err := reader.identityPage(ctx, scope.ProjectID, trajectoryHour, physicalKey{}, 10)
		if err != nil || len(keys) != 1 {
			t.Fatalf("identity page %v: %v", keys, err)
		}
		rows, err := reader.payloadPage(ctx, scope.ProjectID, trajectoryHour, keys)
		if err != nil {
			t.Fatal(err)
		}
		batch, _, err := buildPage(rows, scope, trajectoryHour, trajectoryHour.Add(time.Hour), observedcatalog.DefaultLimits())
		if err != nil {
			t.Fatal(err)
		}
		return batch, reader
	}
	got, reader := backfill(dedupDB)
	want, _ := backfill(baseDB)
	if !reader.trajectory.pointers || reader.contentUnavailable != 0 {
		t.Fatalf("dedup source: pointers=%v unavailable=%d", reader.trajectory.pointers, reader.contentUnavailable)
	}
	if !reflect.DeepEqual(got, want) {
		g, _ := json.Marshal(got)
		w, _ := json.Marshal(want)
		t.Fatalf("backfilled catalog differs from the dedup-off twin:\n got %s\nwant %s", g, w)
	}
	for _, k := range got.Keys {
		if strings.HasPrefix(k.AttributeKey, "__fi_promoted_") {
			t.Fatalf("sentinel %s catalogued", k.AttributeKey)
		}
	}
	if len(got.Keys) == 0 {
		t.Fatal("nothing was catalogued, so the comparison is vacuous")
	}
}
