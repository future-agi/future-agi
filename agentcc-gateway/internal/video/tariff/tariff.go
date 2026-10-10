// Package tariff prices typed video usage. Unknown prices always return
// ErrUnpriced; callers must never interpret an error as a free request.
package tariff

import (
	"errors"
	"fmt"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"math"
)

type Unit = video.Unit

const (
	VideoTokens      = video.VideoTokens
	OutputSeconds    = video.OutputSeconds
	InputSeconds     = video.InputSeconds
	Images           = video.Images
	Credits          = video.Credits
	MultimodalTokens = video.MultimodalTokens
	StorageBytes     = video.StorageBytes
)

var ErrUnpriced = errors.New("video tariff unavailable")

type Rate struct {
	Unit          Unit
	Revision      string
	Currency      string
	USDPerMillion float64
}

// Price rounds upward to microdollars, including fractional token usage.
func (r Rate) Price(quantity float64) (int64, error) {
	if r.Revision == "" || r.USDPerMillion <= 0 || math.IsNaN(r.USDPerMillion) || math.IsInf(r.USDPerMillion, 0) {
		return 0, ErrUnpriced
	}
	cost := quantity * r.USDPerMillion
	if quantity < 0 || math.IsNaN(cost) || math.IsInf(cost, 0) || cost >= math.MaxInt64 {
		return 0, fmt.Errorf("invalid video usage quantity")
	}
	return int64(math.Ceil(cost)), nil
}

type EstimateResult struct {
	video.UsageLine
	Micros int64  `json:"micros"`
	Basis  string `json:"basis"`
}
type Options struct {
	// Operator-asserted minimum: the referenced provider pricing sheet was not
	// recovered. Zero MUST fail closed whenever reference_video is present.
	MinTokensWithVideoInput int64
}
