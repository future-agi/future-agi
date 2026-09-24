package propertycatalog

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
)

func testCandidateRecord(t *testing.T, topic string, offset int64, value string) (catalogkafka.Record, WireCandidate) {
	return testCandidateRecordForWorkspace(t, topic, offset, value, testWorkspace)
}

func testCandidateRecordForWorkspace(
	t *testing.T, topic string, offset int64, value, workspaceID string,
) (catalogkafka.Record, WireCandidate) {
	t.Helper()
	candidate := mustCandidates(t, candidateRuntimeConfig(t), []ScopedSpan{
		scopedHotRow(workspaceID, testProject, hotRow(
			testProject, testSeen, map[string]string{"candidate": value}, nil,
		)),
	})[0]
	raw, err := candidate.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	key, err := CandidateKafkaKey(candidate)
	if err != nil {
		t.Fatal(err)
	}
	return catalogkafka.Record{
		Topic: topic, Key: key, Value: raw, Partition: 2, Offset: offset, LeaderEpoch: 7,
	}, candidate
}

type queuedCandidateSource struct {
	records []catalogkafka.Record
	store   *CandidateReceiptStore
	next    int
	commits []int64
	allowed int
}

func (s *queuedCandidateSource) PollOne(context.Context) (catalogkafka.Record, error) {
	if s.next >= len(s.records) {
		return catalogkafka.Record{}, errors.New("no scripted candidate")
	}
	record := s.records[s.next]
	s.next++
	return record, nil
}

func (s *queuedCandidateSource) Commit(_ context.Context, record catalogkafka.Record) error {
	pending, err := s.store.Pending()
	if err != nil || len(pending) == 0 {
		return errors.New("candidate offset commit preceded durable receipt")
	}
	s.commits = append(s.commits, record.Offset)
	return nil
}

func (s *queuedCandidateSource) AllowRebalance() { s.allowed++ }
func (*queuedCandidateSource) Close()            {}

func testReceiptStore(t *testing.T, directory, topic string) *CandidateReceiptStore {
	t.Helper()
	store, err := NewCandidateReceiptStore(CandidateReceiptStoreConfig{
		Directory: directory, Topic: topic, MaxRecentIDs: 8,
	})
	if err != nil {
		t.Fatal(err)
	}
	return store
}

func TestCandidateReceiptSurvivesRestartAndCompletionDedupesRedelivery(t *testing.T) {
	directory := t.TempDir()
	topic := "property-candidates-v1-test"
	record, _ := testCandidateRecord(t, topic, 41, "first")
	store := testReceiptStore(t, directory, topic)
	receipt, completed, err := store.Receive(record)
	if err != nil || completed {
		t.Fatalf("receive completed=%v err=%v", completed, err)
	}
	if _, err := os.Stat(filepath.Join(directory, candidateReceiptName(2, 41))); err != nil {
		t.Fatalf("receipt was not durable before commit boundary: %v", err)
	}

	restarted := testReceiptStore(t, directory, topic)
	pending, err := restarted.Pending()
	if err != nil || len(pending) != 1 || pending[0].Candidate().Snapshot().CandidateID != receipt.Candidate().Snapshot().CandidateID {
		t.Fatalf("pending=%+v err=%v", pending, err)
	}
	if err := restarted.Complete(pending[0]); err != nil {
		t.Fatal(err)
	}
	restartedAgain := testReceiptStore(t, directory, topic)
	if _, duplicate, err := restartedAgain.Receive(record); err != nil || !duplicate {
		t.Fatalf("durable duplicate=%v err=%v", duplicate, err)
	}
}

func TestSkippedCompletionSurvivesReceiptRemovalSyncFailureWithoutReclassification(t *testing.T) {
	directory := t.TempDir()
	topic := "property-candidates-v1-test"
	record, _ := testCandidateRecord(t, topic, 45, "skip-removal-crash")
	store := testReceiptStore(t, directory, topic)
	receipt, completed, err := store.Receive(record)
	if err != nil || completed {
		t.Fatalf("receive completed=%v err=%v", completed, err)
	}
	syncCalls := 0
	store.syncDir = func(path string) error {
		syncCalls++
		if syncCalls == 2 {
			return errors.New("crash after receipt removal")
		}
		return syncDirectory(path)
	}
	if err := store.CompleteNotAdmitted(receipt, CandidateNoCurrentBuildFence); err == nil {
		t.Fatal("injected post-removal directory sync failure was ignored")
	}
	if store.pendingFiles != 0 || store.pendingBytes != 0 {
		t.Fatalf("removed receipt accounting files=%d bytes=%d", store.pendingFiles, store.pendingBytes)
	}
	// A replay path may call ordinary Complete after Completed reports true.
	// It must preserve the already-durable skipped disposition.
	store.syncDir = syncDirectory
	if err := store.Complete(receipt); err != nil {
		t.Fatalf("durable skipped completion was reclassified: %v", err)
	}
	restarted := testReceiptStore(t, directory, topic)
	completion := restarted.completions[candidateCoordinate{partition: 2, offset: 45}]
	if restarted.SkippedTotal() != 1 ||
		completion.Disposition != candidateCompletionNotAdmitted ||
		completion.SkipReason != CandidateNoCurrentBuildFence {
		t.Fatalf("durable skip total=%d completion=%+v", restarted.SkippedTotal(), completion)
	}
}

type scriptedCandidateSource struct {
	record          catalogkafka.Record
	store           *CandidateReceiptStore
	commitErr       error
	polls           int
	commits         int
	allowed         int
	receiptAtCommit bool
}

func (s *scriptedCandidateSource) PollOne(context.Context) (catalogkafka.Record, error) {
	s.polls++
	return s.record, nil
}

func (s *scriptedCandidateSource) Commit(context.Context, catalogkafka.Record) error {
	s.commits++
	pending, err := s.store.Pending()
	s.receiptAtCommit = err == nil && len(pending) == 1
	return s.commitErr
}

func (s *scriptedCandidateSource) AllowRebalance() { s.allowed++ }
func (*scriptedCandidateSource) Close()            {}

type scriptedCandidateAcceptor struct {
	ids []string
	err error
}

func (a *scriptedCandidateAcceptor) AcceptCandidate(candidate WireCandidate) (bool, error) {
	a.ids = append(a.ids, candidate.Snapshot().CandidateID)
	return false, a.err
}

func TestSequencerCommitsOnlyAfterFsyncReceiptAndReplaysAfterProcessingCrash(t *testing.T) {
	directory := t.TempDir()
	topic := "property-candidates-v1-test"
	record, candidate := testCandidateRecord(t, topic, 51, "crash")
	store := testReceiptStore(t, directory, topic)
	source := &scriptedCandidateSource{record: record, store: store}
	acceptor := &scriptedCandidateAcceptor{err: errors.New("crash after offset commit")}
	sequencer, err := NewCandidateSequencer(topic, source, store, acceptor)
	if err != nil {
		t.Fatal(err)
	}
	if err := sequencer.ProcessOne(context.Background()); err == nil {
		t.Fatal("injected processing crash was ignored")
	}
	if source.commits != 1 || !source.receiptAtCommit || source.allowed != 1 {
		t.Fatalf("commit boundary commits=%d receipt=%v allowed=%d", source.commits, source.receiptAtCommit, source.allowed)
	}
	if pending, err := store.Pending(); err != nil || len(pending) != 1 {
		t.Fatalf("pending after crash=%d err=%v", len(pending), err)
	}

	restarted := testReceiptStore(t, directory, topic)
	replayAcceptor := &scriptedCandidateAcceptor{}
	replaySource := &scriptedCandidateSource{store: restarted}
	replay, err := NewCandidateSequencer(topic, replaySource, restarted, replayAcceptor)
	if err != nil {
		t.Fatal(err)
	}
	if err := replay.ReplayPending(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(replayAcceptor.ids) != 1 || replayAcceptor.ids[0] != candidate.Snapshot().CandidateID {
		t.Fatalf("replayed candidate IDs=%v", replayAcceptor.ids)
	}
	if pending, err := restarted.Pending(); err != nil || len(pending) != 0 {
		t.Fatalf("pending after replay=%d err=%v", len(pending), err)
	}
}

func TestSequencerCommitFailureLeavesReceiptAndNeverSequences(t *testing.T) {
	directory := t.TempDir()
	topic := "property-candidates-v1-test"
	record, _ := testCandidateRecord(t, topic, 61, "commit-failure")
	store := testReceiptStore(t, directory, topic)
	source := &scriptedCandidateSource{
		record: record, store: store, commitErr: errors.New("ambiguous commit"),
	}
	acceptor := &scriptedCandidateAcceptor{}
	sequencer, err := NewCandidateSequencer(topic, source, store, acceptor)
	if err != nil {
		t.Fatal(err)
	}
	if err := sequencer.ProcessOne(context.Background()); err == nil {
		t.Fatal("commit failure was ignored")
	}
	if !source.receiptAtCommit || len(acceptor.ids) != 0 {
		t.Fatalf("receipt_at_commit=%v sequenced=%v", source.receiptAtCommit, acceptor.ids)
	}
	if pending, err := store.Pending(); err != nil || len(pending) != 1 {
		t.Fatalf("pending after commit failure=%d err=%v", len(pending), err)
	}
}

func TestSequencerSkipsNonAdmittedWorkspaceAndProgressesToAdmittedWorkspace(t *testing.T) {
	directory := t.TempDir()
	topic := "property-candidates-v1-test"
	dark, _ := testCandidateRecordForWorkspace(t, topic, 71, "dark", testWorkspace)
	admitted, admittedCandidate := testCandidateRecordForWorkspace(
		t, topic, 72, "admitted", testWorkspaceTwo,
	)
	store := testReceiptStore(t, directory, topic)
	source := &queuedCandidateSource{records: []catalogkafka.Record{dark, admitted}, store: store}

	cfg := validRuntimeConfig(t).WithDefaults()
	cfg.Mode = RuntimeSequencer
	cfg.WorkspaceScopeMode = WorkspaceScopeRevisionFence
	cfg.WorkspaceAllowlist = nil
	provider := testMutableProvider(17, testWorkspaceTwo)
	runtime, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}
	sequencer, err := NewCandidateSequencer(topic, source, store, runtime)
	if err != nil {
		t.Fatal(err)
	}

	if err := sequencer.ProcessOne(context.Background()); err != nil {
		t.Fatalf("non-admitted workspace stopped singleton progress: %v", err)
	}
	if sequencer.SkippedCandidates() != 1 {
		t.Fatalf("skipped candidates=%d", sequencer.SkippedCandidates())
	}
	select {
	case gap := <-sequencer.Gaps():
		var notAdmitted *CandidateNotAdmittedError
		if !errors.As(gap, &notAdmitted) ||
			notAdmitted.Reason != CandidateNoCurrentBuildFence ||
			notAdmitted.WorkspaceID != testWorkspace {
			t.Fatalf("non-admission gap=%v", gap)
		}
	default:
		t.Fatal("non-admitted candidate was not observable")
	}
	if pending, err := store.Pending(); err != nil || len(pending) != 0 {
		t.Fatalf("dark workspace receipt was not durably completed: pending=%d err=%v", len(pending), err)
	}
	restartedStore := testReceiptStore(t, directory, topic)
	completion := restartedStore.completions[candidateCoordinate{partition: 2, offset: 71}]
	if restartedStore.SkippedTotal() != 1 ||
		completion.Disposition != candidateCompletionNotAdmitted ||
		completion.SkipReason != CandidateNoCurrentBuildFence {
		t.Fatalf(
			"durable skip total=%d completion=%+v",
			restartedStore.SkippedTotal(), completion,
		)
	}

	if err := sequencer.ProcessOne(context.Background()); err != nil {
		t.Fatalf("admitted workspace did not progress after skip: %v", err)
	}
	if len(source.commits) != 2 || source.commits[0] != 71 || source.commits[1] != 72 || source.allowed != 2 {
		t.Fatalf("commits=%v allowed=%d", source.commits, source.allowed)
	}
	pending, err := runtime.spool.PendingEnvelopes()
	if err != nil || len(pending) != 1 ||
		pending[0].Snapshot().WorkspaceID != testWorkspaceTwo ||
		pending[0].Snapshot().Payload.SourceBatchDigest != admittedCandidate.Snapshot().CandidateID {
		t.Fatalf("admitted ordered spool=%+v err=%v", pending, err)
	}
	if receipts, err := store.Pending(); err != nil || len(receipts) != 0 {
		t.Fatalf("candidate receipts after progress=%d err=%v", len(receipts), err)
	}
}

func TestCandidateFenceConflictsRemainFailClosedAndRetryable(t *testing.T) {
	candidate := mustCandidates(t, candidateRuntimeConfig(t), []ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(
			testProject, testSeen, map[string]string{"candidate": "conflict"}, nil,
		)),
	})[0]
	cfg := validRuntimeConfig(t).WithDefaults()
	cfg.Mode = RuntimeSequencer
	provider := testMutableProvider(17, testWorkspace)
	fence, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace)
	if err != nil {
		t.Fatal(err)
	}
	fence.ProjectionVersion++
	provider.set(fence)
	runtime, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := runtime.AcceptCandidate(candidate); err == nil || errors.Is(err, ErrCandidateNotAdmitted) {
		t.Fatalf("epoch/projection conflict was converted into a skip: %v", err)
	}
}

func TestHotRuntimeDedupesCrashAfterOrderedSpoolBeforeReceiptCompletion(t *testing.T) {
	candidate := mustCandidates(t, candidateRuntimeConfig(t), []ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testSeen, map[string]string{"a": "one"}, nil)),
	})[0]
	cfg := validRuntimeConfig(t).WithDefaults()
	cfg.Mode = RuntimeSequencer
	provider := testMutableProvider(17, testWorkspace)
	first, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}
	duplicate, err := first.AcceptCandidate(candidate)
	if err != nil || duplicate {
		t.Fatalf("first acceptance duplicate=%v err=%v", duplicate, err)
	}
	pending, err := first.spool.PendingEnvelopes()
	if err != nil || len(pending) != 1 || pending[0].Snapshot().Sequence != 1 {
		t.Fatalf("first pending=%+v err=%v", pending, err)
	}

	// Simulate process death before receipt completion: reconstruct solely from
	// the fsynced ordered spool and retry the same receipt.
	restarted, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}
	duplicate, err = restarted.AcceptCandidate(candidate)
	if err != nil || !duplicate {
		t.Fatalf("restart duplicate=%v err=%v", duplicate, err)
	}
	pending, err = restarted.spool.PendingEnvelopes()
	if err != nil || len(pending) != 1 {
		t.Fatalf("duplicate created another envelope: count=%d err=%v", len(pending), err)
	}
}

func TestReceiptCompactionFailsClosedForForgottenOffsets(t *testing.T) {
	directory := t.TempDir()
	topic := "property-candidates-v1-test"
	store, err := NewCandidateReceiptStore(CandidateReceiptStoreConfig{
		Directory: directory, Topic: topic, MaxRecentIDs: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	first, _ := testCandidateRecord(t, topic, 0, "zero")
	second, _ := testCandidateRecord(t, topic, 1, "one")
	for _, record := range []catalogkafka.Record{first, second} {
		receipt, _, receiveErr := store.Receive(record)
		if receiveErr != nil {
			t.Fatal(receiveErr)
		}
		if err := store.Complete(receipt); err != nil {
			t.Fatal(err)
		}
	}
	if _, _, err := store.Receive(first); !errors.Is(err, ErrCandidateHistoryCompacted) {
		t.Fatalf("forgotten offset did not fail closed: %v", err)
	}
}
