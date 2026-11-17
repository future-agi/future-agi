package cost

import "sync/atomic"

// UnpricedManagedAttemptsMetric is the Prometheus counter of managed
// attempts the gateway served on FutureAGI's credit but could not price, so
// did not bill. The prometheus plugin exposes UnpricedManagedAttempts
// under this name.
const UnpricedManagedAttemptsMetric = "agentcc_unpriced_managed_attempts_total"

var unpricedManagedAttempts atomic.Int64

// UnpricedManagedAttempts is how many managed attempts this process could not
// price since it started.
func UnpricedManagedAttempts() int64 { return unpricedManagedAttempts.Load() }

func countUnpricedManaged() { unpricedManagedAttempts.Add(1) }
