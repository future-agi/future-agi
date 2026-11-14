package observedcatalog

import (
	"log/slog"
	"time"
)

// LogHandoffGap identifies a conservative repair range after a source-committed
// batch fails catalog handoff. Some observations may already be durable; repair
// may safely overlap them. Never include attribute names, values or span bodies.
// These diagnostics are not a durable journal or an automatic backfill trigger.
func LogHandoffGap(log *slog.Logger, spans []ScopedSpan, err error) {
	if err == nil {
		return
	}
	type window struct {
		scope       Scope
		first, last string
		count       int
	}
	byScope := make(map[Scope]int)
	windows := []window{}
	unresolved := 0
	for _, span := range spans {
		project, _ := span.Row["project_id"].(string)
		scope := Scope{OrganizationID: span.OrganizationID, WorkspaceID: span.WorkspaceID, ProjectID: project}
		org, orgIsString := span.Row["org_id"].(string)
		seen, _ := span.Row["start_time"].(string)
		at, timeErr := time.Parse(TimeLayout, seen)
		if validateScope(scope) != nil || span.ScopeError != "" ||
			(span.Row["org_id"] != nil && (!orgIsString || (org != "" && org != scope.OrganizationID))) ||
			timeErr != nil || at.Format(TimeLayout) != seen {
			// Do not advertise unverified ownership or arbitrary input as a
			// runnable repair scope. The summary requires investigation instead.
			unresolved++
			continue
		}
		i, exists := byScope[scope]
		if !exists {
			i = len(windows)
			byScope[scope] = i
			windows = append(windows, window{scope: scope, first: seen, last: seen})
		}
		w := &windows[i]
		w.first, w.last = min(w.first, seen), max(w.last, seen)
		w.count++
	}
	log.Warn("observed catalog enqueue failed; backfill repair required",
		"event", "observed_catalog_handoff_gap", "err", err,
		"source_spans", len(spans), "repair_scopes", len(windows), "unresolved_spans", unresolved)
	// One bounded record per scope, without a tenant cap or a potentially huge
	// single log field. Keep first-appearance order to make retries comparable.
	for _, w := range windows {
		log.Warn("observed catalog repair scope",
			"event", "observed_catalog_repair_scope",
			"organization_id", w.scope.OrganizationID, "workspace_id", w.scope.WorkspaceID,
			"project_id", w.scope.ProjectID, "source_spans", w.count,
			"source_first_seen", w.first, "source_last_seen", w.last,
			"time_bounds", "inclusive", "timezone", "UTC")
	}
}
