// Package servertools embeds the gateway's shipped server-tool fee table,
// fees.json. internal/servertools parses it (ParseFees) but cannot
// embed it, because go:embed patterns may not leave a package's directory;
// the same split as catalog/deprecations.go.
package servertools

import (
	_ "embed"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/servertools"
)

//go:embed fees.json
var feesJSON []byte

// Fees is the shipped fee table, parsed once. It is compiled in, so an error
// means the binary was built from a table TestShippedFeeTable would have
// refused.
var Fees = sync.OnceValues(func() (servertools.FeeTable, error) {
	return servertools.ParseFees(feesJSON)
})
