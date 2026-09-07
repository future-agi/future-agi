package propertycatalog

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"golang.org/x/sys/unix"
)

func testAttemptIntent() ExactWriteAttempt {
	id := testDigest("envelope")
	return ExactWriteAttempt{
		Body: []byte("{\"key\":\"value\"}\n"), Endpoint: "http://replica01:8123",
		EnvelopeID: id, Table: string(DefinitionTable), Token: "property-catalog-v1:" + id + ":chunk:0",
		TopologySHA256: testDigest("topology"),
		Settings:       map[string]string{"async_insert": "0", "wait_end_of_query": "1", "insert_quorum": "3", "insert_quorum_parallel": "1", "insert_deduplicate": "1", "insert_quorum_timeout": "500"},
	}
}

func TestWriteAttemptRestartPreservesExactUnknownWithoutResendTransition(t *testing.T) {
	directory := t.TempDir()
	journal, err := OpenWriteAttemptJournal(directory)
	if err != nil {
		t.Fatal(err)
	}
	intent := testAttemptIntent()
	lease, err := journal.Lock(intent.Token)
	if err != nil {
		t.Fatal(err)
	}
	prepared, err := lease.Prepare(intent)
	if err != nil {
		t.Fatal(err)
	}
	intent.Body[0] = '!' // no caller-owned bytes in the durable intent
	sent, err := lease.Advance(prepared, AttemptSent)
	if err != nil {
		t.Fatal(err)
	}
	_ = lease.Close()
	_ = journal.Close()
	journal, err = OpenWriteAttemptJournal(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer journal.Close()
	lease, _ = journal.Lock(sent.Token)
	defer lease.Close()
	restored, err := lease.Prepare(testAttemptIntent())
	if err != nil || restored.State != AttemptSent || restored.QueryID != sent.QueryID || !bytes.Equal(restored.Body, sent.Body) {
		t.Fatalf("restored=%+v err=%v", restored, err)
	}
	for _, state := range []string{AttemptPrepared, AttemptComplete} {
		if _, err := lease.Advance(restored, state); err == nil {
			t.Fatalf("unknown outcome allowed unsafe transition to %s", state)
		}
	}
	ack, err := lease.Acknowledge(restored, testDigest("settled witness"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := lease.Acknowledge(restored, testDigest("settled witness")); err == nil {
		t.Fatal("stale CAS accepted")
	}
	complete, err := lease.Advance(ack, AttemptComplete)
	if err != nil || complete.State != AttemptComplete {
		t.Fatalf("completion=%+v err=%v", complete, err)
	}
}

func TestWriteAttemptConflictsAndCompetingOwnersFailClosed(t *testing.T) {
	directory := t.TempDir()
	one, _ := OpenWriteAttemptJournal(directory)
	defer one.Close()
	two, _ := OpenWriteAttemptJournal(directory)
	defer two.Close()
	intent := testAttemptIntent()
	lease, err := one.Lock(intent.Token)
	if err != nil {
		t.Fatal(err)
	}
	defer lease.Close()
	if _, err := two.Lock(intent.Token); err == nil {
		t.Fatal("competing journal owner entered")
	}
	if _, err := lease.Prepare(intent); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*ExactWriteAttempt){
		func(a *ExactWriteAttempt) { a.Body = []byte("different\n") },
		func(a *ExactWriteAttempt) { a.Endpoint = "http://other:8123" },
		func(a *ExactWriteAttempt) { a.TopologySHA256 = testDigest("shrunk quorum") },
		func(a *ExactWriteAttempt) { a.Table = string(AttributeValueTable) },
	} {
		changed := testAttemptIntent()
		mutate(&changed)
		if _, err := lease.Prepare(changed); err == nil {
			t.Fatal("conflicting exact attempt accepted")
		}
	}
}

func TestWriteAttemptRejectsSymlinkFIFOHardlinkCorruptionAndOversize(t *testing.T) {
	for _, kind := range []string{"symlink", "fifo", "hardlink", "corrupt", "oversize", "writable"} {
		t.Run(kind, func(t *testing.T) {
			directory := t.TempDir()
			journal, err := OpenWriteAttemptJournal(directory)
			if err != nil {
				t.Fatal(err)
			}
			defer journal.Close()
			intent := testAttemptIntent()
			lease, err := journal.Lock(intent.Token)
			if err != nil {
				t.Fatal(err)
			}
			defer lease.Close()
			path := filepath.Join(directory, WriteAttemptDirectory, lease.key+".json")
			switch kind {
			case "symlink":
				err = os.Symlink(filepath.Join(directory, "absent"), path)
			case "fifo":
				err = unix.Mkfifo(path, 0o600)
			case "hardlink":
				other := filepath.Join(directory, "other")
				err = os.WriteFile(other, []byte("{}\n"), 0o600)
				if err == nil {
					err = os.Link(other, path)
				}
			case "corrupt":
				err = os.WriteFile(path, []byte("{}\n"), 0o600)
			case "oversize":
				f, createErr := os.OpenFile(path, os.O_CREATE|os.O_WRONLY, 0o600)
				err = createErr
				if err == nil {
					err = f.Truncate(maxWriteAttemptBytes + 1)
					f.Close()
				}
			case "writable":
				_, err = lease.Prepare(intent)
				if err == nil {
					err = os.Chmod(path, 0o666)
				}
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err := lease.Prepare(intent); err == nil || errors.Is(err, unix.ENOENT) {
				t.Fatalf("unsafe %s treated as a missing/replayable record: %v", kind, err)
			}
		})
	}
}

func TestWriteAttemptMissingInstallationAndSymlinkDirectoryFail(t *testing.T) {
	directory := t.TempDir()
	for _, path := range []string{"", "relative", "/", filepath.Join(directory, "absent")} {
		if j, err := OpenWriteAttemptJournal(path); err == nil {
			j.Close()
			t.Fatalf("unsafe installation accepted: %q", path)
		}
	}
	if err := os.Symlink(t.TempDir(), filepath.Join(directory, WriteAttemptDirectory)); err != nil {
		t.Fatal(err)
	}
	if j, err := OpenWriteAttemptJournal(directory); err == nil {
		j.Close()
		t.Fatal("symlink journal accepted")
	}
}
