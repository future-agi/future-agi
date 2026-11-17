package auth

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
)

type rotationBulkTransport func(*http.Request) (*http.Response, error)

func (f rotationBulkTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	return f(r)
}

func rotationStore() (*KeyStore, *fakeClock) {
	ks := NewKeyStore(config.AuthConfig{}, true)
	clock := &fakeClock{t: time.Unix(100, 0)}
	ks.now = clock.Now
	return ks, clock
}

func rotationKey(id, raw string) SyncedKey {
	return SyncedKey{ID: id, KeyHash: HashKey(raw), Metadata: map[string]string{
		"org_id": "synthetic-org", "user_id": "synthetic-person",
	}}
}

func rotationEnvelope(t *testing.T, keys []SyncedKey, retired []RetiredKey) []byte {
	t.Helper()
	rows := make([]map[string]any, 0, len(keys))
	for _, key := range keys {
		rows = append(rows, map[string]any{
			"id": key.ID, "key_hash": key.KeyHash, "metadata": key.Metadata,
			"expires_at": key.ExpiresAt,
		})
	}
	body, err := json.Marshal(map[string]any{
		"status": true, "result": rows, "retired": retired,
		"snapshot":         KeySnapshot{Complete: true, IssuedEver: 2},
		"config_conflicts": []string{},
	})
	if err != nil {
		t.Fatal("could not encode synthetic bulk envelope")
	}
	return body
}

// Exercise the real HTTP decode, contract conversion, list ordering and negative
// consumer without a listener, DNS, provider access or a fake KeyStore.
func applyRotationEnvelope(t *testing.T, ks *KeyStore, body []byte, inFlight func()) {
	t.Helper()
	previous := keySyncHTTPClient
	defer func() { keySyncHTTPClient = previous }()
	keySyncHTTPClient = &http.Client{Transport: rotationBulkTransport(func(r *http.Request) (*http.Response, error) {
		if r.Method != http.MethodGet || r.URL.Path != "/agentcc/api-keys/bulk/" {
			t.Fatal("unexpected bulk request")
		}
		if inFlight != nil {
			inFlight()
		}
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(bytes.NewReader(body)), Header: make(http.Header)}, nil
	})}
	if err := SyncKeysFromControlPlane(context.Background(), "http://control-plane.invalid", "", ks); err != nil {
		t.Fatal("real bulk consumer rejected the synthetic producer envelope")
	}
}

func assertRotationLive(t *testing.T, ks *KeyStore, key SyncedKey, raw string) {
	t.Helper()
	got := ks.Authenticate(raw)
	if got == nil || got.ID != key.ID || ks.blockedHashes[key.KeyHash] {
		t.Fatal("committed replacement was removed or permanently blocked")
	}
}

func TestCommittedImportDelayedSnapshots(t *testing.T) {
	for _, schedule := range []string{"pending", "pre-command-positive"} {
		t.Run(schedule, func(t *testing.T) {
			ks, clock := rotationStore()
			oldRaw, newRaw := "synthetic-old", "synthetic-replacement"
			old, replacement := rotationKey("old", oldRaw), rotationKey("replacement", newRaw)
			ks.SyncFromHashes([]SyncedKey{old})
			clock.Advance(time.Second)
			var body []byte
			if schedule == "pending" {
				body = rotationEnvelope(t, nil, []RetiredKey{{ID: old.ID, KeyHash: old.KeyHash}})
			} else {
				body = rotationEnvelope(t, []SyncedKey{old}, nil)
			}
			applyRotationEnvelope(t, ks, body, func() {
				clock.Advance(time.Second)
				if ks.LoadFromHashes([]SyncedKey{replacement}) != 1 {
					t.Fatal("committed import not acknowledged")
				}
				if !ks.Revoke(old.ID) {
					t.Fatal("old key revoke did not apply")
				}
				// Response delay may exceed grace; the FETCH predates import.
				clock.Advance(config.DefaultKeySyncGrace + time.Second)
			})
			assertRotationLive(t, ks, replacement, newRaw)
			if ks.Authenticate(oldRaw) != nil {
				t.Fatal("explicitly revoked old key was resurrected")
			}
		})
	}
}

func TestCommittedImportGraceIsBoundedAndNotRenewed(t *testing.T) {
	ks, clock := rotationStore()
	raw := "synthetic-bounded-import"
	key := rotationKey("bounded", raw)
	if ks.LoadFromHashes([]SyncedKey{key}) != 1 {
		t.Fatal("initial import failed")
	}
	minted := ks.Get(key.ID).mintedAt
	clock.Advance(config.DefaultKeySyncGrace - time.Second)
	if ks.LoadFromHashes([]SyncedKey{key}) != 0 || !ks.Get(key.ID).mintedAt.Equal(minted) {
		t.Fatal("duplicate import renewed grace")
	}
	applyRotationEnvelope(t, ks, rotationEnvelope(t, nil, nil), nil)
	assertRotationLive(t, ks, key, raw)
	clock.Advance(time.Second)
	applyRotationEnvelope(t, ks, rotationEnvelope(t, nil, nil), nil)
	if ks.Authenticate(raw) != nil || ks.Get(key.ID) != nil {
		t.Fatal("unlisted import survived the creation grace limit")
	}
}

func TestCommittedImportListedKeyLosesGrace(t *testing.T) {
	ks, clock := rotationStore()
	raw := "synthetic-listed-import"
	key := rotationKey("listed", raw)
	ks.LoadFromHashes([]SyncedKey{key})
	clock.Advance(time.Second)
	applyRotationEnvelope(t, ks, rotationEnvelope(t, []SyncedKey{key}, nil), nil)
	assertRotationLive(t, ks, key, raw)
	if !ks.Get(key.ID).mintedAt.IsZero() {
		t.Fatal("positive list did not end creation grace")
	}
	clock.Advance(time.Second)
	applyRotationEnvelope(t, ks, rotationEnvelope(t, nil, nil), nil)
	if ks.Authenticate(raw) != nil {
		t.Fatal("listed then removed key survived on creation grace")
	}
}

func TestCommittedImportNegativeFactsAlwaysWin(t *testing.T) {
	for _, negative := range []string{"retirement", "explicit-revoke"} {
		t.Run(negative, func(t *testing.T) {
			ks, _ := rotationStore()
			raw := "synthetic-negative-import"
			key := rotationKey("negative", raw)
			ks.LoadFromHashes([]SyncedKey{key})
			if negative == "retirement" {
				applyRotationEnvelope(t, ks, rotationEnvelope(t, nil, []RetiredKey{{ID: key.ID, KeyHash: key.KeyHash}}), nil)
			} else if !ks.Revoke(key.ID) {
				t.Fatal("explicit revoke failed")
			}
			applyRotationEnvelope(t, ks, rotationEnvelope(t, []SyncedKey{key}, nil), nil)
			if ks.Authenticate(raw) != nil || !ks.blockedHashes[key.KeyHash] || ks.LoadFromHashes([]SyncedKey{key}) != 0 {
				t.Fatal("positive list/import overrode an explicit negative fact")
			}
		})
	}
}

// This separate gate MUST run with the Django producer export from the SAME
// integration head. A skip is not a pass for the cross-language matrix row.
func TestCommittedImportDjangoBulkSchedules(t *testing.T) {
	path := os.Getenv("KEY_ROTATION_BULK_CASES")
	if path == "" {
		t.Skip("requires the actual Django producer export; mandatory integration gate")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal("missing Django producer export")
	}
	var fixture struct {
		Schema   string                  `json:"schema"`
		Before   json.RawMessage         `json:"before"`
		Pending  json.RawMessage         `json:"pending"`
		Active   json.RawMessage         `json:"active"`
		Failed   json.RawMessage         `json:"failed"`
		Imported *gatewayadmin.SyncedKey `json:"imported"`
	}
	if json.Unmarshal(data, &fixture) != nil || fixture.Schema != "key-rotation-bulk-schedules-v1" {
		t.Fatal("invalid Django producer export")
	}
	replacement, err := SyncedKeyFromContract(fixture.Imported)
	if err != nil {
		t.Fatal("invalid committed import from Django")
	}
	newRaw, oldRaw := "sk-agentcc-"+strings.Repeat("a1", 24), "sk-agentcc-"+strings.Repeat("b2", 24)
	if replacement.KeyHash != HashKey(newRaw) {
		t.Fatal("Django export is not from the synthetic issuance fixture")
	}
	for _, schedule := range []struct {
		name string
		body []byte
	}{{"pending", fixture.Pending}, {"pre-command-positive", fixture.Before}} {
		t.Run(schedule.name, func(t *testing.T) {
			ks, clock := rotationStore()
			applyRotationEnvelope(t, ks, fixture.Before, nil)
			old := ks.Authenticate(oldRaw)
			if old == nil {
				t.Fatal("Django pre-command list lacks the old key")
			}
			clock.Advance(time.Second)
			applyRotationEnvelope(t, ks, schedule.body, func() {
				clock.Advance(time.Second)
				if ks.LoadFromHashes([]SyncedKey{replacement}) != 1 || !ks.Revoke(old.ID) {
					t.Fatal("committed import or explicit old-key revoke failed")
				}
				clock.Advance(config.DefaultKeySyncGrace + time.Second)
			})
			assertRotationLive(t, ks, replacement, newRaw)
			if ks.Authenticate(oldRaw) != nil {
				t.Fatal("delayed producer payload resurrected the old key")
			}
			applyRotationEnvelope(t, ks, fixture.Active, nil)
			assertRotationLive(t, ks, replacement, newRaw)
			// FAILED must still retire: no global weakening of negative sync.
			applyRotationEnvelope(t, ks, fixture.Failed, nil)
			applyRotationEnvelope(t, ks, fixture.Active, nil)
			if ks.Authenticate(newRaw) != nil || !ks.blockedHashes[replacement.KeyHash] || ks.LoadFromHashes([]SyncedKey{replacement}) != 0 {
				t.Fatal("FAILED identity was promoted by positive sync or reimport")
			}
		})
	}
}
