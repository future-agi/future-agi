package config

import (
	"strings"
	"testing"
)

// TestScheduledEnabledIsConfigError: routing.scheduled.enabled: true fails
// Validate: this gateway does not run scheduled jobs, so it refuses to start
// with it.
func TestScheduledEnabledIsConfigError(t *testing.T) {
	c := DefaultConfig()
	c.Routing.Scheduled.Enabled = true
	err := c.Validate()
	if err == nil {
		t.Fatal("Validate accepted routing.scheduled.enabled: true")
	}
	for _, want := range []string{"routing.scheduled.enabled", "must be false", "this gateway", "does not run scheduled jobs"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not say %q", err, want)
		}
	}

	if _, err := loadYAML(t, "routing:\n  scheduled:\n    enabled: true\n    worker_count: 2\n"); err == nil || !strings.Contains(err.Error(), "routing.scheduled.enabled") {
		t.Errorf("Load = %v, want the scheduled refusal", err)
	}
	// The other scheduled keys still parse while the feature is off.
	c, err = loadYAML(t, "routing:\n  scheduled:\n    enabled: false\n    worker_count: 2\n")
	if err != nil {
		t.Fatal(err)
	}
	if c.Routing.Scheduled.WorkerCount != 2 {
		t.Errorf("worker_count = %d", c.Routing.Scheduled.WorkerCount)
	}
}
