package auth

import (
	"context"
	"fmt"
	"log/slog"
	"sync/atomic"
	"time"
)

// Simulation routing needs one column on accounts_orgapikey and one on
// tracer_project. Until a check has seen both, sandbox-key requests are not
// routed, and every other request is served as before.
const (
	// columnCheckSeconds is how often the columns are looked for once a
	// check has succeeded.
	columnCheckSeconds = 60
	columnCheckTimeout = 5 * time.Second
)

// columnRetryInterval is the wait after a failed first check; sandbox-key
// requests are refused (retryable) until a check has succeeded. A variable so
// that a test can shorten it.
var columnRetryInterval = 5 * time.Second

type columnState int32

const (
	columnsUnknown columnState = iota // no check has succeeded yet
	columnsPresent
	columnsMissing
)

// routingColumns is the last known state of the two columns, shared by the
// key resolver (which picks its statement by it) and the router.
type routingColumns struct {
	v   atomic.Int32
	log *slog.Logger
}

func newRoutingColumns(log *slog.Logger) *routingColumns {
	return &routingColumns{log: log}
}

func (c *routingColumns) state() columnState {
	if c == nil {
		return columnsUnknown
	}
	return columnState(c.v.Load())
}

// set records a check's answer and reports a change of state.
func (c *routingColumns) set(next columnState) {
	if c == nil {
		return
	}
	prev := columnState(c.v.Swap(int32(next)))
	if prev == next {
		return
	}
	log := c.log
	if log == nil {
		log = slog.Default()
	}
	switch next {
	case columnsMissing:
		log.Error("simulation routing disabled: a required column is missing, requests of sandbox keys are dropped",
			"event", "simulation_routing_disabled", "reason", dropColumnMissing)
	case columnsPresent:
		log.Info("simulation routing columns present")
	}
}

// columnChecker answers whether both routing columns exist.
type columnChecker interface {
	RoutingColumnsPresent(context.Context) (bool, error)
}

// CheckRoutingColumns looks for the columns once. A failed check keeps the
// last known state, so a database blip neither drops nor admits sandbox-key
// requests.
func (a *Authenticator) CheckRoutingColumns(ctx context.Context) {
	if a == nil || a.columns == nil || a.checker == nil {
		return
	}
	ctx, cancel := context.WithTimeout(ctx, columnCheckTimeout)
	defer cancel()
	present, err := a.checker.RoutingColumnsPresent(ctx)
	if err != nil {
		level := slog.LevelDebug
		if a.columns.state() == columnsUnknown {
			level = slog.LevelWarn
		}
		a.logger().Log(ctx, level, "simulation routing column check failed", "err", err)
		return
	}
	if present {
		a.columns.set(columnsPresent)
	} else {
		a.columns.set(columnsMissing)
	}
}

// WatchRoutingColumns checks at start and then every columnCheckSeconds, or
// every few seconds while no check has succeeded yet. Blocks until ctx is
// cancelled.
func (a *Authenticator) WatchRoutingColumns(ctx context.Context) {
	if a == nil || a.columns == nil || a.checker == nil {
		return
	}
	for {
		a.CheckRoutingColumns(ctx)
		wait := time.Duration(columnCheckSeconds) * time.Second
		if a.columns.state() == columnsUnknown {
			wait = columnRetryInterval
		}
		timer := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}

const routingColumnsQuery = `
	SELECT count(*) FROM information_schema.columns
	WHERE table_schema = current_schema()
	  AND column_name = 'simulation_agent_id'
	  AND table_name IN ('accounts_orgapikey', 'tracer_project')`

// RoutingColumnsPresent reports whether both routing columns exist, read on
// the pool that key validation reads from.
func (r *PGResolver) RoutingColumnsPresent(ctx context.Context) (bool, error) {
	var n int
	if err := r.read.QueryRow(ctx, routingColumnsQuery).Scan(&n); err != nil {
		return false, fmt.Errorf("check routing columns: %w", err)
	}
	return n == 2, nil
}
