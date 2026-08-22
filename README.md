# Flyology Alire index

This index contains development and release manifests maintained by the
[Flyology organization](https://github.com/flyology-ada). It is separate from
the Alire community index.

Keep the community index enabled for compiler and third-party dependency
resolution, then add this index ahead of it:

```sh
alr index --reset-community
alr index --add=git+https://github.com/flyology-ada/alire-index.git \
  --name=flyology --before=community
```

The repository's default branch is `main`. Release origins remain pinned to
exact source commits so an indexed version does not change after publication.

## Web catalog and JSON

The index is published at [crates.flyology.org](https://crates.flyology.org/).
The site is generated from the TOML manifests on every push to `main`; it does
not maintain a separate package inventory. The same build checks out the Alire
community index with full history and publishes a read-only shadow at
[`/community/`](https://crates.flyology.org/community/). GitHub Actions rebuilds
both catalogs on every push and once per day so community-only changes are
picked up without a Flyology commit.

Machine-readable data is available as an aggregate catalog and as one file per
package, plus a direct manifest object for each version:

- `https://crates.flyology.org/crates.json`
- `https://crates.flyology.org/crates/<package-name>.json`
- `https://crates.flyology.org/crates/<package-name>/<version>/manifest.json`

The community shadow exposes the same endpoints beneath `/community`, for
example `https://crates.flyology.org/community/crates.json` and
`https://crates.flyology.org/community/crates/<package-name>/<version>/`.

LLM-oriented catalog discovery is available at
`https://crates.flyology.org/llms.txt`.

Both forms contain the complete parsed manifest for every indexed version,
the resolved target for each dependency, and the indexed releases that depend
on it. Resolution follows the configured index order: Flyology first, then
community, selecting the highest matching release from the first catalog that
can satisfy the version set. Conditional dependencies retain their branch,
and community system externals link to a synthetic `system` release because
their concrete version is detected on the user's host. Each dependant records
the version set it declares and whether that requirement is satisfied,
resolved through `provides` so a dependency on `gnat` is matched against the
version a toolchain stands in for. Requirements are evaluated with Alire's
`semantic_versioning` rules.

Human-readable routes include:

- `https://crates.flyology.org/crates/<package-name>/`
- `https://crates.flyology.org/crates/<package-name>/<version>/`
- `https://crates.flyology.org/crates/<package-name>/<version>/dependants/`
- `https://crates.flyology.org/changes/`
- `https://crates.flyology.org/stats/`
- `https://crates.flyology.org/community/`
- `https://crates.flyology.org/community/changes/`
- `https://crates.flyology.org/community/stats/`

The package page selects the newest published version. If a crate has only
`-dev` manifests, it selects the newest development version and identifies the
crate as development-only. Homepage disclosures, crate pages, and version pages
expose project and repository links, the exact linked source revision and
subdirectory, maintainer profiles, release artifacts, availability, tags,
executables, and other package-level metadata. Lower-frequency build switches,
actions, environment, configuration, and conditional origin rules remain
available in a compact **Build and platform metadata** disclosure. Full crate
and version pages also render the nearest
`README.md` found from the manifest origin's `subdir` upward, at the exact
indexed commit. If the same search finds a `CHANGELOG.md`, the section headed
by that page's exact version is rendered as release notes, with the remaining
entries that follow it available through a **See more** disclosure. If an older
pinned commit predates the changelog, the generator looks for that exact
version in other indexed commits from the same repository and `subdir` path.
Relative documentation links and images remain pinned to the source commit used. The
homepage includes a bounded digest of recent index activity. The changes page derives its detailed publication and
development-update history from Git, including source revision and dependency
changes, so the Pages checkout retains full repository history.
The community landing page has the same bounded recent-changes digest for
crates and versions added or updated in that index, with the complete derived
history at `/community/changes/`. Community pages intentionally omit README
and changelog source cloning: mirroring hundreds of upstream source
repositories during every site build would make the shadow unreliable, while
the structured manifests, relationships, provenance, and JSON remain complete.
Community crate detail pages also link to the corresponding
[Alire Crates CI](https://alire-crate-ci.ada.dev/) report and load its small
per-crate badge summary in the browser. The external report link remains the
fallback if JavaScript or the status request is unavailable.
Each catalog also has a statistics page derived at build time. It reports
license declarations and package composition from selected releases, release
status, dependency resolution, and Git-derived age, monthly activity,
freshness, and frequently changed packages. Activity includes only paths for
manifests currently present in the catalog, so deleted packages are outside
the snapshot.
GitHub Pages serves JSON with `Access-Control-Allow-Origin: *`, so browser
applications on other origins can fetch these URLs directly. GitHub Pages does
not support repository-defined custom response headers, so clients should
treat this CORS policy as hosting-platform behavior.

Build and check the site locally with checkouts of
[`flyology-ada/website-kit`](https://github.com/flyology-ada/website-kit) and
[`alire-project/alire-index`](https://github.com/alire-project/alire-index):

```sh
python3 -m pip install -r requirements-site.txt
WEBSITE_KIT_DIR=../website-kit \
COMMUNITY_INDEX_DIR=../alire-index \
  ./scripts/build-site.sh
```

If `COMMUNITY_INDEX_DIR/index/index.toml` is absent, the local build generates
only the Flyology catalog. CI always supplies the community checkout.

The generator requires Python 3.11 or newer. Set `PYTHON` if the modern Python
executable is not the first `python3` on your path. Site generation fetches the
pinned source repositories; pass `--source-cache <directory>` directly to
`scripts/generate-site.py` to reuse its bare clones, or
`--skip-source-documents` for an intentionally offline metadata-only build.

The repository's Pages settings must use **GitHub Actions** as the source and
set `crates.flyology.org` as the custom domain. With an Actions publishing
source, GitHub stores the domain in repository settings and ignores a `CNAME`
file in the uploaded artifact.

## Patched GNAT toolchains

The index imports `gnat_flyology_native` manifests from immutable
[`flyology-ada/gnat-patches`](https://github.com/flyology-ada/gnat-patches/releases)
releases. The importer verifies the release tag, exact three-platform origin
set, release-asset URLs, and checksum sidecars before running `alr index
--check` and committing a new compiler version. It runs hourly and can also be
dispatched manually. Existing compiler manifests are immutable; changed
release content fails closed.

Select an indexed patched compiler by its GCC and patchset version:

```sh
alr -n toolchain --select \
  gnat_flyology_native=16.1.0-patchset.1.0.0
```

## Updating development origins

Run the updater from a clean `main` checkout to publish tagged releases and
advance every active development manifest to the exact `HEAD` advertised by
its configured Git remote:

```sh
./scripts/update-dev-origins.sh
```

The script first fast-forwards the index checkout from its upstream and
resolves each distinct origin once. A stable release is published by pushing a
`<crate>/v<version>` tag, such as `flyology_http/v0.1.0`. The updater fetches
each previously unindexed stable semantic-version tag, verifies the tagged
`alire.toml` has the matching crate name and version, imports its metadata, and
pins the new release manifest to the tag commit. For a monorepo crate, it reads
`alire.toml` from the development manifest's `subdir`. Malformed and unrelated
tags are ignored. Retired development manifests remain repository descriptors,
so later patch and minor release tags continue to be discovered automatically.

The script also updates all `*-dev.toml` manifests that share an origin to the
same default-branch commit. Each one is re-rendered from the `alire.toml` at
that commit plus a generated `[origin]` table, so dependencies and other
metadata never drift away from the source the manifest claims to describe. A
development manifest whose source has moved to a different version is reported
and left untouched, because a manifest may not name one version while its
source declares another. The complete result is validated with `alr index
--check`.

A development manifest serves two audiences. Locally a `[[pins]]` entry makes a
sibling checkout fulfil a dependency, which is how a monorepo builds its own
crates against the working tree. As an indexed dependency the same crate must
come from the index, where a local path means nothing. The updater therefore
publishes each manifest without its `[[pins]]` table, and the dependency itself
has to carry the constraint that takes over once the pin is gone. Pinning a
crate while leaving its dependency at `"*"` is refused rather than published,
so a pin can never silently widen what dependents resolve. It ignores local source checkouts, including unpushed commits. By
default it leaves changes for review; use `--commit` to create a
Problem/Solution commit, or `--push` to commit and push the update.

The hourly GitHub Actions workflow runs `--releases-only --push`, so tag-based
publication is automatic without advancing development origins. Development
updates remain an explicit local operation.

Pass one or more `--crate NAME` selectors to update only the chosen source
groups. Selecting any crate advances every development manifest from the same
origin, so the PostgreSQL facade, core, and versioned parser crates remain on
one source commit. With no selector, the script updates every development
origin:

```sh
./scripts/update-dev-origins.sh --crate flyology
./scripts/update-dev-origins.sh --crate flyology_postgres --push
./scripts/update-dev-origins.sh \
  --crate flyology --crate flyology_postgres
```

Stable releases retire old development lines per crate on the next run. For
example, once `0.1.0` exists, the updater ignores `0.1.0-dev` and every lower
`-dev` version of that crate, while a later `0.2.0-dev` remains active. Retired
manifests are not grouped by origin, resolved remotely, edited, or included in
generated commits. Other prerelease labels do not count as stable releases,
and build metadata does not affect the three-part semantic-version comparison.

Run the updater's isolated local-remote test with:

```sh
./scripts/test-update-dev-origins.sh
```
