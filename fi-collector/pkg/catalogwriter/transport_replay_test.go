package catalogwriter

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

type deliveryHandlerFunc func(context.Context, PendingDelivery) error

func (f deliveryHandlerFunc) DeliverCatalogJob(ctx context.Context, delivery PendingDelivery) error {
	return f(ctx, delivery)
}

func TestReplayToFailureRetainsAndAcknowledgementRemoves(t *testing.T) {
	dir := t.TempDir()
	cfg := enabledConfig(dir)
	w, err := NewTransportWriter(cfg)
	if err != nil {
		t.Fatal(err)
	}
	job, _ := w.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"model": "gpt"}),
	})
	if err := w.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	originalBytes := w.spoolBytes
	wantErr := errors.New("transport unavailable")
	failed := deliveryHandlerFunc(func(_ context.Context, delivery PendingDelivery) error {
		if delivery.ID == "" || delivery.CreatedAt.IsZero() || delivery.WireJob.Metadata.InputSpans != 1 {
			t.Fatalf("incomplete delivery: %+v", delivery)
		}
		return wantErr
	})
	result, err := w.ReplayTo(context.Background(), failed)
	if !errors.Is(err, wantErr) || result != (ReplayResult{Attempted: 1}) {
		t.Fatalf("failed ReplayTo=%+v err=%v", result, err)
	}
	if w.spoolFiles != 1 || w.spoolBytes != originalBytes {
		t.Fatalf("failure changed accounting: files=%d bytes=%d", w.spoolFiles, w.spoolBytes)
	}
	pending, err := w.Pending()
	if err != nil || len(pending) != 1 {
		t.Fatalf("failed delivery was not retained: pending=%+v err=%v", pending, err)
	}

	acknowledged := 0
	result, err = w.ReplayTo(context.Background(), deliveryHandlerFunc(
		func(context.Context, PendingDelivery) error {
			acknowledged++
			return nil
		},
	))
	if err != nil || result != (ReplayResult{Attempted: 1, Delivered: 1}) || acknowledged != 1 {
		t.Fatalf("acknowledged ReplayTo=%+v calls=%d err=%v", result, acknowledged, err)
	}
	if w.spoolFiles != 0 || w.spoolBytes != 0 {
		t.Fatalf("ack did not release capacity: files=%d bytes=%d", w.spoolFiles, w.spoolBytes)
	}
	pending, err = w.Pending()
	if err != nil || len(pending) != 0 {
		t.Fatalf("acknowledged delivery remains: pending=%+v err=%v", pending, err)
	}
}

func TestTransportWriterDirectReplayAndNilHandlerFailClosed(t *testing.T) {
	w, err := NewTransportWriter(enabledConfig(t.TempDir()))
	if err != nil {
		t.Fatal(err)
	}
	job, _ := w.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:00.000001", "map"),
	})
	if err := w.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	if result, err := w.Replay(context.Background()); err == nil ||
		!strings.Contains(err.Error(), "use ReplayTo") || result != (ReplayResult{}) {
		t.Fatalf("direct Replay on transport writer=%+v err=%v", result, err)
	}
	if result, err := w.ReplayTo(context.Background(), nil); err == nil ||
		!strings.Contains(err.Error(), "requires a delivery handler") || result != (ReplayResult{}) {
		t.Fatalf("nil ReplayTo=%+v err=%v", result, err)
	}
	pending, err := w.Pending()
	if err != nil || len(pending) != 1 {
		t.Fatalf("misuse deleted pending work: pending=%+v err=%v", pending, err)
	}
}

func TestReplayToRestartMetadataOnlyAndDefensiveCopies(t *testing.T) {
	dir := t.TempDir()
	cfg := enabledConfig(dir)
	w, err := NewTransportWriter(cfg)
	if err != nil {
		t.Fatal(err)
	}
	metadataOnly, _ := w.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{}),
	})
	if !metadataOnly.Empty() || metadataOnly.Metadata().InputSpans != 1 {
		t.Fatalf("expected metadata-only job: rows=%d metadata=%+v", metadataOnly.RowCount(), metadataOnly.Metadata())
	}
	if err := w.Submit(context.Background(), metadataOnly); err != nil {
		t.Fatal(err)
	}

	restarted, err := NewTransportWriter(cfg)
	if err != nil {
		t.Fatal(err)
	}
	var first PendingDelivery
	mutationErr := errors.New("retry after mutation")
	result, err := restarted.ReplayTo(context.Background(), deliveryHandlerFunc(
		func(_ context.Context, delivery PendingDelivery) error {
			first = delivery
			if len(delivery.WireJob.KeyRows) != 0 || len(delivery.WireJob.ValueRows) != 0 ||
				delivery.WireJob.EncodedBytes != 0 || delivery.WireJob.Metadata.InputSpans != 1 ||
				len(delivery.WireJob.Metadata.Projects) != 1 {
				t.Fatalf("metadata-only wire job changed: %+v", delivery.WireJob)
			}
			delivery.WireJob.Metadata.InputSpans = 999
			delivery.WireJob.Metadata.Projects[0].ProjectID = "mutated"
			delivery.WireJob.Metadata.GapReasons = append(delivery.WireJob.Metadata.GapReasons, "mutated")
			return mutationErr
		},
	))
	if !errors.Is(err, mutationErr) || result != (ReplayResult{Attempted: 1}) {
		t.Fatalf("mutating attempt=%+v err=%v", result, err)
	}

	var retry PendingDelivery
	result, err = restarted.ReplayTo(context.Background(), deliveryHandlerFunc(
		func(_ context.Context, delivery PendingDelivery) error {
			retry = delivery
			return nil
		},
	))
	if err != nil || result != (ReplayResult{Attempted: 1, Delivered: 1}) {
		t.Fatalf("retry=%+v err=%v", result, err)
	}
	if retry.ID != first.ID || !retry.CreatedAt.Equal(first.CreatedAt) {
		t.Fatalf("retry identity changed: first=%+v retry=%+v", first, retry)
	}
	if retry.WireJob.Metadata.InputSpans != 1 ||
		retry.WireJob.Metadata.Projects[0].ProjectID != testProjectID ||
		reflect.DeepEqual(retry.WireJob.Metadata.GapReasons, []string{"mutated"}) {
		t.Fatalf("handler mutation escaped defensive copy: %+v", retry.WireJob.Metadata)
	}
}

func TestReplayToSerializesHandlersWithoutBlockingSubmit(t *testing.T) {
	w, err := NewTransportWriter(enabledConfig(t.TempDir()))
	if err != nil {
		t.Fatal(err)
	}
	firstJob, _ := w.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:00.000001", "first.map"),
	})
	if err := w.Submit(context.Background(), firstJob); err != nil {
		t.Fatal(err)
	}

	started := make(chan struct{})
	release := make(chan struct{})
	var once sync.Once
	var mu sync.Mutex
	active, maxActive, calls := 0, 0, 0
	handler := deliveryHandlerFunc(func(ctx context.Context, _ PendingDelivery) error {
		mu.Lock()
		active++
		calls++
		if active > maxActive {
			maxActive = active
		}
		mu.Unlock()
		once.Do(func() { close(started) })
		select {
		case <-release:
		case <-ctx.Done():
			return ctx.Err()
		}
		mu.Lock()
		active--
		mu.Unlock()
		return nil
	})
	type outcome struct {
		result ReplayResult
		err    error
	}
	firstDone := make(chan outcome, 1)
	secondDone := make(chan outcome, 1)
	go func() {
		result, replayErr := w.ReplayTo(context.Background(), handler)
		firstDone <- outcome{result, replayErr}
	}()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("first ReplayTo did not reach handler")
	}
	go func() {
		result, replayErr := w.ReplayTo(context.Background(), handler)
		secondDone <- outcome{result, replayErr}
	}()
	select {
	case got := <-secondDone:
		t.Fatalf("second ReplayTo bypassed serialization: %+v", got)
	case <-time.After(100 * time.Millisecond):
	}

	secondJob, _ := w.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:01.000001", "second.map"),
	})
	submitDone := make(chan error, 1)
	go func() { submitDone <- w.Submit(context.Background(), secondJob) }()
	select {
	case err := <-submitDone:
		if err != nil {
			t.Fatalf("Submit during ReplayTo: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("ReplayTo handler blocked Submit")
	}
	close(release)
	firstOutcome := <-firstDone
	secondOutcome := <-secondDone
	if firstOutcome.err != nil || secondOutcome.err != nil ||
		firstOutcome.result != (ReplayResult{Attempted: 1, Delivered: 1}) ||
		secondOutcome.result != (ReplayResult{Attempted: 1, Delivered: 1}) {
		t.Fatalf("first=%+v second=%+v", firstOutcome, secondOutcome)
	}
	mu.Lock()
	defer mu.Unlock()
	if calls != 2 || maxActive != 1 {
		t.Fatalf("handler calls=%d max concurrent=%d", calls, maxActive)
	}
}
