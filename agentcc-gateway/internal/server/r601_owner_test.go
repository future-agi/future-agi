package server

import "github.com/futureagi/agentcc-gateway/internal/billing"

// The HTTP family matrix now exercises the real memory ownership implementation,
// not a mock with century-long holds. Redis durability is tested separately.
type r601Owner = billing.MemoryLedger

func newR601Owner() *r601Owner { return billing.NewMemoryLedger() }
