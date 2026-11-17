package redisstate

// Consumer integration tests use the original helpers without making the
// internal test package import a plugin that depends on redisstate.
var (
	ManagedTestStoreForConsumer = managedTestStore
	MustGrantForConsumer        = mustGrant
	MustAccountForConsumer      = mustAccount
	SpendRecordForConsumer      = spendRecord
	FixedFeePricesForConsumer   = fixedFeePrices
	ManagedRequestForConsumer   = managedRequest
	OutboxBilledForConsumer     = outboxBilled
	SchemaFieldsForConsumer     = schemaFields
	FieldNamesForConsumer       = fieldNames
	BackgroundForConsumer       = bg
)

// ManagedKeysForConsumer exposes only the existing keys observed by the
// external integration tests; key construction stays in ManagedCreditStore.
func ManagedKeysForConsumer(s *ManagedCreditStore, org string) struct {
	Holds, HoldExpiry, Outbox string
} {
	k := s.keys(org)
	return struct {
		Holds, HoldExpiry, Outbox string
	}{Holds: k.holds, HoldExpiry: k.holdexp, Outbox: k.outbox}
}
