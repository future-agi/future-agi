package video

import (
	"errors"
	"testing"
)

// An OpenAI-shaped job stored by the earlier salted-fingerprint revision still
// replays for the identical request; nothing else matches a salted digest.
func TestReplayAcceptsLegacyWireFingerprint(t *testing.T) {
	salted := legacyWireFingerprint("fp")
	if _, err := replay(&VideoJob{Wire: WireOpenAI, Fingerprint: salted}, "fp"); err != nil {
		t.Fatal(err)
	}
	if _, err := replay(&VideoJob{Wire: WireOpenAI, Fingerprint: "fp"}, "fp"); err != nil {
		t.Fatal(err)
	}
	for _, j := range []*VideoJob{{Fingerprint: salted}, {Wire: WireOpenAI, Fingerprint: salted}} {
		fp := "fp"
		if j.Wire == WireOpenAI {
			fp = "other"
		}
		if _, err := replay(j, fp); !errors.Is(err, ErrIdempotencyConflict) {
			t.Fatalf("%+v replayed for %s", j, fp)
		}
	}
}
