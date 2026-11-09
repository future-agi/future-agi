package inclusive

import (
	"context"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// GenerationClient has no default/live transport. The integration owner supplies
// the authenticated OpenRouter generation lookup; tests use synthetic clients.
type GenerationClient interface {
	Generation(context.Context, string) (Generation, error)
}
type Generation struct{ ID, Model, CostUSD string }

// Correction is immutable once prepared. A changed later upstream answer cannot
// race a previously prepared refund. The store is durable, keyed (org, request),
// and returns the existing correction on replay; it must reject mismatched spend
// identity/amount. Prepare atomically quarantines every Anomalies listing.
type Correction struct {
	OrgID, RequestID                                         string
	SpentMicros, ActualMicros, RefundMicros, ShortfallMicros int64
	Anomalies                                                []string
}
type ReconciliationStore interface {
	Prepared(context.Context, string, string) (*Correction, error)
	Prepare(context.Context, Correction) (Correction, error)
	Finish(context.Context, Correction) error
}

// Refund is an adapter to BL-LEDGER's existing
// refund(org, request_id, micros, reason=..., actor=...). It MUST use its original
// request ID (refund:<request_id>), never a generated ID or a second spend.
// A pre-existing refund with another amount must be reported as a conflict, not
// counted as this correction. The ledger owns funding provenance and events.
type Refund interface {
	Refund(context.Context, string, string, int64, string) error
}

// Reconcile waits until EVERY estimated attempt has a validated inclusive cost,
// then prepares one immutable correction and refunds the aggregate difference.
// Any missing identity or invalid lookup remains pending forever (no deadline
// waiver). Prepared work survives an ambiguous refund acknowledgement and can be
// replayed concurrently, beyond the gateway's seven-day legacy marker.
func Reconcile(ctx context.Context, rec billing.Settlement, client GenerationClient, store ReconciliationStore, ledger Refund) (Correction, error) {
	if rec.Kind != billing.KindSpend || rec.Dimensional == nil || len(rec.Dimensional.InclusiveAttempts) == 0 || client == nil || store == nil || ledger == nil {
		return Correction{}, fmt.Errorf("incomplete inclusive reconciliation context")
	}
	c, err := store.Prepared(ctx, rec.OrgID, rec.RequestID)
	if err != nil {
		return Correction{}, err
	}
	if c != nil && c.SpentMicros != rec.BilledMicros {
		return Correction{}, fmt.Errorf("conflicting original spend")
	}
	if c == nil {
		next := Correction{OrgID: rec.OrgID, RequestID: rec.RequestID, SpentMicros: rec.BilledMicros}
		var original int64
		for _, a := range rec.Dimensional.InclusiveAttempts {
			if a.BilledMicros < 0 || a.BilledMicros > 1<<56 {
				return Correction{}, fmt.Errorf("invalid attempt amount")
			}
			original += a.BilledMicros
			actual := a.BilledMicros
			if a.Estimated {
				if a.GenerationID == "" {
					return Correction{}, fmt.Errorf("generation identity missing: reconciliation remains pending")
				}
				a.Identity = rec.InclusiveBindings[a.AttemptID]
				var g Generation
				var err error
				if scoped, ok := client.(interface {
					GenerationFor(context.Context, string, models.InclusiveAttempt) (Generation, error)
				}); ok {
					g, err = scoped.GenerationFor(ctx, rec.OrgID, a)
				} else {
					g, err = client.Generation(ctx, a.GenerationID)
				}
				if err != nil {
					return Correction{}, err
				}
				if !SnapshotContains(a.Identity.SnapshotModels, g.Model) {
					return Correction{}, fmt.Errorf("served model outside admission snapshot; review required")
				}
				if g.ID != a.GenerationID || g.Model == "" || (a.ServedModel != "" && g.Model != a.ServedModel) {
					return Correction{}, fmt.Errorf("generation lookup identity mismatch")
				}
				_, actual, err = Charge(g.CostUSD)
				if err != nil || actual > 1<<56 {
					return Correction{}, fmt.Errorf("invalid generation cost")
				}
				if actual > a.HoldMicros {
					next.Anomalies = append(next.Anomalies, a.Model)
				}
			}
			next.ActualMicros += actual
		}
		// Other normal managed attempts in this same request remain unchanged.
		if original > rec.BilledMicros {
			return Correction{}, fmt.Errorf("attempt sum exceeds original spend")
		}
		next.ActualMicros += rec.BilledMicros - original
		next.RefundMicros = max(int64(0), next.SpentMicros-next.ActualMicros)
		next.ShortfallMicros = max(int64(0), next.ActualMicros-next.SpentMicros)
		prepared, err := store.Prepare(ctx, next)
		if err != nil {
			return Correction{}, err
		}
		c = &prepared
	}
	if c.RefundMicros > 0 {
		if err := ledger.Refund(ctx, c.OrgID, c.RequestID, c.RefundMicros, "inclusive provider cost reconciliation"); err != nil {
			return *c, err
		}
	}
	// A late upward correction cannot mutate an immutable BL-LEDGER spend.
	// Record shortfall + quarantine instead; never create an extra child spend.
	return *c, store.Finish(ctx, *c)
}

// SpendConsumer is the pinned BL-LEDGER adapter:
// settle_spend(record, workspace_id=<request-log workspace or None>).
// BL-LEDGER alone emits its atomic already-rated UsageEventLog and funding split.
type SpendConsumer interface {
	SettleSpend(context.Context, map[string]string, *string) error
}
type PendingSink interface {
	Save(context.Context, billing.Settlement) error
}

// Consume persists the v2 evidence BEFORE projecting one v1-compatible financial
// spend under the original request ID. Save is durable/idempotent and reconciliation
// must wait for that spend to exist (the refund path refuses an absent spend).
// No snapshot hash is passed as price_catalog_version: BL-LEDGER resolves the
// actual active catalogue, or null. All original audit/key/agent/timestamp fields
// are preserved, and no second financial usage event is emitted by this drain.
func Consume(ctx context.Context, rec billing.Settlement, workspaceID *string, pending PendingSink, ledger SpendConsumer) error {
	if rec.Kind != billing.KindSpend {
		return nil
	}
	if pending == nil || ledger == nil {
		return fmt.Errorf("inclusive consumer unavailable")
	}
	if err := pending.Save(ctx, rec); err != nil {
		return err
	}
	fields := rec.FieldMap()
	delete(fields, "dimensions")
	fields["v"] = "1"
	return ledger.SettleSpend(ctx, fields, workspaceID)
}
