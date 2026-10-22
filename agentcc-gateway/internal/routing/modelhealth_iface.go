package routing

// ModelHealthSnapshot is the recent health of one (provider, model): N calls
// in the window, their success and error rates, latency percentiles and the
// class of the latest error. N is 0 when nothing was recorded.
type ModelHealthSnapshot struct {
	N              int
	SuccessRate    float64
	ErrorRate      float64
	P50Ms          float64
	P95Ms          float64
	LastErrorClass string
}

// ModelHealthReader reports per-model health. ModelHealth implements it; strategies,
// eligibility, outcome learning, auto routing and the catalog read it through
// the gateway's injected dependencies, never by importing the implementation.
type ModelHealthReader interface {
	Snapshot(providerID, model string) ModelHealthSnapshot
}

// NoModelHealth is the ModelHealthReader that has recorded nothing: every
// snapshot is empty. It is the default until a health store is wired.
var NoModelHealth ModelHealthReader = noModelHealth{}

type noModelHealth struct{}

func (noModelHealth) Snapshot(string, string) ModelHealthSnapshot { return ModelHealthSnapshot{} }
