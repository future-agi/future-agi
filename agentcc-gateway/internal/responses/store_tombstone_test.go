package responses

import (
	"fmt"
	"testing"
	"time"
)

func TestStore_TombstoneRejectsLatePuts(t *testing.T) {
	for _, readable := range []bool{false, true} {
		for _, put := range []string{"Put", "PutBound"} {
			t.Run(fmt.Sprintf("%s/readable=%t", put, readable), func(t *testing.T) {
				s := NewStore(time.Hour)
				var body []byte
				if readable {
					body = []byte(`{"id":"id","status":"completed"}`)
				}
				s.PutBound("id", "org", "binding", body)
				if !s.CanContinue("id", "org", "binding") || !s.Delete("id", "org") {
					t.Fatal("fixture must authorize continuation and delete successfully")
				}
				for _, lateBody := range [][]byte{nil, []byte(`{"id":"id","status":"completed"}`)} {
					var stored bool
					if put == "Put" {
						stored = s.Put("id", "org", lateBody)
					} else {
						stored = s.PutBound("id", "org", "binding", lateBody)
					}
					if stored {
						t.Error("late publication succeeded after deletion")
					}
					if s.Get("id", "org") != nil || s.CanContinue("id", "org", "binding") {
						t.Error("late publication restored retrieval or continuation authority")
					}
				}
				if s.Delete("id", "org") {
					t.Error("repeated deletion treated a tombstone as a live response")
				}
			})
		}
	}
}

func TestStore_TombstoneIsScopedToExactTenant(t *testing.T) {
	for _, owner := range []string{"org-a", "org-b", ""} {
		t.Run("owner="+owner, func(t *testing.T) {
			s := NewStore(time.Hour)
			for _, org := range []string{"org-a", "org-b", ""} {
				s.PutBound("same-id", org, "binding", []byte("original"))
			}
			if !s.Delete("same-id", owner) {
				t.Fatal("owner could not delete response")
			}
			for _, org := range []string{"org-a", "org-b", ""} {
				want := org != owner
				if got := s.PutBound("same-id", org, "binding", []byte("completed")); got != want {
					t.Errorf("PutBound tenant %q: %t, want %t", org, got, want)
				}
				if got := s.Get("same-id", org); (got != nil) != want {
					t.Errorf("Get tenant %q: %+v, want readable=%t", org, got, want)
				}
				if got := s.CanContinue("same-id", org, "binding"); got != want {
					t.Errorf("CanContinue tenant %q: %t, want %t", org, got, want)
				}
			}
		})
	}
}

func TestStore_TombstoneRetainedForFullTTLAfterDelete(t *testing.T) {
	for _, clean := range []bool{false, true} {
		t.Run(fmt.Sprintf("cleanup=%t", clean), func(t *testing.T) {
			s := NewStore(time.Hour)
			s.PutBound("id", "org", "binding", []byte("original"))
			previous := s.Get("id", "org")
			// No concurrent readers: simulate deletion near the original expiry.
			previous.ExpiresAt = time.Now().Add(time.Second)
			before := time.Now()
			if !s.Delete("id", "org") {
				t.Fatal("delete failed")
			}
			key := storeKey{orgID: "org", id: "id"}
			tombstone := s.responses[key]
			if tombstone == nil || tombstone.ExpiresAt.Before(before.Add(s.ttl)) {
				t.Fatal("deletion must retain a tombstone for a full record TTL")
			}
			if tombstone == previous || string(previous.Body) != "original" {
				t.Error("deletion mutated a snapshot already held by a reader")
			}
			if len(tombstone.Body) != 0 || tombstone.ContinuationBinding != "" {
				t.Error("tombstone retained the deleted body or continuation binding")
			}
			if removed := s.CleanExpired(); removed != 0 {
				t.Errorf("cleanup removed an unexpired tombstone: %d", removed)
			}
			if s.PutBound("id", "org", "binding", []byte("late")) {
				t.Error("cleanup allowed an in-flight publication to restore authority")
			}
			// Expire the tombstone without sleeping. Both cleanup and lazy
			// replacement must allow a new response after the retention window.
			tombstone.ExpiresAt = time.Now().Add(-time.Second)
			if clean {
				if removed := s.CleanExpired(); removed != 1 || s.responses[key] != nil {
					t.Errorf("expired tombstone was not reclaimed: removed=%d", removed)
				}
			}
			if !s.PutBound("id", "org", "new-binding", []byte("new")) ||
				s.Get("id", "org") == nil || !s.CanContinue("id", "org", "new-binding") {
				t.Error("expired tombstone prevented a new response")
			}
		})
	}
}
