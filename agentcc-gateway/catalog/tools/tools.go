// Package tools embeds the shipped tool price cards (tool-price-card-v1),
// one file per capability and vendor at
// <capability>/<vendor>.json. internal/toolcatalog loads and validates them;
// an operator's tools.catalog_dir replaces them.
//
// Prices and terms are as each vendor published them on 2026-10-15. Every
// card has an empty resale_terms_ref: no partner agreement is signed, so no
// third-party vendor is managed_partner. A card whose price is not confirmed
// carries owner_price_pending and $0 SKUs, so no managed call is admitted at
// it. Each capability's cards live in its own <capability>/ subdirectory.
package tools

import "embed"

// Cards holds every seed card.
//
//go:embed */*.json
var Cards embed.FS
