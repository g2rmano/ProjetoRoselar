---
name: Roselar Móveis — Sistema Interno
description: Precision instrument for quote-to-order sales ops — black chrome, one red voice, loud numbers.
colors:
  primary: "#D32F2F"
  ink: "#111418"
  navy-legacy: "#0A2640"
  bg: "#F4F6F9"
  surface: "#FFFFFF"
  success: "#198754"
  success-deep: "#146C43"
  info: "#0D6EFD"
  warning: "#E67E22"
  muted: "#6C757D"
  border: "#DEE2E6"
typography:
  display:
    fontFamily: "Manrope, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 800
    lineHeight: 1.2
  headline:
    fontFamily: "Manrope, sans-serif"
    fontSize: "1.1rem"
    fontWeight: 700
    lineHeight: 1.3
  title:
    fontFamily: "Manrope, sans-serif"
    fontSize: "0.88rem"
    fontWeight: 700
    lineHeight: 1.4
  body:
    fontFamily: "Manrope, sans-serif"
    fontSize: "0.86rem"
    fontWeight: 500
    lineHeight: 1.5
  label:
    fontFamily: "Manrope, sans-serif"
    fontSize: "0.72rem"
    fontWeight: 700
    letterSpacing: "0.04em"
rounded:
  sm: "8px"
  md: "10px"
  pill: "2rem"
spacing:
  xs: "0.3rem"
  sm: "0.6rem"
  md: "1.1rem"
  lg: "1.5rem"
components:
  button-primary:
    backgroundColor: "{colors.success}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "0.55rem 1.2rem"
  button-primary-hover:
    backgroundColor: "{colors.success-deep}"
    textColor: "{colors.surface}"
  button-dark:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "0.55rem 1.2rem"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0.55rem 1.2rem"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0.5rem 0.9rem"
---

# Design System: Roselar Móveis — Sistema Interno

## 1. Overview

**Creative North Star: "O Instrumento de Precisão"**

This is the internal sales-operations system of Roselar Móveis, a furniture reseller. The interface is a precision instrument: calm, near-monochrome chrome that stays out of the way so financial numbers carry the screen. Personality is *confiável, preciso, sóbrio* — banking-grade trust rendered in the brand's own materials: black wordmark chrome, white working surfaces, and a single red voice reserved for what demands attention.

The system explicitly rejects **startup SaaS flash** (no gradients-as-decoration, no glassmorphism, no marketing-dashboard chrome) and the **dense ERP wall** (no TOTVS/SAP-style grids of fields; progressive disclosure, one task per screen). Density is moderate: compact enough for sellers moving fast, never cramped.

**Canonical palette note.** The brand is **black + red** (the wordmark is black; red is the brand accent). The current codebase still carries a legacy navy (`#0A2640`) as chrome color; treat it as **legacy to migrate away from**. All new surfaces use Preto Roselar chrome and Vermelho Roselar accent.

**Key Characteristics:**
- Black chrome, white surfaces, one red voice
- Numbers get the strongest hierarchy on every screen
- Flat at rest; shadows respond only to interaction
- Restrained color strategy: accent ≤10% of any screen
- pt-BR voice: direct, professional, never blaming

## 2. Colors

A restrained monochrome instrument with one red voice and quiet functional signals.

### Primary
- **Vermelho Roselar** (#D32F2F): The single brand accent and the alarm channel, unified. Used for what must not be missed: margin violations, overdue events, destructive actions, blocked states, and rare brand moments (active nav marker, notification badge). At 4.9:1 on white it is AA-safe for text.

### Neutral
- **Preto Roselar** (#111418): Chrome and ink. Navbars, section headers, primary text, financial values. The wordmark's black carried into the interface.
- **Névoa** (#F4F6F9): Body background. Cool near-white; surfaces sit on it as white cards.
- **Branco** (#FFFFFF): Working surfaces — cards, tables, inputs.
- **Cinza Apoio** (#6C757D): Secondary text and metadata. Use on white surfaces only (4.7:1); on the tinted body background it drops below AA — put muted text inside cards.
- **Traço** (#DEE2E6): Borders, dividers, input strokes.
- **Navy Legado** (#0A2640): The old chrome color, still present across templates. Do not use in new work; replace with Preto Roselar when touching a surface.

### Tertiary (functional signals)
- **Verde Confirmado** (#198754, hover #146C43): Money-positive: sales, goals hit, primary confirm actions ("Novo Orçamento").
- **Azul Informativo** (#0D6EFD): Informational states and links inside data (converted status, info alerts).
- **Laranja Atenção** (#E67E22): Warnings that are not yet violations (approaching margin limit, pending too long).

### Named Rules
**A Regra do Vermelho Único.** One red. Vermelho Roselar means "attention required" — margin violations, overdue, destructive. Never use red decoratively, and never introduce a second red (retire Bootstrap's `#dc3545` when touching a surface). Its rarity is what makes it loud.

**A Regra dos Números.** Financial values are always Preto Roselar at 700–800 weight with tabular figures. Color on a number is a signal (green = healthy, red = violation), never decoration.

## 3. Typography

**Display/Body Font:** Manrope (with system sans-serif fallback)

**Character:** A single geometric-humanist family carrying the whole system through weight contrast — light chrome text against heavy numbers. Sober and instrument-like; no second family needed.

### Hierarchy
- **Display** (800, 1.5rem, 1.2): Metric values, page-level financial figures. Always with `font-variant-numeric: tabular-nums` for columns of money.
- **Headline** (700, 1.1rem, 1.3): Page titles ("Dashboard", "Lista de Orçamentos").
- **Title** (700, 0.88rem, 1.4): Card and section headers, row primary text (quote number, customer).
- **Body** (500, 0.86rem, 1.5): Form values, table cells, descriptions. Cap prose at 65–75ch.
- **Label** (700, 0.72rem, +0.04em, UPPERCASE): Field labels, metric labels, status badges. 0.72rem is the floor — never smaller (mobile floor is enforced in user.css).

### Named Rules
**A Regra do Peso.** Hierarchy comes from weight (500 → 700 → 800), not from adding sizes. The scale has five steps; do not invent intermediate sizes.

## 4. Elevation

Flat by default with tonal layering: the cool body background (#F4F6F9) recedes and white surfaces read as the working plane. Shadows exist only as a response to state — a whisper at rest, a lift on hover. Depth never decorates.

### Shadow Vocabulary
- **Repouso** (`box-shadow: 0 1px 4px rgba(0,0,0,.06)`): Resting cards and rows. Barely perceptible; separation comes from the border (1px rgba(0,0,0,.04)) and background contrast.
- **Foco** (`box-shadow: 0 4px 14px rgba(0,0,0,.09)` + `translateY(-1px)`): Hover on interactive cards, rows, and buttons.
- **Anel de Foco** (`box-shadow: 0 0 0 3px` accent at 10% alpha): Focused inputs, paired with an ink border.

### Named Rules
**A Regra do Plano.** Surfaces are flat at rest. If a shadow is visible while nothing is being pointed at, it is too strong.

## 5. Components

Feel: **firme e discreto** — bold weights, quiet surfaces. Components assert through typography, not chrome.

### Buttons
- **Shape:** Softly rounded (8px), inline-flex with 0.35rem icon gap.
- **Primary (confirm/money):** Verde Confirmado background, white 700-weight text, `0.55rem 1.2rem` padding. Hover deepens to #146C43.
- **Dark (navigate/neutral):** Preto Roselar background, white text. Hover lightens one step.
- **Secondary:** White background, ink text, 1.5px Traço border. Hover fills with Névoa.
- **Destructive:** Vermelho Roselar background, white text. Reserved for irreversible actions; always paired with a consequence-stating confirm.
- **Hover (all):** `translateY(-1px)` + Foco shadow, 0.2s ease-out.

### Status Badges
- **Style:** Pill (2rem radius), 0.7rem 700-weight uppercase with +0.04em tracking, tinted background with deep-toned text of the same hue (e.g. approved: #E8F5E9 bg / #0F5132 text). Never white-on-saturated.

### Cards / Containers
- **Corner Style:** 10px radius.
- **Background:** White on Névoa body; header strip #FAFBFC with bottom border.
- **Shadow Strategy:** Repouso at rest, Foco on hover (interactive cards only).
- **Border:** 1px rgba(0,0,0,.04) hairline.
- **Internal Padding:** 1.1rem body; 0.7rem 1.1rem header.

### Inputs / Fields
- **Style:** 1.5px Traço border, 8px radius, white background, ink text at 500 weight, `0.5rem 0.9rem` padding. Selects use an inline SVG chevron, appearance:none.
- **Focus:** Border shifts to ink + Anel de Foco ring. No outline removal without replacement.
- **Labels:** Above the field, Label style (0.72rem uppercase 700).

### Navigation
- **Style:** Fixed-top dark navbar on Preto Roselar (legacy surfaces still navy), white links, active page marked. Logo left, user dropdown right (white surface, divided sections, red logout row).
- **Mobile:** Collapse panel keeps the solid dark background (race-condition guard in user.css).

### Alerts (signature)
- **Style:** 8px radius, tinted background + 1px same-hue border + deep-toned text; icon leading. Variants: info (blue tint), success (green tint), warn (amber tint), danger (red tint). Enters with a 0.35s fade-in (6px rise), disabled under `prefers-reduced-motion`.

## 6. Do's and Don'ts

### Do:
- **Do** use Preto Roselar (#111418) for chrome and all financial values; weight 700–800, tabular figures.
- **Do** keep Vermelho Roselar (#D32F2F) under 10% of any screen — it is the alarm and the brand voice at once.
- **Do** replace Navy Legado (#0A2640) with Preto Roselar whenever you touch a legacy surface.
- **Do** put muted gray text (#6C757D) on white surfaces only; on the tinted body background it fails AA.
- **Do** state consequences in destructive confirms ("Esta ação não pode ser desfeita") with specific button labels.
- **Do** provide a `prefers-reduced-motion` alternative for every animation.

### Don't:
- **Don't** ship "startup SaaS flash": no gradient decoration, no glassmorphism, no hero-metric marketing tiles (anti-reference from PRODUCT.md).
- **Don't** build "dense ERP (Excel-like)" walls of fields and grids; one task per screen, progressive disclosure (anti-reference from PRODUCT.md).
- **Don't** introduce a second red or use red decoratively — A Regra do Vermelho Único.
- **Don't** use `border-left`/`border-right` stripes thicker than 1px as accents; use full borders or background tints.
- **Don't** convey status by color alone; pair every red/green signal with an icon or text.
- **Don't** go below 0.72rem text anywhere, or add font sizes between the five hierarchy steps.
