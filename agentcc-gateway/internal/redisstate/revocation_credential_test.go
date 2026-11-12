package redisstate

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
)

func TestRevocationIDOnlyIsNeverDurable(t *testing.T) {
	for _, identity := range []string{"", "key_1", "sha256:key_1"} {
		b, r := newRevocationFloorFixture()
		if err := b.PublishCredentialWithError(context.Background(), "key_1", identity); err == nil {
			t.Error("unknown credential identity must not claim durable publication")
		}
		if len(r.revoked) != 0 || !reflect.DeepEqual(r.actions, []string{"publish"}) {
			t.Error("ID-only notification must never create a persistent slot tombstone")
		}
		if r.published != "key_1" {
			t.Error("unknown-ID legacy broadcast attempt must be preserved")
		}
	}
}

func TestRevocationCredentialPublishContract(t *testing.T) {
	for _, phase := range []string{"healthy", "publish_failure", "persistence_failure"} {
		t.Run(phase, func(t *testing.T) {
			b, r := newRevocationFloorFixture()
			hash := strings.Repeat("a", 64) // visibly synthetic SHA-256 fixture
			detail := errors.New("synthetic-private-transport-detail")
			if phase == "publish_failure" {
				r.publishErr = detail
			}
			if phase == "persistence_failure" {
				r.storeErr = detail
			}
			err := b.PublishCredentialWithError(context.Background(), "key_1", hash)
			if phase == "healthy" && err != nil {
				t.Fatal("synthetic healthy publication failed")
			}
			if phase != "healthy" {
				var failure *RevocationPublishError
				if !errors.As(err, &failure) || errors.Unwrap(err) != nil || err.Error() != "revocation not broadcast" {
					t.Error("credential publication failure must remain typed and value-free")
				}
			}
			want := []string{"persist", "publish"}
			if phase == "persistence_failure" {
				want = []string{"persist"}
			}
			if !reflect.DeepEqual(r.actions, want) {
				t.Error("credential persistence must precede delivery")
			}
			if r.revoked[hash] != (phase != "persistence_failure") || r.revoked["key_1"] {
				t.Error("persistent tombstone must identify the credential, never its slot")
			}
			if phase != "persistence_failure" && r.published != "sha256:"+hash {
				t.Error("delayed delivery payload must identify the credential, never its slot")
			}
			if r.wrongKey {
				t.Error("credential tombstones must use the versioned identity namespace")
			}
		})
	}
}
