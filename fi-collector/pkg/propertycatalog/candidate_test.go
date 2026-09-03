package propertycatalog

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
)

func candidateRuntimeConfig(t *testing.T) RuntimeConfig {
	t.Helper()
	cfg := validRuntimeConfig(t)
	cfg.Mode = RuntimeKafka
	cfg.Kafka.ClientID = ""
	return cfg.WithDefaults()
}

func mustCandidates(t *testing.T, cfg RuntimeConfig, rows []ScopedSpan) []WireCandidate {
	t.Helper()
	candidates, err := BuildCandidates(cfg, rows)
	if err != nil {
		t.Fatal(err)
	}
	return candidates
}

func TestCandidateBuildIsDeterministicBoundedAndWorkspaceKeyed(t *testing.T) {
	cfg := candidateRuntimeConfig(t)
	rows := []ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(
			testProject, testLastSeen, map[string]string{"plan": "enterprise"}, map[string]float64{"score": 2},
		)),
		scopedHotRow(testWorkspace, testProject, hotRow(
			testProject, testSeen, map[string]string{"plan": "pro"}, map[string]float64{"score": 1},
		)),
	}
	forward := mustCandidates(t, cfg, rows)
	reverse := mustCandidates(t, cfg, []ScopedSpan{rows[1], rows[0]})
	if len(forward) != 1 || len(reverse) != 1 {
		t.Fatalf("candidate counts forward=%d reverse=%d", len(forward), len(reverse))
	}
	left, _ := forward[0].MarshalBinary()
	right, _ := reverse[0].MarshalBinary()
	if !bytes.Equal(left, right) || forward[0].Snapshot().CandidateID != reverse[0].Snapshot().CandidateID {
		t.Fatal("candidate identity depends on collector row order")
	}
	if len(left) > cfg.MaxCandidateBytes || len(left) > MaxCandidateRecordBytes {
		t.Fatalf("candidate bytes=%d", len(left))
	}
	key, err := CandidateKafkaKey(forward[0])
	if err != nil || string(key) != testWorkspace {
		t.Fatalf("candidate key=%q err=%v", key, err)
	}
	snapshot := forward[0].Snapshot()
	if snapshot.SourceRows != 2 || snapshot.CatalogEpoch != cfg.CatalogEpoch ||
		snapshot.ProjectionVersion != cfg.ProjectionVersion || len(snapshot.Values) != 4 {
		t.Fatalf("candidate snapshot=%+v", snapshot)
	}
}

func TestCandidateBuildSplitsBySpanAndEncodedByteCaps(t *testing.T) {
	cfg := candidateRuntimeConfig(t)
	cfg.MaxCandidateSpans = 1
	rows := []ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testSeen, map[string]string{"a": "one"}, nil)),
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testLastSeen, map[string]string{"b": "two"}, nil)),
	}
	candidates := mustCandidates(t, cfg, rows)
	if len(candidates) != 2 || candidates[0].Snapshot().SourceRows != 1 || candidates[1].Snapshot().SourceRows != 1 {
		t.Fatalf("split candidates=%+v", candidates)
	}

	tooSmall := candidateRuntimeConfig(t)
	tooSmall.MaxCandidateBytes = 256
	large := scopedHotRow(testWorkspace, testProject, hotRow(
		testProject, testSeen, map[string]string{"large": strings.Repeat("x", 2048)}, nil,
	))
	if _, err := BuildCandidates(tooSmall, []ScopedSpan{large}); err == nil || !strings.Contains(err.Error(), "candidate uses") {
		t.Fatalf("oversize candidate error=%v", err)
	}
}

func TestCandidateParseRejectsTamperUnknownFieldsAndNonCanonicalBytes(t *testing.T) {
	candidate := mustCandidates(t, candidateRuntimeConfig(t), []ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testSeen, map[string]string{"a": "one"}, nil)),
	})[0]
	raw, _ := candidate.MarshalBinary()

	for name, mutate := range map[string]func([]byte) []byte{
		"digest tamper": func(value []byte) []byte {
			marker := []byte(`"candidate_id":"`)
			index := bytes.Index(value, marker)
			if index < 0 {
				return value
			}
			digest := index + len(marker)
			if value[digest] == '0' {
				value[digest] = '1'
			} else {
				value[digest] = '0'
			}
			return value
		},
		"unknown field": func(value []byte) []byte {
			return append(append([]byte(nil), value[:len(value)-1]...), []byte(`,"unknown":true}`)...)
		},
		"non canonical whitespace": func(value []byte) []byte {
			return append([]byte(" "), value...)
		},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := ParseWireCandidate(mutate(bytes.Clone(raw))); err == nil {
				t.Fatal("invalid candidate was accepted")
			}
		})
	}
}

type candidateRecordingWriter struct {
	records []catalogkafka.Record
	errAt   int
}

func (w *candidateRecordingWriter) WriteRecord(_ context.Context, record catalogkafka.Record) error {
	if w.errAt > 0 && len(w.records)+1 == w.errAt {
		return errors.New("write failed")
	}
	w.records = append(w.records, record)
	return nil
}

func (*candidateRecordingWriter) Close() {}

func TestCandidateWriterValidatesWholeBatchBeforeFirstBrokerWrite(t *testing.T) {
	cfg := candidateRuntimeConfig(t)
	transport := &candidateRecordingWriter{}
	producer, err := NewCandidateProducer(cfg.Kafka.Topic, transport)
	if err != nil {
		t.Fatal(err)
	}
	writer, err := NewCandidateWriter(cfg, producer)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		if err := writer.Shutdown(ctx); err != nil {
			t.Errorf("candidate writer shutdown: %v", err)
		}
	}()
	invalid := ScopedSpan{OrganizationID: "not-a-uuid", WorkspaceID: testWorkspace, Row: hotRow(
		testProject, testSeen, map[string]string{"bad": "scope"}, nil,
	)}
	if err := writer.EnqueueCanonicalSpans([]ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testSeen, map[string]string{"ok": "yes"}, nil)),
		invalid,
	}); err == nil {
		t.Fatal("mixed invalid candidate batch was accepted")
	}
	if len(transport.records) != 0 {
		t.Fatalf("published %d records before whole-batch validation", len(transport.records))
	}
}

type blockedCandidateTransport struct {
	started chan struct{}
	once    sync.Once
	calls   atomic.Int32
}

func (w *blockedCandidateTransport) WriteRecord(ctx context.Context, _ catalogkafka.Record) error {
	w.calls.Add(1)
	w.once.Do(func() { close(w.started) })
	<-ctx.Done()
	return ctx.Err()
}

func (*blockedCandidateTransport) Close() {}

func TestAsyncCandidateHandoffCannotStallCanonicalDrainOnBlockedBroker(t *testing.T) {
	cfg := candidateRuntimeConfig(t)
	cfg.MaxCandidateSpans = 1
	cfg.QueueDepth = 2
	cfg.Kafka.DeliveryTimeout = 5 * time.Second
	transport := &blockedCandidateTransport{started: make(chan struct{})}
	producer, err := NewCandidateProducer(cfg.Kafka.Topic, transport)
	if err != nil {
		t.Fatal(err)
	}
	writer, err := NewCandidateWriter(cfg, producer)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Start(context.Background()); err != nil {
		t.Fatal(err)
	}

	rows := []ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testSeen, map[string]string{"a": "one"}, nil)),
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testLastSeen, map[string]string{"b": "two"}, nil)),
	}
	handoff := make(chan error, 1)
	go func() { handoff <- writer.EnqueueCanonicalSpans(rows) }()
	select {
	case err := <-handoff:
		if err != nil {
			t.Fatalf("non-blocking handoff failed: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("canonical drain handoff waited on the blocked candidate broker")
	}
	select {
	case <-transport.started:
	case <-time.After(time.Second):
		t.Fatal("candidate worker did not reach the blocked broker")
	}
	if transport.calls.Load() != 1 {
		t.Fatalf("blocked broker calls=%d", transport.calls.Load())
	}

	// Shutdown has one independent batch-level bound and cancels the blocked
	// transport before the producer can be closed underneath its worker.
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	if err := writer.Shutdown(shutdownCtx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("shutdown error=%v, want deadline", err)
	}
}

type deadlineConsumingCandidateTransport struct {
	calls atomic.Int32
}

func (w *deadlineConsumingCandidateTransport) WriteRecord(ctx context.Context, _ catalogkafka.Record) error {
	call := w.calls.Add(1)
	if call == 1 {
		<-ctx.Done()
		// Force the writer to attempt the next candidate. A per-candidate
		// timeout would now wait again; the shared batch context is expired.
		return nil
	}
	return ctx.Err()
}

func (*deadlineConsumingCandidateTransport) Close() {}

func TestCandidatePublishDeadlineIsSharedByTheWholeBatch(t *testing.T) {
	cfg := candidateRuntimeConfig(t)
	cfg.MaxCandidateSpans = 1
	cfg.Kafka.DeliveryTimeout = 80 * time.Millisecond
	transport := &deadlineConsumingCandidateTransport{}
	producer, err := NewCandidateProducer(cfg.Kafka.Topic, transport)
	if err != nil {
		t.Fatal(err)
	}
	writer, err := NewCandidateWriter(cfg, producer)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	rows := []ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testSeen, map[string]string{"a": "one"}, nil)),
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testLastSeen, map[string]string{"b": "two"}, nil)),
	}
	started := time.Now()
	if err := writer.EnqueueCanonicalSpans(rows); err != nil {
		t.Fatal(err)
	}
	select {
	case <-writer.Gaps():
	case <-time.After(time.Second):
		t.Fatal("shared batch deadline did not terminate publication")
	}
	if elapsed := time.Since(started); elapsed >= 400*time.Millisecond {
		t.Fatalf("candidate batch deadline multiplied across records: %s", elapsed)
	}
	if transport.calls.Load() != 2 {
		t.Fatalf("broker calls=%d, want first timeout plus immediate expired-context call", transport.calls.Load())
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := writer.Shutdown(shutdownCtx); err != nil {
		t.Fatal(err)
	}
}
