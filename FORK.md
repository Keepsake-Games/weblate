# Keepsake fork of Weblate

This fork makes Discord the single identity provider and the Discord handle the
attribution identity, for a community that lives on Discord rather than on
e-mail. It is deployed at `localize.jumpspacegame.com` and fed by an in-house
Unity localization tool over the REST API.

## Upstream base

**Current base: `weblate-2026.7.1`.**

`main` is the deployable branch: it is our commits sitting directly on top of an
upstream release tag. Upstream itself is not mirrored in a local branch; it is
tracked through the `upstream` remote's own refs.

```sh
git remote add upstream https://github.com/WeblateOrg/weblate.git
git fetch upstream --tags
git tag --list 'weblate-20*' | sort -V | tail -5
git log --oneline weblate-2026.7.1..main   # exactly our commits, nothing else
```

The fork is always rebased onto a tagged upstream *release*, never onto upstream's
`main`. Two reasons:

* The production image is built `FROM weblate/weblate:<tag>`, and that image
  contains a released version. Basing our code on a `.dev0` snapshot means the
  code and the dependency set in the image disagree.
* A release tag gives a fixed point to distinguish our bugs from upstream's.

**Consequence of that layout: `main` gets rebased, so its history is rewritten on
every upstream upgrade.** Pushing an upgrade needs
`git push --force-with-lease origin main`, and anyone else with a clone recovers
with `git fetch origin && git reset --hard origin/main` rather than a merge. Do
not merge `origin/main` into a stale local `main`; that resurrects the pre-rebase
commits as duplicates.

## What the fork changes

Each commit is self-contained:

| Commit | Area |
| --- | --- |
| `feat(auth): make Discord the sole identity provider` | Settings, pipeline, synthetic addresses, frozen username, synced display name |
| `feat(accounts): never queue mail to undeliverable addresses` | Notification guards, credits `mailto:` suppression |
| `feat(accounts): serve avatars from the Discord CDN` | `avatar.py` |
| `feat(api): filter changes by author and expose the author handle` | `ChangeFilter`, `ChangeSerializer` |
| `feat(trans): credit the voters on an accepted suggestion` | `suggestion.py` |
| `feat(accounts): watch configured projects from the first login` | `INITIAL_WATCHED_PROJECTS` |
| `feat(ui): make the navigation search box searchable` | `base.html`, `loader-bootstrap.js` |
| `feat(ui): self-host the Satoshi font family` | `static/fonts/`, `meta-css.html` |
| `docs: document Discord auth and the attribution contract` | `docs/` |

Anything not in that list is upstream. `weblate/settings.py` is local and
git-ignored; it holds the Discord client secret.

### `INITIAL_WATCHED_PROJECTS`

A list of project slugs, or `["*"]`, that a brand new account starts out
watching; `WEBLATE_INITIAL_WATCHED_PROJECTS` in the Docker environment. Watching
is what puts a project in the navigation menu and on the dashboard, so on a
single-project server this is the difference between the project being one click
away and three. Applied once, when the account is created, so a member who
deliberately unwatches stays unwatched.

The default is empty, which is upstream behaviour, and `settings_example.py`
leaves it commented out: `settings_test.py` inherits from it, and two upstream
dashboard tests assert that a fresh account watches nothing.

### Satoshi

`weblate/static/fonts/satoshi.css` declares the family and
`weblate/static/fonts/satoshi/` holds the `woff` files, linked from the
`{% compress css %}` block in `snippets/meta-css.html`. Serving it is all the
fork does; which font the interface uses is a runtime choice under
**Manage → Appearance**, where *Page font* and *Header font* are free-text CSS
`font-family` values. Set them to `Satoshi, sans-serif`.

Self-hosted rather than pulled from a CDN because the CSP is `font-src 'self'`.
The declarations use one family with real weights, not the family-per-weight
shape the foundry's generated stylesheet has, so Bootstrap's 500/600/700 get the
actual cuts instead of synthetic bold.

Adding static files and editing a `{% compress %}` block is safe despite
`COMPRESS_OFFLINE`, because the official image's entrypoint runs
`collectstatic --clear` and `compress --force` on every container start.

### Navigation search box

Upstream's box is a typeahead only. autoComplete.js swallows Enter unless its
`submit` option is set, so pressing Enter does nothing at all, on any Weblate
instance. The form now points at `search`, and a keydown handler submits it when
no suggestion is highlighted, leaving Enter-to-pick-a-suggestion intact.

## Rebasing onto a new release

Work on a scratch branch so a failed upgrade never leaves `main` half-rebased:

```sh
git fetch upstream --tags
git switch -c upgrade-<new> main
git rebase --onto weblate-<new> weblate-<old> upgrade-<new>
# verify (see "Verifying a build"), then:
git switch main && git reset --hard upgrade-<new>
git push --force-with-lease origin main
```

`weblate-<old>` is the tag named at the top of this file. Update it there in the
same commit as the upgrade, or the next person cannot compute the rebase range.

Conflicts concentrate in five files, because upstream refactors around them:

* `weblate/trans/views/reports.py` for the credits renderer
* `weblate/api/serializers.py` for `ChangeSerializer`
* `weblate/api/views.py` for `ChangeFilter`
* `weblate/accounts/pipeline.py`
* `weblate/settings_example.py` and `weblate/settings_docker.py`
* `weblate/static/loader-bootstrap.js` and `weblate/templates/base.html` for the
  navigation search box

When a conflict shows an empty upstream side against a large block of ours, the
cause is almost always that upstream moved the surrounding code in a release we
skipped. Take the upstream file (`git checkout --ours -- <file>`) and re-apply
our change against the new shape, rather than resolving hunk by hunk.

`docs/changes.rst` is deliberately left untouched. Our changes are recorded here
instead, because that file changes at the top on every upstream release and
editing it would conflict every single time.

## Building the production image

The fork's diff adds no dependencies, so the released base image already has the
right dependency set.

**The image tag has four components, the git tag has three.** Release
`weblate-2026.7.1` is published as `weblate/weblate:2026.7.1.<n>`, where the
fourth number is the image rebuild (same Weblate, refreshed base layers). There
is no `weblate/weblate:2026.7.1`; pulling it fails with `manifest unknown`. Find
the current one with:

```sh
curl -s "https://hub.docker.com/v2/repositories/weblate/weblate/tags/?name=2026.7.1" \
  | tr ',' '\n' | grep '"name"'
```

```dockerfile
FROM docker.io/weblate/weblate:2026.7.1.1
USER root
ADD src.tar /src/
RUN /app/venv/bin/python -m ensurepip \
 && /app/venv/bin/python -m pip install --no-deps /src \
 && rm -rf /src
USER 1000
```

Three things about that `RUN` line, each learned the hard way:

* The image has **no pip** and no uv on `PATH`; its venv is at `/app/venv`.
  `ensurepip` is available and is the way in.
* Do **not** add `--no-build-isolation`. The build backend is setuptools, which
  is not installed in the venv, so the build needs pip's isolated environment.
* `--no-deps` is what makes this safe: it installs our code without touching the
  dependency set the release was tested against.

Build the context with `git archive --format=tar HEAD -o src.tar` rather than
`COPY .`, so the image never contains `.git`, `__pycache__`, or the git-ignored
`weblate/settings.py` with the Discord secret in it.

This inherits the official image's uwsgi, nginx, Celery supervision, entrypoint,
migrations, and healthcheck. Our modified `weblate/settings_docker.py` rides
along, so the Discord-only authentication backends and pipeline become the
image's defaults.

Pin the tag in the compose file. Never deploy `latest`.

### Verifying a build

`weblate check` exercises Django's system checks plus Weblate's own
configuration checks, and needs a database, a cache, and a secret:

```sh
podman run --rm --network <net> --user 0 \
  -e WEBLATE_SITE_DOMAIN=localize.jumpspacegame.com \
  -e POSTGRES_HOST=<host> -e POSTGRES_USER=weblate -e POSTGRES_PASSWORD=<pw> \
  -e POSTGRES_DATABASE=<db> -e REDIS_HOST=<cache> \
  --entrypoint sh <image> \
  -c 'mkdir -p /app/data && printf x > /app/data/secret && /app/venv/bin/weblate check'
```

The published wheel **excludes the test packages**, so `weblate test` cannot see
them. To run the suite, add the source tree and the test dependency group to a
throwaway image layer and run from there:

```sh
/app/venv/bin/python -m pip install --no-deps \
  coverage responses selenium standardwebhooks pytest pytest-django
cd /srcfull && /app/venv/bin/python manage.py test --noinput --parallel=1 \
  weblate.accounts weblate.api weblate.trans.tests.test_reports \
  weblate.trans.tests.test_suggestions
```

`--parallel=1` is required: on Python 3.14 the parallel runner dies with
`TypeError: cannot pickle 'traceback' object` on the first failure, which hides
the real error.

One known failure in this setup, **not** caused by the fork:
`weblate.accounts.tests.test_notifications.NotificationTest.test_notify_html_language`
asserts on a translated string, and a `git archive` tree has no compiled `.mo`
files, so the mail renders in English. It fails identically on a pristine
upstream tree, which is how to confirm any other suspicious failure: run the
same test from a pristine tag checkout in the same image and compare.

## Notes for this deployment

* Components use the `local` VCS backend, so there is no remote git to re-clone
  from. The database is the only copy of suggestions, votes, and change history,
  and therefore of credits and patch notes. Back it up accordingly.
* Uploads must not send `author_email`, or attribution creates a junk
  non-Discord account keyed on that address.
* A token consuming credits needs the `reports.view` permission on the scope, or
  the report is silently generated for the requesting user only and looks empty.
