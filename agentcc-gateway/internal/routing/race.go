package routing

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// RaceCallFunc is called for each racing provider.
// Returns the response as an interface{} and any error.
type RaceCallFunc func(ctx context.Context, providerID string, modelOverride string) (interface{}, error)

// RaceOutcome describes the result of a provider race.
type RaceOutcome struct {
	WinnerProvider string
	WinnerDuration time.Duration
	Response       interface{}
	ProviderCount  int
	Losers         []raceLossSummary
}

type raceLossSummary struct {
	ProviderID string
	Duration   time.Duration
	Err        error
}

type raceResult struct {
	providerID    string
	modelOverride string
	response      interface{}
	err           error
	duration      time.Duration
}

// RaceExecutor runs the same request against multiple providers concurrently.
type RaceExecutor struct {
	maxConcurrent int
	cancelDelay   time.Duration
	excluded      map[string]bool
}

// NewRaceExecutor creates a race executor from config.
func NewRaceExecutor(maxConcurrent int, cancelDelay time.Duration, excludedProviders []string) *RaceExecutor {
	if cancelDelay == 0 {
		cancelDelay = 50 * time.Millisecond
	}
	excluded := make(map[string]bool, len(excludedProviders))
	for _, p := range excludedProviders {
		excluded[p] = true
	}
	return &RaceExecutor{
		maxConcurrent: maxConcurrent,
		cancelDelay:   cancelDelay,
		excluded:      excluded,
	}
}

// IsEnabled returns true if the executor is configured.
func (re *RaceExecutor) IsEnabled() bool {
	return re != nil
}

// Execute races the call across all targets and returns the first success.
func (re *RaceExecutor) Execute(ctx context.Context, targets []RoutingTarget, call RaceCallFunc) (*RaceOutcome, error) {
	// Filter excluded providers.
	eligible := make([]RoutingTarget, 0, len(targets))
	for _, t := range targets {
		if !re.excluded[t.ProviderID] {
			eligible = append(eligible, t)
		}
	}
	if len(eligible) == 0 {
		return nil, fmt.Errorf("no eligible providers for race after exclusions")
	}

	// Limit to max_concurrent.
	if re.maxConcurrent > 0 && len(eligible) > re.maxConcurrent {
		eligible = eligible[:re.maxConcurrent]
	}

	// Single target — call directly, no race overhead.
	if len(eligible) == 1 {
		start := time.Now()
		resp, err := call(ctx, eligible[0].ProviderID, eligible[0].ModelOverride)
		if err != nil {
			return nil, err
		}
		return &RaceOutcome{
			WinnerProvider: eligible[0].ProviderID,
			WinnerDuration: time.Since(start),
			Response:       resp,
			ProviderCount:  1,
		}, nil
	}

	// Race: fan out to all eligible targets.
	raceCtx, raceCancel := context.WithCancel(ctx)
	resultCh := make(chan raceResult, len(eligible))

	var wg sync.WaitGroup
	for _, t := range eligible {
		wg.Add(1)
		go func(target RoutingTarget) {
			defer wg.Done()
			start := time.Now()
			resp, err := call(raceCtx, target.ProviderID, target.ModelOverride)
			resultCh <- raceResult{
				providerID:    target.ProviderID,
				modelOverride: target.ModelOverride,
				response:      resp,
				err:           err,
				duration:      time.Since(start),
			}
		}(t)
	}

	// Close resultCh when all goroutines finish.
	go func() {
		wg.Wait()
		close(resultCh)
	}()

	var winner *raceResult
	var losers []raceLossSummary
	failures := 0

	for res := range resultCh {
		if res.err != nil {
			failures++
			losers = append(losers, raceLossSummary{
				ProviderID: res.providerID,
				Duration:   res.duration,
				Err:        res.err,
			})
			if failures == len(eligible) {
				raceCancel()
				return nil, res.err
			}
			continue
		}

		if winner == nil {
			// First success wins.
			r := res
			winner = &r
			// Cancel remaining after a short delay for graceful cleanup.
			go func() {
				time.Sleep(re.cancelDelay)
				raceCancel()
			}()
		} else {
			// Losers that succeeded.
			losers = append(losers, raceLossSummary{
				ProviderID: res.providerID,
				Duration:   res.duration,
			})
		}
	}

	if winner == nil {
		raceCancel()
		return nil, fmt.Errorf("all %d racing providers failed", len(eligible))
	}

	return &RaceOutcome{
		WinnerProvider: winner.providerID,
		WinnerDuration: winner.duration,
		Response:       winner.response,
		ProviderCount:  len(eligible),
		Losers:         losers,
	}, nil
}
