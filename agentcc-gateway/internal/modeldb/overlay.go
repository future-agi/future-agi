package modeldb

// Overlaid returns a copy of base, filed under id, with ov merged on top the
// way a config.yaml model_database override is (mergeOverride: a nil field
// keeps base's value). A nil base gives an entry made of ov alone. base is
// never modified, so an entry of the shared database can be overlaid per
// request (an org's model_database overrides).
func Overlaid(base *ModelInfo, id string, ov ModelOverride) *ModelInfo {
	var m ModelInfo
	if base != nil {
		m = *base
	}
	m.ID = id
	mergeOverride(&m, ov)
	return &m
}
