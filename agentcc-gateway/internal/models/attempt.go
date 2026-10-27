package models

// ProviderAttempt is a body-free record of observed provider usage. Missing
// usage is explicit: a canceled upstream can consume tokens without reporting
// them, and those tokens must not be represented as a measured zero.
type ProviderAttempt struct {
	Provider     string `json:"provider"`
	Model        string `json:"model"`
	Usage        *Usage `json:"usage,omitempty"`
	Cost         string `json:"cost,omitempty"`
	Credits      string `json:"credits,omitempty"`
	UsageMissing bool   `json:"usage_missing,omitempty"`
}

// ResponseUsage returns measured usage even when the response body was rejected.
// Only final observers use it; accounting plugins settle individual candidates.
func (rc *RequestContext) ResponseUsage() *Usage {
	if rc.AccountedUsage != nil {
		return rc.AccountedUsage
	}
	if rc.Response != nil {
		return rc.Response.Usage
	}
	return nil
}

// AdmissionError prevents transport failover from retrying a local quota denial.
type AdmissionError struct{ Err error }

func (e *AdmissionError) Error() string            { return e.Err.Error() }
func (e *AdmissionError) Unwrap() error            { return e.Err }
func (e *AdmissionError) IsAdmissionFailure() bool { return true }

// AttemptLedger is request-local accounting state. It is not copied into trace
// snapshots; only its sanitized totals are published by the pipeline.
type AttemptLedger struct {
	Attempts []ProviderAttempt
	Started  int
	Open     bool
	Denied   error
}
