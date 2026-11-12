package config

import (
	"testing"
	"time"
)

func Test733ControlPlaneSyncIntervalBound(t *testing.T) {
	for _, interval := range []string{"0s", "-1s", "61s", "1h"} {
		t.Run(interval, func(t *testing.T) {
			// A control plane needs a strong admin token to load at all.
			unsetEnv(t, adminTokenEnv...)
			t.Setenv("AGENTCC_ADMIN_TOKEN", strongToken)
			t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://127.0.0.1:8000")
			t.Setenv("AGENTCC_SYNC_INTERVAL", interval)
			cfg, err := Load("")
			if err != nil {
				t.Fatal(err)
			}
			if cfg.ControlPlane.SyncInterval != 60*time.Second {
				t.Fatalf("interval = %s, want 60s", cfg.ControlPlane.SyncInterval)
			}
		})
	}
}
