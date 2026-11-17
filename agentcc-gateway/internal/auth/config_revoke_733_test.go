package auth

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func Test733ConfigRevokeIsControlPlaneOnly(t *testing.T) {
	for _, controlPlane := range []bool{false, true} {
		name := "standalone"
		if controlPlane {
			name = "control_plane"
		}
		t.Run(name, func(t *testing.T) {
			raw := generateKey()
			ks := NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: raw, KeyType: "internal"}}}, controlPlane)
			if controlPlane {
				pull733(t, ks, map[string]interface{}{"status": true, "result": []interface{}{}, "config_conflicts": []string{}})
			}
			key := ks.Authenticate(raw)
			if key == nil {
				t.Fatal("configured key did not authenticate before revoke")
			}
			if revoked := ks.Revoke(key.ID); revoked == controlPlane {
				t.Fatalf("revoked=%v, control_plane=%v", revoked, controlPlane)
			}
			if active := ks.Authenticate(raw) != nil; active != controlPlane {
				t.Fatalf("active=%v, control_plane=%v", active, controlPlane)
			}
		})
	}
}
