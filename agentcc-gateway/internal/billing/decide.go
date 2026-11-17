package billing

// decideReserve is RESERVE's decision for an existing account, after the
// sweep: whether a hold of micros fits. left is granted - spent plus the
// allowed overdraft; held counts every hold, the caller's own included. The
// Redis script makes the same decision in Lua (redisstate/managedcredits.go).
func decideReserve(acct Account, micros, overdraftMicros int64) ReserveStatus {
	left := acct.GrantedMicros - acct.SpentMicros + overdraftMicros
	switch {
	case left <= 0:
		return ReserveExhausted
	case left-acct.HeldMicros >= micros:
		return ReserveOK
	case left >= micros:
		return ReserveInFlight
	default:
		return ReserveInsufficient
	}
}
