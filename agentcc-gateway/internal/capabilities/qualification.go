package capabilities

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"
)

// ErrNotLiveQualification refuses a qualification file that is not from a
// live run: results earned against a mock are not evidence.
var ErrNotLiveQualification = errors.New("qualification file is not a live run (mode must be \"live\"); mock results are not qualification evidence")

// Qualification is an imported live qualification run, the file named by
// capabilities.qualification_file.
type Qualification struct {
	Mode    string                `json:"mode"`
	RanAt   time.Time             `json:"ran_at"`
	Results []QualificationResult `json:"results"`
}

// QualificationResult is one checked feature of one provider on one endpoint.
type QualificationResult struct {
	Provider string `json:"provider"`
	// Endpoint is a capability id, e.g. "chat.completions".
	Endpoint string `json:"endpoint"`
	Feature  string `json:"feature,omitempty"`
	// Status is pass, fail or unqualified; unqualified results are not evidence.
	Status string     `json:"status"`
	RanAt  *time.Time `json:"ran_at,omitempty"`
}

// Verdict is what a qualification run says about one provider on one endpoint.
type Verdict struct {
	Qualified bool
	At        time.Time
}

// LoadQualification reads path. An unset path or a missing file is no
// evidence (nil, nil); a file that is not a live run is ErrNotLiveQualification.
func LoadQualification(path string) (*Qualification, error) {
	if path == "" {
		return nil, nil
	}
	body, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading qualification file: %w", err)
	}
	var q Qualification
	if err := json.Unmarshal(body, &q); err != nil {
		return nil, fmt.Errorf("parsing qualification file %s: %w", path, err)
	}
	if q.Mode != "live" {
		return nil, ErrNotLiveQualification
	}
	return &q, nil
}

// Verdict says whether provider passed on endpoint: qualified when every
// pass or fail result for the pair passed, at the latest result's run time.
// ok is false when the run has no pass or fail result for the pair.
func (q *Qualification) Verdict(provider, endpoint string) (v Verdict, ok bool) {
	if q == nil {
		return Verdict{}, false
	}
	v.Qualified = true
	for _, r := range q.Results {
		if r.Provider != provider || r.Endpoint != endpoint || (r.Status != "pass" && r.Status != "fail") {
			continue
		}
		ok = true
		v.Qualified = v.Qualified && r.Status == "pass"
		at := q.RanAt
		if r.RanAt != nil {
			at = *r.RanAt
		}
		if at.After(v.At) {
			v.At = at
		}
	}
	if !ok {
		return Verdict{}, false
	}
	return v, true
}
