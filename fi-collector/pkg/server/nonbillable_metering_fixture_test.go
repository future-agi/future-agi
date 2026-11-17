package server

import (
	"context"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
)

// NoopMetering is an explicit nonbillable test fixture, not a production mode.
// Keeping it in a _test file prevents bootstrap from using it when the budget
// pause store is missing. Existing ingestion fixtures keep their assertions.
type NoopMetering struct{}

func (NoopMetering) CheckUsage(_ context.Context, _, _ string, _ int64) auth.CheckResult {
	return auth.CheckResult{Allowed: true}
}
