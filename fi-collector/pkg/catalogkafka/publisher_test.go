package catalogkafka

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

type recordingEnvelopePublisher struct {
	mu      sync.Mutex
	records []WireEnvelope
	err     error
	before  func(WireEnvelope)
}

func (p *recordingEnvelopePublisher) Publish(ctx context.Context, envelope WireEnvelope) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if p.before != nil {
		p.before(envelope)
	}
	p.mu.Lock()
	p.records = append(p.records, envelope)
	err := p.err
	p.mu.Unlock()
	return err
}

func (p *recordingEnvelopePublisher) snapshots() []WireEnvelope {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]WireEnvelope(nil), p.records...)
}

func publisherDelivery(t *testing.T, spoolID string) catalogwriter.PendingDelivery {
	t.Helper()
	keyRow := testKeyRow(0)
	valueRow := testValueRow()
	encodedBytes := len(testRowBytes(t, keyRow)) + len(testRowBytes(t, valueRow))
	metadata := catalogwriter.JobMetadata{
		CatalogEpoch: 1, InputSpans: 1, AcceptedSpans: 1,
		KeyRows: 1, ValueRows: 1, EncodedBytes: encodedBytes,
		MinSpanStart: testTime, MaxSpanStart: testTime,
		Projects: []catalogwriter.ProjectJobMetadata{{
			ProjectID: testProject, InputSpans: 1, AcceptedSpans: 1,
			KeyRows: 1, ValueRows: 1, MinSpanStart: testTime, MaxSpanStart: testTime,
		}},
	}
	return catalogwriter.PendingDelivery{
		ID: spoolID, CreatedAt: time.Date(2026, 8, 13, 12, 0, 0, 0, time.UTC),
		WireJob: catalogwriter.WireJob{
			KeyRows: []map[string]any{keyRow}, ValueRows: []map[string]any{valueRow},
			EncodedBytes: encodedBytes, Metadata: metadata,
		},
	}
}

func newTestSpoolPublisher(
	t *testing.T, stateDirectory string, producer EnvelopePublisher,
) *SpoolPublisher {
	t.Helper()
	publisher, err := NewSpoolPublisher(PublisherConfig{
		StateDirectory: stateDirectory, ProducerStreamID: testStream,
		MaxChunkRows: 1, MaxChunkBytes: 8 << 10,
	}, producer)
	if err != nil {
		t.Fatal(err)
	}
	return publisher
}

func TestSpoolPublisherPersistsBeforePublishChainsAndDeduplicates(t *testing.T) {
	stateDirectory := filepath.Join(t.TempDir(), "publisher-state")
	producer := &recordingEnvelopePublisher{}
	publisher := newTestSpoolPublisher(t, stateDirectory, producer)
	first := publisherDelivery(t, strings.Repeat("a", maxSpoolIDBytes))

	producer.before = func(envelope WireEnvelope) {
		state, exists, err := loadPublisherState(publisher.statePath(testProject, 1))
		if err != nil || !exists {
			t.Fatalf("state before publish exists=%v err=%v", exists, err)
		}
		if state.Sequence != 0 || state.PendingSpoolID != first.ID ||
			!bytes.Equal(state.PendingEnvelope, envelope.Marshal()) {
			t.Fatalf("state was not pending before broker call: %+v", state)
		}
	}
	if err := publisher.DeliverCatalogJob(context.Background(), first); err != nil {
		t.Fatal(err)
	}
	producer.before = nil
	records := producer.snapshots()
	if len(records) != 1 || records[0].Sequence() != 1 || records[0].PreviousPayloadSHA256() != ZeroSHA256 {
		t.Fatalf("first records=%v", len(records))
	}
	firstPayloadDigest := records[0].PayloadSHA256()

	statePath := publisher.statePath(testProject, 1)
	state, exists, err := loadPublisherState(statePath)
	if err != nil || !exists {
		t.Fatalf("final state exists=%v err=%v", exists, err)
	}
	if state.LastSpoolID != first.ID || state.Sequence != 1 ||
		state.LastPayloadSHA256 != firstPayloadDigest || state.PendingSpoolID != "" ||
		len(state.PendingEnvelope) != 0 {
		t.Fatalf("unexpected final state: %+v", state)
	}
	if err := publisher.DeliverCatalogJob(context.Background(), first); err != nil {
		t.Fatal(err)
	}
	if len(producer.snapshots()) != 1 {
		t.Fatal("already-completed spool ID was republished")
	}

	second := publisherDelivery(t, strings.Repeat("b", maxSpoolIDBytes))
	if err := publisher.DeliverCatalogJob(context.Background(), second); err != nil {
		t.Fatal(err)
	}
	records = producer.snapshots()
	if len(records) != 2 || records[1].Sequence() != 2 ||
		records[1].PreviousPayloadSHA256() != firstPayloadDigest {
		t.Fatalf("payload chain did not advance: records=%d", len(records))
	}

	directoryInfo, err := os.Stat(stateDirectory)
	if err != nil || directoryInfo.Mode().Perm() != 0o700 {
		t.Fatalf("state directory mode=%v err=%v", directoryInfo.Mode().Perm(), err)
	}
	stateInfo, err := os.Stat(statePath)
	if err != nil || stateInfo.Mode().Perm() != 0o600 {
		t.Fatalf("state file mode=%v err=%v", stateInfo.Mode().Perm(), err)
	}
	encoded, err := os.ReadFile(statePath)
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := jsonMarshalState(stateForPath(t, statePath))
	if err != nil || !bytes.Equal(encoded, canonical) {
		t.Fatalf("state is not canonical JSON: err=%v", err)
	}
}

func TestSpoolPublisherFailureRestartReusesExactPendingBytes(t *testing.T) {
	stateDirectory := filepath.Join(t.TempDir(), "publisher-state")
	brokerFailure := errors.New("broker acknowledgement unavailable")
	firstProducer := &recordingEnvelopePublisher{err: brokerFailure}
	firstPublisher := newTestSpoolPublisher(t, stateDirectory, firstProducer)
	delivery := publisherDelivery(t, strings.Repeat("c", maxSpoolIDBytes))
	if err := firstPublisher.DeliverCatalogJob(context.Background(), delivery); !errors.Is(err, brokerFailure) {
		t.Fatalf("publish error=%v", err)
	}
	firstRecords := firstProducer.snapshots()
	if len(firstRecords) != 1 {
		t.Fatalf("first records=%d", len(firstRecords))
	}
	pendingBytes := firstRecords[0].Marshal()
	state, exists, err := loadPublisherState(firstPublisher.statePath(testProject, 1))
	if err != nil || !exists || state.PendingSpoolID != delivery.ID ||
		!bytes.Equal(state.PendingEnvelope, pendingBytes) {
		t.Fatalf("pending state exists=%v state=%+v err=%v", exists, state, err)
	}

	secondProducer := &recordingEnvelopePublisher{}
	restarted := newTestSpoolPublisher(t, stateDirectory, secondProducer)
	if err := restarted.DeliverCatalogJob(context.Background(), delivery); err != nil {
		t.Fatal(err)
	}
	secondRecords := secondProducer.snapshots()
	if len(secondRecords) != 1 || !bytes.Equal(secondRecords[0].Marshal(), pendingBytes) {
		t.Fatal("restart did not reuse the byte-identical pending envelope")
	}
}

func TestSpoolPublisherRefusesAnotherSpoolWhilePending(t *testing.T) {
	stateDirectory := filepath.Join(t.TempDir(), "publisher-state")
	producer := &recordingEnvelopePublisher{err: errors.New("broker down")}
	publisher := newTestSpoolPublisher(t, stateDirectory, producer)
	first := publisherDelivery(t, strings.Repeat("d", maxSpoolIDBytes))
	if err := publisher.DeliverCatalogJob(context.Background(), first); err == nil {
		t.Fatal("broker failure was hidden")
	}
	stateBefore, _, err := loadPublisherState(publisher.statePath(testProject, 1))
	if err != nil {
		t.Fatal(err)
	}
	second := publisherDelivery(t, strings.Repeat("e", maxSpoolIDBytes))
	if err := publisher.DeliverCatalogJob(context.Background(), second); err == nil ||
		!strings.Contains(err.Error(), "refusing") {
		t.Fatalf("different pending spool error=%v", err)
	}
	stateAfter, _, err := loadPublisherState(publisher.statePath(testProject, 1))
	if err != nil {
		t.Fatal(err)
	}
	if stateAfter.PendingSpoolID != first.ID ||
		!bytes.Equal(stateAfter.PendingEnvelope, stateBefore.PendingEnvelope) ||
		len(producer.snapshots()) != 1 {
		t.Fatal("different spool overwrote or republished pending state")
	}
}

func TestSpoolPublisherAckStateFailureRepublishesSameEnvelope(t *testing.T) {
	stateDirectory := filepath.Join(t.TempDir(), "publisher-state")
	firstProducer := &recordingEnvelopePublisher{}
	publisher := newTestSpoolPublisher(t, stateDirectory, firstProducer)
	realWrite := publisher.writeState
	ackFailure := errors.New("directory fsync failed")
	writes := 0
	publisher.writeState = func(path string, state publisherState) error {
		writes++
		if writes == 2 {
			return ackFailure
		}
		return realWrite(path, state)
	}
	delivery := publisherDelivery(t, strings.Repeat("f", maxSpoolIDBytes))
	if err := publisher.DeliverCatalogJob(context.Background(), delivery); !errors.Is(err, ackFailure) {
		t.Fatalf("ack state error=%v", err)
	}
	firstBytes := firstProducer.snapshots()[0].Marshal()
	state, _, err := loadPublisherState(publisher.statePath(testProject, 1))
	if err != nil || state.PendingSpoolID != delivery.ID || !bytes.Equal(state.PendingEnvelope, firstBytes) {
		t.Fatalf("ack failure did not retain pending state: state=%+v err=%v", state, err)
	}

	secondProducer := &recordingEnvelopePublisher{}
	restarted := newTestSpoolPublisher(t, stateDirectory, secondProducer)
	if err := restarted.DeliverCatalogJob(context.Background(), delivery); err != nil {
		t.Fatal(err)
	}
	if records := secondProducer.snapshots(); len(records) != 1 || !bytes.Equal(records[0].Marshal(), firstBytes) {
		t.Fatal("ambiguous broker acknowledgement changed the retry envelope")
	}
}

func TestSpoolPublisherRejectsMixedUnscopedAndOversizeJobs(t *testing.T) {
	stateDirectory := filepath.Join(t.TempDir(), "publisher-state")
	producer := &recordingEnvelopePublisher{}
	publisher := newTestSpoolPublisher(t, stateDirectory, producer)

	mixed := publisherDelivery(t, strings.Repeat("1", maxSpoolIDBytes))
	mixed.WireJob.Metadata.Projects = append(
		mixed.WireJob.Metadata.Projects,
		catalogwriter.ProjectJobMetadata{ProjectID: "cccccccc-cccc-4ccc-8ccc-cccccccccccc"},
	)
	if err := publisher.DeliverCatalogJob(context.Background(), mixed); err == nil {
		t.Fatal("mixed-project WireJob was accepted")
	}
	unscoped := publisherDelivery(t, strings.Repeat("2", maxSpoolIDBytes))
	unscoped.WireJob.Metadata.UnscopedRejectedSpans = 1
	if err := publisher.DeliverCatalogJob(context.Background(), unscoped); err == nil {
		t.Fatal("unscoped WireJob was accepted")
	}

	large := publisherDelivery(t, strings.Repeat("3", maxSpoolIDBytes))
	large.WireJob.KeyRows = []map[string]any{testKeyRow(350 << 10), testKeyRow(350 << 10)}
	large.WireJob.ValueRows = nil
	large.WireJob.EncodedBytes = len(testRowBytes(t, large.WireJob.KeyRows...))
	large.WireJob.Metadata.KeyRows = 2
	large.WireJob.Metadata.ValueRows = 0
	large.WireJob.Metadata.EncodedBytes = large.WireJob.EncodedBytes
	large.WireJob.Metadata.InputSpans = 2
	large.WireJob.Metadata.AcceptedSpans = 2
	large.WireJob.Metadata.Projects[0].KeyRows = 2
	large.WireJob.Metadata.Projects[0].ValueRows = 0
	large.WireJob.Metadata.Projects[0].InputSpans = 2
	large.WireJob.Metadata.Projects[0].AcceptedSpans = 2
	publisher.maxChunkBytes = 400 << 10
	if err := publisher.DeliverCatalogJob(context.Background(), large); err == nil ||
		!strings.Contains(err.Error(), "encoded envelope") {
		t.Fatalf("oversize record error=%v", err)
	}
	if len(producer.snapshots()) != 0 {
		t.Fatal("rejected job reached producer")
	}
	entries, err := os.ReadDir(stateDirectory)
	if err != nil || len(entries) != 0 {
		t.Fatalf("rejected jobs created publisher state: entries=%d err=%v", len(entries), err)
	}
}

func TestSpoolPublisherRejectsNonCanonicalOrOversizeState(t *testing.T) {
	stateDirectory := filepath.Join(t.TempDir(), "publisher-state")
	failingProducer := &recordingEnvelopePublisher{err: errors.New("broker down")}
	publisher := newTestSpoolPublisher(t, stateDirectory, failingProducer)
	delivery := publisherDelivery(t, strings.Repeat("4", maxSpoolIDBytes))
	if err := publisher.DeliverCatalogJob(context.Background(), delivery); err == nil {
		t.Fatal("expected broker failure")
	}
	statePath := publisher.statePath(testProject, 1)
	encoded, err := os.ReadFile(statePath)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(statePath, append(encoded, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	restartedProducer := &recordingEnvelopePublisher{}
	restarted := newTestSpoolPublisher(t, stateDirectory, restartedProducer)
	if err := restarted.DeliverCatalogJob(context.Background(), delivery); err == nil ||
		!strings.Contains(err.Error(), "canonical JSON") {
		t.Fatalf("non-canonical state error=%v", err)
	}
	if len(restartedProducer.snapshots()) != 0 {
		t.Fatal("corrupt state reached producer")
	}

	if err := os.WriteFile(statePath, make([]byte, maxPublisherStateBytes+1), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := restarted.DeliverCatalogJob(context.Background(), delivery); err == nil ||
		!strings.Contains(err.Error(), "state size") {
		t.Fatalf("oversize state error=%v", err)
	}
}

func TestPublisherSourceDigestUsesOnlyStableSpoolIdentityAndMetadata(t *testing.T) {
	first := publisherDelivery(t, strings.Repeat("5", maxSpoolIDBytes))
	second := first
	second.CreatedAt = first.CreatedAt.Add(24 * time.Hour)
	firstDigest, err := publisherSourceBatchDigest(first)
	if err != nil {
		t.Fatal(err)
	}
	secondDigest, err := publisherSourceBatchDigest(second)
	if err != nil {
		t.Fatal(err)
	}
	if firstDigest != secondDigest || !isLowerSHA256(firstDigest) {
		t.Fatalf("stable source digests differ: %q %q", firstDigest, secondDigest)
	}
	second.WireJob.Metadata.AcceptedSpans++
	changedDigest, err := publisherSourceBatchDigest(second)
	if err != nil {
		t.Fatal(err)
	}
	if changedDigest == firstDigest {
		t.Fatal("metadata change did not alter source batch digest")
	}
}

func stateForPath(t *testing.T, path string) publisherState {
	t.Helper()
	state, exists, err := loadPublisherState(path)
	if err != nil || !exists {
		t.Fatalf("load state exists=%v err=%v", exists, err)
	}
	return state
}

func jsonMarshalState(state publisherState) ([]byte, error) {
	return json.Marshal(state)
}
