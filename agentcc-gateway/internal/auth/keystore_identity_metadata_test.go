package auth

import (
	"errors"
	"strings"
	"testing"
)

// id, name and owner are reserved for the key's own identity, so an update
// cannot add them either.
func TestUpdate_RefusesToAddIdentityMetadata(t *testing.T) {
	for _, name := range []string{"id", "name", "owner"} {
		t.Run(name, func(t *testing.T) {
			ks := NewKeyStore(authCfg())
			key, _ := ks.Create("plain", "alice", nil, nil, map[string]string{"org_id": "org-a"})

			_, err := ks.Update(key.ID, nil, nil, nil, nil, map[string]string{name: "spoofed", "env": "prod"})

			var reserved *ReservedMetadataError
			if !errors.As(err, &reserved) || strings.Join(reserved.Keys, ",") != name {
				t.Fatalf("expected a ReservedMetadataError naming %s, got %v", name, err)
			}
			if got := ks.Get(key.ID).Metadata; len(got) != 1 {
				t.Errorf("metadata changed: %v", got)
			}
		})
	}
}
