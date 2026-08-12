# Product

## Register

product

## Users

Ada and GNAT developers use the index to discover Flyology-maintained crates,
compare the versions available to Alire, inspect dependency and platform
constraints, and obtain machine-readable package metadata for tooling.

## Product Purpose

The Flyology crate index is the human and machine-readable view of the custom
Alire index. It should make every indexed manifest easy to inspect, keep the
shortest path to configuring Alire obvious, and publish a stable JSON contract
that other sites can consume without maintaining a second package inventory.

## Brand Personality

Precise, mechanical, and factual. The interface should feel like a
well-annotated engineering index: compact and approachable without promotional
claims or generic developer-dashboard language.

## Anti-references

Avoid neon-terminal developer-tool cliches, generic startup card walls,
glassmorphism, marketing language, ecosystem claims, and decorative complexity
that competes with package data.

## Design Principles

- Make package identity, purpose, and newest indexed version scannable first.
- Reveal complete manifest detail progressively without hiding it from
  keyboard or assistive-technology users.
- Keep the installation command and downloadable JSON within one action.
- Derive every published representation from the TOML manifests so the page
  and API cannot drift from the Alire index.
- Reuse the Flyology website kit's visual language and interaction patterns.

## Accessibility & Inclusion

Target WCAG 2.2 AA. Preserve semantic headings and disclosure controls, full
keyboard navigation, visible focus states, color-independent meaning, readable
code and metadata at narrow widths, and a complete reduced-motion experience.
