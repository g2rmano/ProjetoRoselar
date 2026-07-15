# Product

## Register

product

## Users

- **Vendedores (salespeople)** — day-to-day core users. Create quotes (orçamentos), run price simulations, track personal goals and follow-ups. Context: at a desk or on the phone with a customer; speed and confidence in the numbers matter more than visual flair.
- **Owner/admin** — monitors team performance, margins, commissions, discounts, and the audit log. Needs to trust every number at a glance.
- Not users: architects and customers only receive generated PDFs; they never log in.

Language: Brazilian Portuguese (pt-BR) throughout. Currency BRL, dates dd/mm/yyyy.

## Product Purpose

Internal sales-operations system for Roselar, a furniture/decor reseller. Manages the full quote-to-order flow: quotes with supplier products, freight, payment tariffs and architect commissions; orders and deliveries; a shared calendar; goals and reports. Success = margin control: the admin trusts that commissions, discounts, and tariffs are computed correctly, and sellers can't accidentally produce an unprofitable quote.

## Brand Personality

Confiável, preciso, sóbrio. Numbers-first, banking-grade trust. The interface should feel like a reliable instrument: calm surfaces, clear hierarchy, no decoration that competes with financial data. Tone of voice: direct, professional pt-BR; empathetic (never blaming) in errors.

## Anti-references

- **Startup SaaS flash** — no gradients-as-decoration, glassmorphism, marketing-style hero metrics, or trendy dashboard chrome.
- **Dense ERP (Excel-like)** — no TOTVS/SAP-style walls of fields and grids. Progressive disclosure over cramming; one clear task per screen.

## Design Principles

1. **Numbers are the interface** — financial values get the strongest hierarchy on every screen; formatting (R$, alignment, tabular figures) is never an afterthought.
2. **Wrong is loud, right is quiet** — margin violations, overdue events, and blocked states must be unmissable; healthy states stay calm.
3. **One task per screen** — quote creation, simulation, and reporting each own their flow; avoid multiplexing unrelated actions.
4. **Trust through consistency** — same term, same color, same placement for the same concept everywhere (Orçamento, Pedido, Meta).
5. **Fast path for the seller** — the most frequent action (new quote) is always one click away; defaults favor the common case.

## Accessibility & Inclusion

WCAG AA baseline: ≥4.5:1 body-text contrast, keyboard-navigable forms and menus, visible focus states, `prefers-reduced-motion` alternatives for all animation. Status must never be conveyed by color alone (pair with icon or text) — relevant for margin/goal indicators.
