# ProjetoRoselar

Internal sales-operations system for Roselar Móveis (furniture reseller). Django, server-rendered pt-BR templates in `templates/`, Bootstrap 5 + Flowbite + Manrope.

## Design Context

- **PRODUCT.md** (project root): register `product`, users (vendedores + admin), purpose (margin control), brand personality (confiável, preciso, sóbrio), anti-references, design principles. Read before any UI work.
- **DESIGN.md** (project root): visual system. Canonical palette is **black (#111418) + red (#D32F2F)**; the navy `#0A2640` in existing templates is **legacy** — replace when touching a surface. Key rules: A Regra do Vermelho Único (one red, never decorative), A Regra dos Números (financial values ink-black 700–800 tabular), flat-at-rest elevation.
- `.impeccable/design.json`: machine-readable sidecar for impeccable live mode.
