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

## Updating development origins

Run the updater from a clean `main` checkout to advance every development
manifest to the exact `HEAD` advertised by its configured Git remote:

```sh
./scripts/update-dev-origins.sh
```

The script first fast-forwards the index checkout from its upstream, resolves
each distinct origin once, updates all `*-dev.toml` manifests that share that
origin to the same commit, and validates the result with `alr index --check`.
It ignores local source checkouts, including unpushed commits. By default it
leaves the changes for review; use `--commit` to create a Problem/Solution
commit, or `--push` to commit and push the update.

Run the updater's isolated local-remote test with:

```sh
./scripts/test-update-dev-origins.sh
```

## Packages

- `flyology` `0.1.0-dev`
- `flyology_postgres` `0.1.0-dev`
- `flyology_postgres_sql_core` `0.1.0-dev`
- `flyology_postgres_sql_v14` `0.1.0-dev`
- `flyology_postgres_sql_v15` `0.1.0-dev`
- `flyology_postgres_sql_v16` `0.1.0-dev`
- `flyology_postgres_sql_v17` `0.1.0-dev`
- `flyology_postgres_sql_v18` `0.1.0-dev`
- `flyology_postgres_sql` `0.1.0-dev`
