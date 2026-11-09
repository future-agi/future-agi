// Package tools embeds the shipped tool price cards (tool-price-card-v1,
// plan 6.37), one file per capability and vendor at
// <capability>/<vendor>.json. internal/toolcatalog loads and validates them;
// an operator's tools.catalog_dir replaces them.
//
// Seeded by V4 (W2c) from GATEWAY-EXPANSION sections 3 and 4 and the
// verified research it cites (prices and terms read 2026-10-15). Every card
// starts with an empty resale_terms_ref: no partner agreement is signed, so
// no third-party vendor is managed_partner yet. A card whose price the
// research did not capture carries owner_price_pending and $0 SKUs, so no
// managed call can be admitted at it. Each later V lane owns its own
// <capability>/ subdirectory (VT20).
package tools

import "embed"

// Cards holds every seed card.
//
//go:embed */*.json
var Cards embed.FS
