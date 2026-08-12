---
name: Flyology Crate Index
description: A searchable, machine-readable catalog of Flyology Alire crates.
colors:
  ink: "oklch(27% 0.052 270)"
  ink-soft: "oklch(39% 0.043 270)"
  violet: "oklch(57% 0.19 285)"
  violet-deep: "oklch(47% 0.18 285)"
  teal: "oklch(73% 0.13 185)"
  teal-deep: "oklch(56% 0.11 185)"
  paper: "oklch(98.5% 0.006 270)"
  surface: "oklch(95.8% 0.015 270)"
  surface-strong: "oklch(92.5% 0.024 270)"
  line: "oklch(86% 0.025 270)"
typography:
  body:
    fontFamily: "Geologica, Avenir Next, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.65
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Cascadia Code, Roboto Mono, monospace"
    fontSize: "0.82rem"
    fontWeight: 400
    lineHeight: 1.7
rounded:
  control: "0.45rem"
  panel: "0.9rem"
  feature: "1.4rem"
spacing:
  xs: "0.45rem"
  sm: "0.85rem"
  md: "1.25rem"
  lg: "2rem"
---

# Design System: Flyology Crate Index

## Overview

The crate index is a product-oriented extension of the Flyology website kit.
It inherits the kit's Geologica typography, tinted technical grid, violet and
teal semantic palette, light and dark themes, focus treatment, and compact
mechanical controls. The catalog itself is denser than a project landing page:
package rows are separated by rules and expand in place rather than forming a
wall of detached cards.

## Information hierarchy

The page has three levels: an introductory band with the Alire setup command,
a search and type filter, and one semantic disclosure per crate. Each closed
disclosure shows the crate name, current version, description, and tags. Its
open state presents structured metadata and a second disclosure for every
indexed version.

## Components

- Search and select controls use the website kit's surface, line, focus, and
  control-radius tokens.
- Crate disclosures are full-width rows with one-pixel separators. Hover and
  open states use tonal surfaces, not decorative shadows.
- Version labels and executable values use the shared mono stack.
- Violet identifies interactive/current state. Teal identifies source and
  availability facts; neither is used as interchangeable decoration.
- JSON and repository links use the website kit's standard primary and
  secondary pill actions.

## Responsive and accessible behavior

The summary becomes a single-column reading order on narrow viewports. Long
origins, hashes, and JSON scroll horizontally rather than shrinking. Native
`details` and `summary` elements retain keyboard and assistive-technology
behavior. Filtering reports its result count through a polite live region.
Motion is limited to transforms and opacity and is removed when reduced motion
is requested.
