# Changelog

All notable changes to **factum-fic** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-08-17

### Added

- **Deterministic SDI XML parser** (`parse_sdi_xml`) — zero LLM calls, zero tokens, zero hallucinations.
  Parses CedentePrestatore, DatiGeneraliDocumento, DatiRiepilogo nativamente.
  Bypasses Factum Parse API entirely for `.xml` files.
- **Reverse charge detection** — distinguishes real reverse charge (N6.x codes / foreign supplier with
  zero VAT) from exempt/flat-rate regimes (N2.2, N4) that also have VAT=0 but are NOT reverse charge.
- **Nature tag audit trail** — `raw["nature"]` list in parser output for traceability.
- **Heuristic regex fallback for amounts** (`_fallback_amount_from_text`) — extracts totals from raw
  PDF text when Factum API returns null amounts. Multi-lingual patterns (EN/DE/IT).
- **Anti-zero circuit breaker** — blocks expense creation on FIC when `amount_net <= 0`.
- **Currency conversion via BCE** (`convert_currency`) — Frankfurter API with 6-hour in-memory cache.
- **Attachment upload to FIC** — PDF/PNG/JPG attached to expense in a single API call.

### Changed

- **Breaking:** `is_reverse_charge` no longer equals `vat_amount == 0.0`. Now uses N6.x codes +
  foreign supplier logic. VAT=0 is necessary but not sufficient.
- **Pipeline:** `.xml` files bypass Factum API entirely (local parsing only).
- **Archiver:** tree hierarchy `archiviate/YYYY/MM/` with collision-resilient naming.
- **Queue:** dual-ID tracking (`fic_expense_id` + `fic_self_invoice_id`) with automatic v1→v2 migration.

### Fixed

- Deduplication retry: `search_document` pre-check prevents double-creation on timeout/retry.
- FIC entity endpoints: correct `/entities/suppliers` sub-path.
- TD17 payload: exact amounts in `items_list` and `received_documents`.
- Company info payload: correct header propagation in `verify` command.

## [0.1.0] — 2026-08-16

### Added

- Initial project scaffolding: CLI, config, Factum client, FIC client.
- End-to-end pipeline: file → SHA-256 → text extraction → Factum Parse → mapper → FIC registration.
- SDI self-invoice generation: TD17 (extra-UE), TD18 (intra-UE with VAT), TD19 (intra-UE without VAT).
- Watcher daemon: watchdog-based hotfolder monitoring.
- SQLite queue: SHA-256 deduplication, retry tracking, status dashboard.
- Interactive CLI setup wizard: bilingual (IT/EN) with API key validation.
- Zero-data-retention policy: Factum Parse receives text only, never stores invoices.
- Local text extraction via `pypdf` before sending to Factum Parse.
- Items list with `tax_deductibility=100`, `vat_deductibility=0` for Regime Forfettario.