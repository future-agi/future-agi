// Package catalog embeds the gateway's shipped catalog data: the model
// shutdown calendar, deprecations.json (plan V3). internal/modeldb parses it
// (modeldb.ParseDeprecations) but cannot embed it, because go:embed patterns
// may not leave a package's directory (W2b integration, IC-3, V3 ruling R1).
package catalog

import (
	_ "embed"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
)

//go:embed deprecations.json
var deprecationsJSON []byte

var (
	deprecationsOnce sync.Once
	deprecations     *modeldb.Deprecations
	deprecationsErr  error
)

// Deprecations is the shipped model shutdown calendar, parsed once. It is
// compiled in, so an error means the binary was built from a calendar
// TestEmbeddedDeprecationsParse would have refused.
func Deprecations() (*modeldb.Deprecations, error) {
	deprecationsOnce.Do(func() {
		deprecations, deprecationsErr = modeldb.ParseDeprecations(deprecationsJSON)
	})
	return deprecations, deprecationsErr
}
