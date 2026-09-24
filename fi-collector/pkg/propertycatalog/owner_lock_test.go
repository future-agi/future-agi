package propertycatalog

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSequencerOwnerLockRejectsConcurrentOwnerAndReleasesCleanly(t *testing.T) {
	directory := t.TempDir()
	ownerID := "property-catalog-sequencer-prod-v1"
	first, err := AcquireSequencerOwnerLock(directory, ownerID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := AcquireSequencerOwnerLock(directory, ownerID); err == nil {
		t.Fatal("concurrent singleton owner acquired the same durable state")
	}
	raw, err := os.ReadFile(filepath.Join(directory, sequencerOwnerLockFile))
	if err != nil || len(raw) == 0 || raw[len(raw)-1] != '\n' {
		t.Fatalf("owner marker raw=%q err=%v", raw, err)
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	second, err := AcquireSequencerOwnerLock(directory, ownerID)
	if err != nil {
		t.Fatalf("released owner lock could not be reacquired: %v", err)
	}
	if err := second.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestSequencerOwnerLockRequiresFixedSafeIdentity(t *testing.T) {
	for _, ownerID := range []string{"", " padded", "bad\nidentity"} {
		if _, err := AcquireSequencerOwnerLock(t.TempDir(), ownerID); err == nil {
			t.Fatalf("unsafe owner identity %q was accepted", ownerID)
		}
	}
}
