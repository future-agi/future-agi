package server

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/traceavailable"
)

func partialChunkRow(name, project string) map[string]any {
	return map[string]any{"id": "same-span-id", "name": name, "trace_id": "44444444-4444-4444-8444-444444444444", "parent_span_id": "", "org_id": calOrg, "project_id": project, "start_time": "2026-10-27 01:00:00.000000", "end_time": "2026-10-27 01:00:01.000000"}
}

// Equal IDs are not an acknowledgement key: one copy can fail while a
// sibling with those same IDs succeeds. Mixed hidden routing also changes
// canonical positions relative to the scoped sidecar slices.
func TestCorrectnessPartialChunksUsePositionsAcrossRouting(t *testing.T) {
	const hiddenProject = "11111111-2222-4333-8444-555555555556"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if bytes.Contains(body, []byte(`"name":"bad"`)) {
			w.WriteHeader(400)
			return
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()
	writer := newSpanTestWriter(t, srv.URL, t.TempDir()+"/dl.jsonl")
	defer writer.Close()
	catalog := &propertyCatalogWriterStub{}
	var roots []traceavailable.Root
	s := New(Config{}, writer, nil, nil, nil, WithPropertyCatalogWriter(catalog), WithTraceNotifier(handoffRootNotifier(func(r []traceavailable.Root) error { roots = append(roots, r...); return nil })))
	writer.SetMaxInsertBytes(1)
	writer.SetMaxConcurrentInserts(3)
	rows := []map[string]any{partialChunkRow("bad", calProject), partialChunkRow("hidden", hiddenProject), partialChunkRow("good", calProject)}
	s.enqueueRouted(rows, nil, auth.RouteOutcome{}, calOrg, calWS, map[string]struct{}{calProject: {}, hiddenProject: {}}, map[string]struct{}{hiddenProject: {}})
	s.drainNow(context.Background())
	s.drainNow(context.Background())
	if len(catalog.rows) != 1 || catalog.rows[0].Row["name"] != "good" {
		t.Fatalf("catalog did not preserve exact confirmed visible position: %d rows", len(catalog.rows))
	}
	if len(roots) != 1 || roots[0].ProjectID != calProject {
		t.Fatalf("root association/grouping failed: %+v", roots)
	}
	events, err := traceavailable.BuildEvents(roots, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 || len(events[0].Traces) != 1 || events[0].OrganizationID != calOrg || events[0].WorkspaceID == nil || *events[0].WorkspaceID != calWS {
		t.Fatal("confirmed root group lost authenticated scope")
	}
	if stats := writer.Snapshot(); stats.RowsInserted != 2 || stats.RowsDeadLettered != 1 {
		t.Fatalf("canonical outcomes changed: %+v", stats)
	}
}

func TestCorrectnessShutdownWaitsForConfirmedSidecars(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	releaseOnce := func() { once.Do(func() { close(release) }) }
	defer releaseOnce()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if bytes.Contains(body, []byte(`"name":"bad"`)) {
			w.WriteHeader(400)
			return
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()
	writer := newSpanTestWriter(t, srv.URL, t.TempDir()+"/dl.jsonl")
	defer writer.Close()
	s := New(Config{BatchMaxRows: 1, BatchMaxAge: time.Hour}, writer, nil, nil, nil, WithPropertyCatalogWriter(correctnessCatalog(func(rows []observedcatalog.ScopedSpan) error {
		if len(rows) != 1 || rows[0].Row["name"] != "good" {
			t.Error("shutdown lost/added confirmed rows")
		}
		close(entered)
		<-release
		return nil
	})))
	writer.SetMaxInsertBytes(1)
	writer.SetMaxConcurrentInserts(2)
	l, err := s.work.acquire(context.Background(), 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	s.adoptRows(l)
	s.wg.Add(1)
	go s.flushLoop()
	s.enqueueScoped([]map[string]any{partialChunkRow("bad", calProject), partialChunkRow("good", calProject)}, nil, calOrg, calWS, map[string]struct{}{calProject: {}})
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("handoff did not start")
	}
	done := make(chan struct{})
	go func() { defer close(done); s.shutdown() }()
	// Closing stopCh is the real shutdown barrier; shutdown must still wait
	// for its flusher and synchronous sidecar rather than releasing the lease.
	<-s.stopCh
	if s.work.inUse.Load() != 1<<20 {
		t.Fatal("shutdown returned row budget before handoff completed")
	}
	select {
	case <-done:
		t.Fatal("shutdown returned with an active sidecar")
	default:
	}
	releaseOnce()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("shutdown did not finish")
	}
	if s.work.inUse.Load() != 0 || s.draining.Load() != 0 {
		t.Fatal("shutdown left a lease or drain")
	}
	if stats := writer.Snapshot(); stats.RowsInserted != 1 || stats.RowsDeadLettered != 1 {
		t.Fatalf("shutdown repeated canonical writes: %+v", stats)
	}
}
