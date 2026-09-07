package propertycatalog

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"testing"
)

func TestCandidateRepairSurvivesCompletionCompactionAndAcknowledgesExactGeneration(t *testing.T) {
	directory, topic := t.TempDir(), "repair-candidates-test"
	store := testReceiptStore(t, directory, topic)
	store.cfg.MaxRecentIDs = 1
	path := candidateRepairPath(directory, testOrganization, testWorkspace)
	complete := func(offset int64, value string) {
		t.Helper()
		record, _ := testCandidateRecord(t, topic, offset, value)
		receipt, done, err := store.Receive(record)
		if err != nil || done {
			t.Fatalf("receive done=%v err=%v", done, err)
		}
		if err := store.CompleteNotAdmitted(receipt, CandidateOutsideBuildSourceScope); err != nil {
			t.Fatal(err)
		}
	}
	complete(41, "old")
	raw, err := readCandidateRepair(path)
	if err != nil {
		t.Fatal(err)
	}
	doc, err := parseCandidateRepair(raw, topic, testOrganization, testWorkspace)
	if err != nil || doc.Generation != 1 {
		t.Fatalf("first request=%+v err=%v", doc, err)
	}
	// Model an earlier coalesced late observation; new candidates must retain
	// its lower bound until that exact request is acknowledged.
	doc.FirstSeenUS--
	older, _ := json.Marshal(doc)
	older = append(older, '\n')
	if err := os.WriteFile(path, older, 0o600); err != nil {
		t.Fatal(err)
	}
	complete(42, "new")
	newer, _ := readCandidateRepair(path)
	coalesced, err := parseCandidateRepair(newer, topic, testOrganization, testWorkspace)
	if err != nil || coalesced.Generation != 2 || coalesced.FirstSeenUS != doc.FirstSeenUS {
		t.Fatalf("coalesced=%+v err=%v", coalesced, err)
	}
	if len(store.completions) != 1 {
		t.Fatal("fixture did not compact completion history")
	}
	if err := os.WriteFile(path+".ack", older, 0o600); err != nil {
		t.Fatal(err)
	}
	complete(43, "after-stale-ack")
	afterStale, _ := readCandidateRepair(path)
	stillPending, _ := parseCandidateRepair(afterStale, topic, testOrganization, testWorkspace)
	if stillPending.FirstSeenUS != doc.FirstSeenUS || stillPending.Generation != 3 {
		t.Fatal("stale acknowledgement lost pending work")
	}
	if err := os.WriteFile(path+".ack", afterStale, 0o600); err != nil {
		t.Fatal(err)
	}
	complete(44, "after-exact-ack")
	reset, _ := readCandidateRepair(path)
	fresh, _ := parseCandidateRepair(reset, topic, testOrganization, testWorkspace)
	if fresh.FirstSeenUS != doc.FirstSeenUS+1 || fresh.Generation != 4 {
		t.Fatal("acknowledged old lower bound was needlessly retained")
	}
}

func TestCandidateRepairFailureRetainsReceiptAndReplayIsIdempotent(t *testing.T) {
	directory, topic := t.TempDir(), "repair-candidates-test"
	store := testReceiptStore(t, directory, topic)
	record, _ := testCandidateRecord(t, topic, 41, "repair-sync-crash")
	receipt, _, err := store.Receive(record)
	if err != nil {
		t.Fatal(err)
	}
	store.syncDir = func(string) error { return errors.New("request fsync uncertain") }
	if err := store.CompleteNotAdmitted(receipt, CandidateNoCurrentBuildFence); err == nil {
		t.Fatal("uncertain request was reported as completed")
	}
	if done, err := store.Completed(receipt); err != nil || done {
		t.Fatalf("completion advanced across failed request sync: %v %v", done, err)
	}
	path := candidateRepairPath(directory, testOrganization, testWorkspace)
	before, _ := readCandidateRepair(path)
	restarted := testReceiptStore(t, directory, topic)
	pending, err := restarted.Pending()
	if err != nil || len(pending) != 1 {
		t.Fatalf("pending receipt lost: %v %v", pending, err)
	}
	if err := restarted.CompleteNotAdmitted(pending[0], CandidateNoCurrentBuildFence); err != nil {
		t.Fatal(err)
	}
	after, _ := readCandidateRepair(path)
	if !bytes.Equal(before, after) || restarted.SkippedTotal() != 1 {
		t.Fatal("replay changed request generation or skip accounting")
	}
}

func TestCandidateRepairRejectsCorruptOrCrossScopeFiles(t *testing.T) {
	directory, topic := t.TempDir(), "repair-candidates-test"
	store := testReceiptStore(t, directory, topic)
	record, _ := testCandidateRecord(t, topic, 41, "repair")
	receipt, _, err := store.Receive(record)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.persistRepairRequest(receipt); err != nil {
		t.Fatal(err)
	}
	path := candidateRepairPath(directory, testOrganization, testWorkspace)
	raw, _ := readCandidateRepair(path)
	if _, err := parseCandidateRepair(raw, "other-topic", testOrganization, testWorkspace); err == nil {
		t.Fatal("cross-topic request admitted")
	}
	if _, err := parseCandidateRepair(append(raw, ' '), topic, testOrganization, testWorkspace); err == nil {
		t.Fatal("noncanonical request admitted")
	}
	link := path + ".link"
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if _, err := readCandidateRepair(link); err == nil {
		t.Fatal("repair symlink followed")
	}
}

func TestClaimSeparatesNewArrivalsBeforeOldRepairFinishes(t *testing.T) {
	directory, topic := t.TempDir(), "repair-candidates-test"
	store := testReceiptStore(t, directory, topic)
	record, _ := testCandidateRecord(t, topic, 41, "old")
	receipt, _, _ := store.Receive(record)
	if err := store.CompleteNotAdmitted(receipt, CandidateNoCurrentBuildFence); err != nil {
		t.Fatal(err)
	}
	path := candidateRepairPath(directory, testOrganization, testWorkspace)
	raw, _ := readCandidateRepair(path)
	doc, _ := parseCandidateRepair(raw, topic, testOrganization, testWorkspace)
	doc.FirstSeenUS--
	raw, _ = json.Marshal(doc)
	raw = append(raw, '\n')
	for _, name := range []string{path, path + ".claim"} {
		if err := os.WriteFile(name, raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	// The scan is still running: no ack exists. New arrivals must no longer
	// inherit the old lower bound, which is safe in the separate durable claim.
	for offset := int64(42); offset < 45; offset++ {
		record, _ = testCandidateRecord(t, topic, offset, string(rune('a'+offset)))
		receipt, _, _ = store.Receive(record)
		if err := store.CompleteNotAdmitted(receipt, CandidateNoCurrentBuildFence); err != nil {
			t.Fatal(err)
		}
		pending, _ := readCandidateRepair(path)
		newDoc, err := parseCandidateRepair(pending, topic, testOrganization, testWorkspace)
		if err != nil || newDoc.FirstSeenUS != doc.FirstSeenUS+1 {
			t.Fatalf("continuous arrivals retained already-claimed history: %+v %v", newDoc, err)
		}
	}
	claim, _ := readCandidateRepair(path + ".claim")
	if !bytes.Equal(claim, raw) {
		t.Fatal("producer changed the lifecycle-owned claim")
	}
}
