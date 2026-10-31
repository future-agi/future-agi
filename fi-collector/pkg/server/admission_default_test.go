package server

import "testing"

// The default is no longer unbounded row admission: resolve and expose the
// current byte limits even when the operator supplies no config.
func TestDefaultAdmissionReportsByteBudget(t *testing.T) {
	s := New(Config{}, nil, nil, nil, nil)
	l := s.Limits()
	if l.AdmissionModel != "byte-leases-v1" || l.IngestBudgetBytes <= 0 || l.WorkPoolBytes <= 0 || l.WirePoolBytes <= 0 || l.MaxPendingRows != 0 || l.BatchMaxRows != 5000 {
		t.Fatalf("limits: %+v", l)
	}
	if l.WorkPoolBytes+l.WirePoolBytes != l.IngestBudgetBytes || l.GRPCMaxRecvBytes != s.limits.maxRecv || l.MaxHTTPBodyBytes != int(s.limits.maxBody) {
		t.Fatalf("limits do not match leased pipeline: %+v", l)
	}
}
