package server

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// fakeSpendGuard records the orgs it was asked to unpause.
type fakeSpendGuard struct {
	paused   map[string]bool
	err      error
	unpaused []string
}

func (f *fakeSpendGuard) Unpause(_ context.Context, orgID string) (bool, error) {
	if f.err != nil {
		return false, f.err
	}
	f.unpaused = append(f.unpaused, orgID)
	was := f.paused[orgID]
	delete(f.paused, orgID)
	return was, nil
}

// DELETE /-/orgs/{org_id}/credits/pause clears the spend guard's auto-pause
// for that org only, with the admin token, and says whether one was set.
func TestSpendGuardUnpauseRoute(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	guard := &fakeSpendGuard{paused: map[string]bool{"org-m": true}}
	g.srv.SetSpendGuard(guard)

	w := creditsAdmin(t, g, http.MethodDelete, "/-/orgs/org-m/credits/pause", "", adminToken)
	if w.Code != http.StatusOK {
		t.Fatalf("unpause: %d %s", w.Code, w.Body)
	}
	var view spendGuardPauseView
	if err := json.Unmarshal(w.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if view != (spendGuardPauseView{OrgID: "org-m", Paused: false, Cleared: true}) {
		t.Fatalf("unpause view %+v", view)
	}
	again := creditsAdmin(t, g, http.MethodDelete, "/-/orgs/org-m/credits/pause", "", adminToken)
	if err := json.Unmarshal(again.Body.Bytes(), &view); err != nil || view.Cleared {
		t.Fatalf("second unpause: %s, want cleared false", again.Body)
	}

	if w := creditsAdmin(t, g, http.MethodDelete, "/-/orgs/org-m/credits/pause", "", "-"); w.Code != http.StatusUnauthorized {
		t.Fatalf("without the admin token: %d, want 401", w.Code)
	}
	if len(guard.unpaused) != 2 {
		t.Fatalf("unpause calls %v, want two (the tokenless call reaches nothing)", guard.unpaused)
	}
}

// A gateway that runs no spend guard (no Redis) answers 404
// spend_guard_disabled; a guard whose Redis fails answers 503 like every
// other managed-credit route.
func TestSpendGuardUnpauseWithoutGuardOrRedis(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	w := creditsAdmin(t, g, http.MethodDelete, "/-/orgs/org-m/credits/pause", "", adminToken)
	if w.Code != http.StatusNotFound || !json.Valid(w.Body.Bytes()) {
		t.Fatalf("no guard: %d %s, want 404", w.Code, w.Body)
	}

	g.srv.SetSpendGuard(&fakeSpendGuard{err: errors.Join(billing.ErrUnavailable, errors.New("dial"))})
	w = creditsAdmin(t, g, http.MethodDelete, "/-/orgs/org-m/credits/pause", "", adminToken)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("guard store down: %d %s, want 503", w.Code, w.Body)
	}
}
