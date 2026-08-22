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

The index has three levels: an introductory band with the Alire setup command,
a search and type filter, and one semantic disclosure per crate. A matching
community shadow lives at `/community/`; the primary navigation and footer
make the catalog boundary explicit while retaining the same information
architecture. The cross-catalog action is an outlined, direction-marked switch
rather than an ordinary peer navigation label. Each closed
disclosure shows the crate name, selected release, description, and status. Its
open state uses the same composition as a dedicated crate page: structured
metadata in the main column and indexed versions, dependencies, a compact
dependant count, and manifest JSON in the right rail. Dedicated crate and
version routes render
the complete release without duplicating every manifest on the front page.

## Components

- Search and select controls use the website kit's surface, line, focus, and
  control-radius tokens.
- Crate disclosures are full-width rows with one-pixel separators. Hover and
  open states use tonal surfaces, not decorative shadows.
- Version labels and executable values use the shared mono stack.
- Each landing page places a compact, bounded crate-change digest before the
  package catalog. The detailed changes route expands that Git-derived history
  into added versions, development advances, and manifest updates.
- Each catalog exposes a peer statistics route in the primary navigation.
  Statistics are computed at build time from selected manifests, resolved
  dependencies, and Git history. Comparative distributions use labeled bars,
  monthly activity uses a compact column chart, and exact values remain visible
  without relying on hover, scripting, or color.
- Complete dependant lists live on a separate per-version route so large
  toolchain relationship sets do not dominate catalog results or browser find.
  The list groups by crate on a tonal surface, newest version first.
  The dependant's own selected version is bold, and each row ends with a
  verdict in a right-aligned rail so qualification scans vertically. The
  verdict is carried by its wording, not by colour alone.
- Violet identifies interactive/current state. Teal identifies source and
  availability facts; neither is used as interchangeable decoration.
- JSON and repository links use the website kit's standard primary and
  secondary pill actions.
- Full crate pages place version-specific changelog prose before the pinned
  README below structured release data. Release notes use an initially open
  native disclosure, and a nested, initially closed **See more** disclosure
  reveals only changelog entries following the current version, without
  repeating it or showing newer entries above it. A compact source-document
  rail identifies both materials. Markdown stays within a 74-character reading
  measure and is separated by rules rather than nested in decorative cards.
- Full crate pages keep indexed versions, dependencies, a link to dependants,
  and the direct manifest JSON download together in a right-hand information rail.
  The main release column promotes project links and source provenance, then
  presents package metadata without repeating the complete JSON inline.
  Repository revisions link to the exact supported forge tree and conditional
  artifact origins remain directly downloadable. Lower-frequency build, environment,
  configuration, and origin data uses one native disclosure after the primary
  facts. Dependency rows link to the highest indexed release
  admitted by their version set in index-priority order, including community
  releases, system externals, conditional branches, and releases matched
  through `provides`. Each row identifies whether Flyology or community
  supplied the match. Cross-catalog dependant groups link back to the exact
  dependant version.
  Dependant group names link to that crate's highest qualifying release, or
  its highest listed release when the current version qualifies none of them.
- Community crate pages link to Alire Crates CI. Detail pages fetch the small
  per-crate badge JSON at runtime to show its current aggregate result; the
  external report link remains usable when scripting or the request is
  unavailable.

## Responsive and accessible behavior

The summary becomes a single-column reading order on narrow viewports. Long
origins, hashes, and JSON scroll horizontally rather than shrinking. Native
`details` and `summary` elements retain keyboard and assistive-technology
behavior. Filtering reports its result count through a polite live region.
Motion is limited to transforms and opacity and is removed when reduced motion
is requested. Source Markdown headings are normalized beneath the page and
section headings, tables scroll horizontally, images remain fluid, and code
blocks preserve their own horizontal scrolling.
