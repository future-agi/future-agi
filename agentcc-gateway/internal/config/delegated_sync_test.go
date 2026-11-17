package config

import (
	"testing"
	"time"
)

func TestDelegated733SyncIntervalBound(t *testing.T) {
	for _, input := range []time.Duration{-time.Second, 0, 30 * time.Second, time.Minute, 2 * time.Minute} {
		c := DefaultConfig()
		c.ControlPlane.URL = "http://127.0.0.1:9"
		c.ControlPlane.SyncInterval = input
		// Validate may also report an unrelated missing provider; the normalization
		// is independent and must run even for an otherwise invalid config.
		_ = c.Validate()
		want := input
		if input <= 0 || input > time.Minute {
			want = time.Minute
		}
		if c.ControlPlane.SyncInterval != want {
			t.Errorf("%s -> %s, want %s", input, c.ControlPlane.SyncInterval, want)
		}
	}
	c := DefaultConfig()
	c.ControlPlane.SyncInterval = 0
	_ = c.Validate()
	if c.ControlPlane.SyncInterval != 0 {
		t.Fatal("standalone sync changed")
	}
}
