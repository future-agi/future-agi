package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestDrainProofPersistenceRetainsExpiredSafetyAcrossRestarts(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cfg := validRuntimeConfig(t, testWorkspace, testWorkspaceTwo).WithDefaults()
	cfg.Mode = RuntimeSequencer
	building, draining := testRevisionFence(17, "building"), drainingTestFence(17, 0)
	draining.WorkspaceID = testWorkspaceTwo
	raw, err := EncodeRevisionFenceFile([]RevisionFence{building, draining})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfg.RevisionFenceFile, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	provider, err := NewFileRevisionProvider(cfg.RevisionFenceFile)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC)
	provider.now = func() time.Time { return now }
	downstream := &recordingEnvelopePublisher{}
	runtime, err := NewHotRuntime(cfg, provider, downstream)
	if err != nil {
		t.Fatal(err)
	}
	if err := runtime.observeDraining(ctx); err != nil {
		t.Fatal(err)
	}
	if err := runtime.persistDrainProofs(ctx); err != nil {
		t.Fatal(err)
	}
	row := scopedHotRow(testWorkspace, testProject, hotRow(
		testProject, testSeen, map[string]string{"accepted": "pending"}, map[string]float64{},
	))
	// Leave the accepted batch in memory: the next process must poison it.
	if err := runtime.EnqueueCanonicalSpans([]ScopedSpan{row}); err != nil {
		t.Fatal(err)
	}
	before, err := runtime.DrainProofs(ctx)
	if err != nil || len(before) != 2 || before[0].PendingAdmissions != 1 || before[0].Poisoned ||
		before[1].Phase != "prepared" || before[1].TerminalSequence != 1 || before[1].Ready {
		t.Fatalf("initial safety evidence: %+v %v", before, err)
	}
	poisonKey, drainKey := proofStreamKey(before[0]), proofStreamKey(before[1])
	wantDrain := runtime.drains[drainKey]
	if !wantDrain.prepared {
		t.Fatal("initial boundary was not durably prepared")
	}

	// Expiry changes authority, not the stored evidence. No runtime goroutines
	// have started yet, so advancing the provider clock is deterministic.
	now = now.Add(3 * time.Minute)
	if fences, err := provider.CurrentRevisions(ctx); err != nil || len(fences) != 0 {
		t.Fatalf("expired assignments remained current: %+v %v", fences, err)
	}
	if err := runtime.persistDrainProofs(ctx); err != nil {
		t.Fatal(err)
	}
	persisted, err := os.ReadFile(runtime.DrainProofPath())
	if err != nil {
		t.Fatal(err)
	}
	var document drainProofDocument
	if err := json.Unmarshal(persisted, &document); err != nil || !reflect.DeepEqual(document.Proofs, before) {
		t.Fatalf("expiry erased or changed durable evidence: %+v %v", document.Proofs, err)
	}

	firstRestart, err := NewHotRuntime(cfg, provider, downstream)
	if err != nil {
		t.Fatal(err)
	}
	if !firstRestart.admissionPoison[poisonKey] || firstRestart.drains[drainKey] != wantDrain {
		t.Fatal("first restart lost poison or prepared boundary")
	}
	// Exercise the actual startup persistence path, then shut down its workers
	// before simulating a second process on the same durable files.
	if err := firstRestart.Start(ctx); err != nil {
		t.Fatal(err)
	}
	if err := firstRestart.Shutdown(ctx); err != nil {
		t.Fatal(err)
	}
	secondRestart, err := NewHotRuntime(cfg, provider, downstream)
	if err != nil {
		t.Fatal(err)
	}
	if !secondRestart.admissionPoison[poisonKey] || secondRestart.drains[drainKey] != wantDrain {
		t.Fatal("second restart lost poison or prepared boundary")
	}
	proofs, err := secondRestart.DrainProofs(ctx)
	if err != nil || len(proofs) != 2 || !proofs[0].Poisoned || proofs[0].PendingAdmissions != 0 ||
		proofs[1].Phase != "prepared" || proofs[1].TerminalSequence != before[1].TerminalSequence {
		t.Fatalf("restarted safety evidence: %+v %v", proofs, err)
	}
	for _, proof := range proofs {
		if proof.Ready || proof.TerminalIssued || proof.TerminalAcknowledged {
			t.Fatalf("historical safety evidence granted terminal success: %+v", proof)
		}
		if _, err := provider.CurrentRevision(ctx, proof.OrganizationID, proof.WorkspaceID); !errors.Is(err, ErrRevisionNotAssigned) {
			t.Fatalf("historical safety evidence granted an assignment: %v", err)
		}
	}
	if err := secondRestart.observeDraining(ctx); err != nil {
		t.Fatal(err)
	}
	if pending, err := secondRestart.spool.PendingEnvelopes(); err != nil || len(pending) != 0 {
		t.Fatalf("expired drain issued an envelope: pending=%d err=%v", len(pending), err)
	}
	if submission, err := secondRestart.admitSubmission([]ScopedSpan{row}); !errors.Is(err, ErrRevisionNotAssigned) || len(submission.streams) != 0 {
		t.Fatalf("expired scope admitted a submission: %+v %v", submission, err)
	}
	candidates := mustCandidates(t, candidateRuntimeConfig(t), []ScopedSpan{row})
	if len(candidates) != 1 {
		t.Fatalf("candidates=%d", len(candidates))
	}
	if _, err := secondRestart.AcceptCandidate(candidates[0]); !errors.Is(err, ErrCandidateNotAdmitted) {
		t.Fatalf("expired scope admitted a candidate: %v", err)
	}
	group, err := candidates[0].hotGroup()
	if err != nil {
		t.Fatal(err)
	}
	envelope, err := buildHotEnvelope(cfg, building, group, 1, ZeroSHA256)
	if err != nil {
		t.Fatal(err)
	}
	if err := secondRestart.spool.Enqueue(envelope); err != nil {
		t.Fatal(err)
	}
	if _, err := secondRestart.spool.Replay(ctx, secondRestart.publisher); !errors.Is(err, ErrRevisionNotAssigned) {
		t.Fatalf("expired scope gained replay authority: %v", err)
	}
	if pending, err := secondRestart.spool.PendingEnvelopes(); err != nil || len(pending) != 1 ||
		len(downstream.envelopes) != 0 || len(secondRestart.publisher.state.snapshot()) != 0 {
		t.Fatalf("expired queued envelope was published or discarded: pending=%d err=%v", len(pending), err)
	}

	// Retention must also fail closed without replacing the last good safety
	// file if an expired assignment is subsequently tampered with.
	persisted, err = os.ReadFile(secondRestart.DrainProofPath())
	if err != nil {
		t.Fatal(err)
	}
	tampered := bytes.Replace(raw, []byte(`"catalog_revision":17`), []byte(`"catalog_revision":19`), 1)
	if err := os.WriteFile(cfg.RevisionFenceFile, tampered, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := secondRestart.persistDrainProofs(ctx); err == nil || !strings.Contains(err.Error(), "digest") {
		t.Fatalf("retention accepted corrupt expired evidence: %v", err)
	}
	after, err := os.ReadFile(secondRestart.DrainProofPath())
	if err != nil || !bytes.Equal(after, persisted) {
		t.Fatalf("corrupt expired inventory overwrote safety evidence: %v", err)
	}
}
