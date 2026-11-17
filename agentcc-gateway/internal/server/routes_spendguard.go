package server

import (
	"context"
	"net/http"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// SpendGuardAdmin is the managed spend guard's admin surface: clearing an
// org's auto-pause.
type SpendGuardAdmin interface {
	Unpause(ctx context.Context, orgID string) (cleared bool, err error)
}

// spendGuards holds each server's spend guard. The route is registered when
// the server is built and the guard exists only once the wiring runs
// (SetSpendGuard), so the handler looks it up per call. Keyed by server so
// servers built side by side (tests) never share one.
var spendGuards sync.Map // *Server -> SpendGuardAdmin

// SetSpendGuard installs the spend guard DELETE /-/orgs/{org_id}/credits/pause
// clears auto-pauses through.
func (s *Server) SetSpendGuard(g SpendGuardAdmin) { spendGuards.Store(s, g) }

func (s *Server) spendGuard() SpendGuardAdmin {
	v, _ := spendGuards.Load(s)
	g, _ := v.(SpendGuardAdmin)
	return g
}

// registerSpendGuardRoutes registers the managed spend guard's admin route.
func (s *Server) registerSpendGuardRoutes(router *Router) {
	router.Handle("DELETE", "/-/orgs/{org_id}/credits/pause", s.managedRoute(s.handleSpendGuardUnpause))
}

// spendGuardPauseView is the unpause response: the org's managed requests
// are no longer auto-paused, and cleared says whether a pause was set. The
// EE budget pause is not the gateway's to lift.
type spendGuardPauseView struct {
	OrgID   string `json:"org_id"`
	Paused  bool   `json:"paused"`
	Cleared bool   `json:"cleared"`
}

// handleSpendGuardUnpause serves DELETE /-/orgs/{org_id}/credits/pause
// (admin): it clears the auto-pause the spend guard set on an anomaly.
func (s *Server) handleSpendGuardUnpause(w http.ResponseWriter, r *http.Request) {
	orgID := s.creditsOrgID(w, r)
	if orgID == "" {
		return
	}
	g := s.spendGuard()
	if g == nil {
		models.WriteError(w, models.ErrNotFound("spend_guard_disabled", "this gateway runs no managed spend guard (it needs Redis)"))
		return
	}
	cleared, err := g.Unpause(r.Context(), orgID)
	if err != nil {
		writeLedgerError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, spendGuardPauseView{OrgID: orgID, Paused: false, Cleared: cleared})
}
