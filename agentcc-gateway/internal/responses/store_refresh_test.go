package responses

import (
	"context"
	"testing"
	"time"
)

func TestStore_RefreshKeepsExpiryAndOrgScope(t *testing.T) {
	s := NewStore(time.Hour)
	refresh := func(context.Context) ([]byte, error) { return []byte("completed"), nil }
	s.PutWithRefresh("same-id", "org-a", "binding-a", []byte("queued-a"), refresh)
	s.PutWithRefresh("same-id", "org-b", "binding-b", []byte("queued-b"), refresh)
	a := s.Get("same-id", "org-a")
	updated := s.Update(a, []byte("completed-a"), false)
	if updated == nil || string(updated.Body) != "completed-a" || updated.Refresh != nil {
		t.Fatalf("completed snapshot: %+v", updated)
	}
	if updated.CreatedAt != a.CreatedAt || updated.ExpiresAt != a.ExpiresAt {
		t.Error("refresh extended the response lifetime")
	}
	if string(a.Body) != "queued-a" || a.Refresh == nil {
		t.Error("refresh mutated a snapshot held by a concurrent reader")
	}
	b := s.Get("same-id", "org-b")
	if b == nil || string(b.Body) != "queued-b" || b.Refresh == nil {
		t.Fatalf("another org's snapshot changed: %+v", b)
	}
	if updated.ContinuationBinding != "binding-a" || !s.CanContinue("same-id", "org-a", "binding-a") ||
		s.CanContinue("same-id", "org-a", "binding-b") || s.CanContinue("same-id", "org-b", "binding-a") ||
		s.CanContinue("same-id", "", "binding-a") || s.CanContinue("same-id", "org-a", "") {
		t.Error("refresh changed the continuation authority")
	}
}

func TestStore_StaleRefreshCannotOverwriteNewerSnapshot(t *testing.T) {
	s := NewStore(time.Hour)
	refresh := func(context.Context) ([]byte, error) { return nil, nil }
	s.PutWithRefresh("id", "org", "binding", []byte("queued"), refresh)
	initial := s.Get("id", "org")
	completed := s.Update(initial, []byte("completed"), false)
	if got := s.Update(initial, []byte("in_progress"), true); got != completed || got.Refresh != nil {
		t.Errorf("late pending poll overwrote completion: %+v", got)
	}
	// A new POST under the same ID also supersedes an earlier poll.
	s.PutBound("id", "org", "replacement", []byte("new response"))
	if got := s.Update(completed, []byte("old response"), false); got == nil || string(got.Body) != "new response" || got.ContinuationBinding != "replacement" {
		t.Errorf("late poll overwrote a replacement: %+v", got)
	}
	if s.CanContinue("id", "org", "binding") || !s.CanContinue("id", "org", "replacement") {
		t.Error("late poll restored the old binding")
	}
	// A replacement streaming ID must remain unreadable until output checks pass.
	s.PutBound("id", "org", "stream-binding", nil)
	if got := s.Update(completed, []byte("old response"), false); got == nil || len(got.Body) != 0 || s.Get("id", "org") != nil {
		t.Error("late poll exposed an unchecked streaming response")
	}
}

func TestStore_RefreshCannotReviveDeletedOrExpiredResponse(t *testing.T) {
	for _, reason := range []string{"deleted", "expired"} {
		t.Run(reason, func(t *testing.T) {
			s := NewStore(time.Hour)
			s.PutWithRefresh("id", "org", "binding", []byte("queued"), func(context.Context) ([]byte, error) { return nil, nil })
			initial := s.Get("id", "org")
			if reason == "deleted" {
				s.Delete("id", "org")
			} else {
				// No readers are running; expire the fixture without a sleep.
				initial.ExpiresAt = time.Now().Add(-time.Second)
			}
			if got := s.Update(initial, []byte("completed"), false); got != nil {
				t.Fatalf("refresh revived a %s response: %+v", reason, got)
			}
			if got := s.Get("id", "org"); got != nil {
				t.Fatalf("%s response is retrievable: %+v", reason, got)
			}
			if s.CanContinue("id", "org", "binding") {
				t.Errorf("refresh revived %s continuation authority", reason)
			}
		})
	}
}

func TestStore_RefreshDoesNotAuthorizeUnboundResponse(t *testing.T) {
	s := NewStore(time.Hour)
	s.PutWithRefresh("id", "org", "", []byte("queued"), func(context.Context) ([]byte, error) { return nil, nil })
	s.Update(s.Get("id", "org"), []byte("completed"), false)
	if s.CanContinue("id", "org", "") || s.CanContinue("id", "org", "binding") {
		t.Error("unbound refresh authorized a continuation")
	}
}
