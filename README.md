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
not maintain a separate package inventory.

Machine-readable data is available as an aggregate catalog and as one file per
package:

- `https://crates.flyology.org/crates.json`
- `https://crates.flyology.org/crates/<package-name>.json`

LLM-oriented catalog discovery is available at
`https://crates.flyology.org/llms.txt`.

Both forms contain the complete parsed manifest for every indexed version,
along with the indexed releases that depend on it. Each dependant records the
version set it declares and whether that requirement is satisfied, resolved
through `provides` so a dependency on `gnat` is matched against the version a
toolchain stands in for. Requirements are evaluated with Alire's
`semantic_versioning` rules.

Human-readable routes include:

- `https://crates.flyology.org/crates/<package-name>/`
- `https://crates.flyology.org/crates/<package-name>/<version>/`
- `https://crates.flyology.org/changes/`

The package page selects the newest published version. If a crate has only
`-dev` manifests, it selects the newest development version and identifies the
crate as development-only. The homepage includes a bounded digest of recent
index activity. The changes page derives its detailed publication and
development-update history from Git, including source revision and dependency
changes, so the Pages checkout retains full repository history.
GitHub Pages serves JSON with `Access-Control-Allow-Origin: *`, so browser
applications on other origins can fetch these URLs directly. GitHub Pages does
not support repository-defined custom response headers, so clients should
treat this CORS policy as hosting-platform behavior.

Build and check the site locally with a checkout of
[`flyology-ada/website-kit`](https://github.com/flyology-ada/website-kit):

```sh
WEBSITE_KIT_DIR=../website-kit ./scripts/build-site.sh
```

The generator requires Python 3.11 or newer. Set `PYTHON` if the modern Python
executable is not the first `python3` on your path.

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
--check`. It ignores local source checkouts, including unpushed commits. By
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
