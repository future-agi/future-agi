package responses

import (
	"context"
	"testing"
	"time"
)

func TestStore_TombstoneRejectsLateRefreshAndUpdate(t *testing.T) {
	s := NewStore(time.Hour)
	refresh := func(context.Context) ([]byte, error) { return []byte("completed"), nil }
	s.PutWithRefresh("id", "org", "binding", []byte("queued"), refresh)
	previous := s.Get("id", "org")
	if previous == nil || !s.Delete("id", "org") {
		t.Fatal("fixture must be readable and deletable")
	}
	for _, pending := range []bool{true, false} {
		if s.PutWithRefresh("id", "org", "binding", []byte("late"), refresh) {
			t.Error("late background publication succeeded after deletion")
		}
		if got := s.Update(previous, []byte("late"), pending); got != nil {
			t.Errorf("stale refresh returned a snapshot after deletion: %+v", got)
		}
		if s.Get("id", "org") != nil || s.CanContinue("id", "org", "binding") {
			t.Error("background refresh restored deleted retrieval or authority")
		}
	}
	if tombstone := s.responses[storeKey{orgID: "org", id: "id"}]; tombstone == nil || tombstone.Refresh != nil {
		t.Error("tombstone must release its provider refresh reference")
	}
	if string(previous.Body) != "queued" || previous.Refresh == nil {
		t.Error("delete or late refresh mutated the original snapshot")
	}
}
