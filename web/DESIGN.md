---
version: alpha
name: Hermes-Inspired Career Operations
description: Experimental retro-futurist identity for a practical, human-supervised career and travel workspace.
colors:
  primary: "#0605F2"
  secondary: "#17172D"
  tertiary: "#8D8DFF"
  neutral: "#080817"
  background: "#080817"
  surface: "#17172D"
  text: "#E7E7F0"
  muted: "#85859D"
  border: "rgba(255,255,255,0.12)"
  light-background: "#F7F6F3"
  light-text: "#080817"
typography:
  display:
    fontFamily: Barlow Condensed
    fontSize: 3.5rem
    fontWeight: 600
    lineHeight: 0.95
    letterSpacing: "-0.025em"
  body:
    fontFamily: Space Grotesk
    fontSize: 1rem
    fontWeight: 400
    lineHeight: 1.5
  mono:
    fontFamily: JetBrains Mono
    fontSize: 0.8125rem
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: 2px
  md: 4px
  lg: 6px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
components:
  app-canvas:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: 16px
  muted-label:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    rounded: "{rounded.sm}"
    padding: 8px
  divider:
    backgroundColor: "{colors.border}"
    width: 1px
    height: 1px
  light-mode-canvas:
    backgroundColor: "{colors.light-background}"
    textColor: "{colors.light-text}"
    rounded: "{rounded.sm}"
    padding: 16px
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#FFFFFF"
    rounded: "{rounded.md}"
    padding: 12px
  button-primary-hover:
    backgroundColor: "#3938FF"
    textColor: "#FFFFFF"
    rounded: "{rounded.md}"
    padding: 12px
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: 16px
---

## Overview

A functional information console for career search and travel planning. The visual language is neo-brutalist and editorial: condensed display typography, precise geometric dividers, electric-blue signals, and restrained CRT/halftone effects. It is inspired by Hermes Agent's visual personality without copying its site or interface.

The interface is an **Operate** surface first: the user's next action, review state, and due dates take priority over decoration or vanity metrics.

## Colors

- **Electric blue (#0605F2):** primary action, selection, active markers, and graphic accents. Do not use it for small text on the deep background; it has insufficient contrast there.
- **Accessible blue tint (#8D8DFF):** small blue text and active indicators on dark surfaces.
- **Deep background (#080817) / secondary surface (#17172D):** dark-theme canvas and panels.
- **Primary text (#E7E7F0) / muted text (#85859D):** high-contrast content hierarchy.
- **Borders (white at 12%):** structure without heavy card chrome.
- A light theme is supported with dark ink and electric-blue links; the dark palette remains the visual signature.

## Typography

- **Barlow Condensed** (or a locally available condensed equivalent) for display headings and short labels. Use uppercase selectively, not for long copy.
- **Space Grotesk** for interface and body text.
- **JetBrains Mono** for dates, IDs, status traces, and system labels only.
- Keep body copy comfortably readable; do not set ordinary paragraph text in condensed or monospace fonts.

## Layout

Use asymmetric editorial grids and compact, information-dense work surfaces. Keep the sidebar distinct, align tables and filters, and make the primary next action obvious. Use lists and tables for scanning; use boards only where moving a record between stages is the task. Responsive layouts must collapse the sidebar and metadata without hiding primary actions.

## Elevation & Depth

Prefer flat surfaces and thin borders. Shadows are limited to popovers and modal layers; avoid ambient glow on every card. Use grain, halftone, or scanlines only on a lead surface or a deliberate system-status treatment.

## Shapes

Use tight corner radii (2–6px), square separators, and clear grid alignment. Prefer lists, tables, and split-pane inspectors over stacks of rounded cards. Avoid oversized pills and uniformly rounded cards. Interactive targets remain at least 44px high on touch layouts.

## Components

- **Navigation:** compact, high-contrast, keyboard accessible; current location is indicated by both color and a visible shape/border change.
- **Action queue:** prioritize due/overdue items, decisions awaiting the user, and verified fresh results. Never imply a search ran when only a health check ran.
- **Job records:** expose stage, score, next action, deadline, and source link; provide table/list and board views where useful.
- **Flight records:** show route, travel dates, total price, currency, source, and observation time. Treat prices as time-sensitive and never claim a saved quote is bookable.
- **Primary buttons:** white text on electric blue. Secondary actions use a thin border and no glow.
- **Focus states:** use a high-contrast outline that is visible in both themes; all interactions must work by keyboard and screen reader.
- **Motion:** use short transitions for state feedback; no looping decoration. Respect `prefers-reduced-motion`.

## Do's and Don'ts

- Do use graphic effects selectively and keep controls predictable.
- Do preserve human approval before sending applications or purchasing travel.
- Do state the source and last-checked time for externally sourced vacancies and prices.
- Do verify contrast for every text-on-surface pairing.
- Do not copy Hermes Agent's website composition or assets.
- Do not use scanlines behind dense body copy, forms, or data tables.
- Do not let animation, noise, or halftone reduce legibility or responsiveness.
- Do not fabricate vacancy, application, or airfare values for visual filler.
