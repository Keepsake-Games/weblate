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
| `chore(build): add the production Dockerfile` | `Dockerfile` |
| `feat(auth): admit only holders of a Discord role, on one sign-in page` | Invite-only gate, sign-in page, no separate registration |
| `feat(ui): restyle the interface for Jump Space` | Brand blue, CJK fonts, no logo, theme flash, single project link |
| `feat(build): serve our own maintenance pages` | `docker/nginx-errors/`, `Dockerfile` |
| `feat(accounts): ask newcomers to set up their languages` | `Profile.onboarded`, onboarding page and banner |
| `feat(trans): add Peon mode, one task at a time` | `trans/views/peon.py`, `peon*.html`, `editor/peon.js`, `styles/peon.css`, navigation button in `base.html`; later: rotated per-session queue, counter suggestions |

Anything not in that list is upstream. `weblate/settings.py` is local and
git-ignored; it holds the Discord client secret.

### Single project

Upstream's `SINGLE_PROJECT`, set to a project slug, already redirects the
dashboard to that project; the fork exposes it to Docker as
`WEBLATE_SINGLE_PROJECT` and also replaces the "Projects" menu in `base.html` with
a plain "Project" link to it (`single_project_url`, from
`weblate/trans/context_processors.py`). `True`, upstream's "whichever project is
the only one", keeps the menu, since there is no slug to link to. The URL is built
from the slug without a database lookup, so a wrong slug just leads to a 404.

### Invite-only sign-in

`DISCORD_REQUIRED_GUILD` and `DISCORD_REQUIRED_ROLES`
(`WEBLATE_DISCORD_REQUIRED_GUILD`, comma-separated `WEBLATE_DISCORD_REQUIRED_ROLES`
in Docker) restrict sign-in to members of one Discord server who hold at least
one of the roles; an empty role list admits any member. The
`require_discord_membership` pipeline step, right after `auth_allowed`, asks
Discord for the member's roles with the `guilds.members.read` scope, which the
Docker settings add only when a server is configured. Discord's 404 for a
non-member, or a missing role, becomes a message on the sign-in page naming the
role from `DISCORD_REQUIRED_ROLE_NAME` (`WEBLATE_DISCORD_REQUIRED_ROLE_NAME`);
the name is configured because a user token cannot read role names;
any other failure fails the sign-in, so an unverifiable member is never admitted.
Checked on every sign-in, superusers included; open sessions are not cut short.

### Sign-in page

Upstream forwards the login page straight to the provider when there is only
one, and Discord completes without interaction once consent was given, so the
welcome text and any rejection message never got seen. `WeblateLoginView.dispatch`
no longer forwards; the page shows the Discord button. `accounts/snippets/login-about.html`
carries our welcome text instead of the "continuous localization" blurb, and
`login-info.html` shows nothing below it unless legal documents are configured:
the upstream points about public names, commit e-mail and per-project licenses
are gone.
The sign-in page has no site title heading above its columns, its card is
headed plain "Sign in" rather than "Sign in to <site title>", and the
"Self-hosted" badge is gone from `accounts/snippets/login-title.html`, which the
2FA, password reset and registration pages still use for their heading.

There is no separate registration: the first Discord sign-in creates the account.
The "Register" navigation button and the "Register new account" link are gone,
the engage page offers "Sign in", and the `register` view forwards to the login
page (keeping `next`) whenever every sign-in backend may also register and e-mail
is not among them. Closed registration and `REGISTRATION_ALLOW_BACKENDS` subsets
keep the upstream behavior. `test_selenium.py` opens the register page by URL
instead of the removed button.
Empty by default, which keeps upstream behaviour for the inherited tests.

### Language setup

Until a member saves `/accounts/onboarding/`, every page shows a banner asking
them to pick their translated languages (at least one) and secondary languages
(optional). Saving sets `Profile.onboarded`, added by
`accounts/migrations/0033_profile_onboarded.py`. Existing accounts start out
not onboarded, so they see it too. Saving the same languages from the profile
page does not count: that page saves every tab at once, so it cannot tell a
deliberate language choice from any other edit. Upstream's "Added <language> to
your translated languages" notice is held back until setup is done, since the
banner already covers it and the page offers that language preselected.

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
`{% compress css %}` block in `snippets/meta-css.html`. It is the interface
font out of the box: `--bs-font-sans-serif` in `weblate/static/styles/variables.css`
puts it ahead of upstream's Source Sans 3, which stays as the fallback. Leave
*Page font* and *Header font* under **Manage → Appearance** empty; anything set
there overrides it.

Self-hosted rather than pulled from a CDN because the CSP is `font-src 'self'`.
The declarations use one family with real weights, not the family-per-weight
shape the foundry's generated stylesheet has, so Bootstrap's 500/600/700 get the
actual cuts instead of synthetic bold.

Adding static files and editing a `{% compress %}` block is safe despite
`COMPRESS_OFFLINE`, because the official image's entrypoint runs
`collectstatic --clear` and `compress --force` on every container start.

### Noto Sans for CJK

Chinese, Japanese and Korean translations render in Noto Sans SC, TC, HK, JP and
KR, the faces the game uses for those languages, instead of falling back from
Satoshi to whatever the system has. `weblate/static/fonts/noto.css` declares the
faces and picks one per language code on the `lang` attribute Weblate puts on
translation text and editors; `weblate/static/fonts/noto/` holds Regular only,
converted to WOFF2 from the game's `Assets/GUI/Fonts/NotoSans*-Regular.otf`
(about 21 MB together). A browser downloads a face only when a page shows that
language, and never when the font is installed locally. Licensed OFL-1.1, text
in `LICENSES/OFL-1.1.txt` and next to the fonts, annotated in `REUSE.toml`.

### Open navigation item

An opened top-level dropdown in the navigation bar is highlighted with
`--wl-nav-open-bg` (`#0f3e54`) and `--wl-nav-open-text` (white), set in
`weblate/static/styles/variables.css` and used by the
`.navbar-nav .nav-link.show` rule in `weblate/static/styles/main.css`. Upstream
uses the primary color there, with the page background as text color, which in
dark mode would be near-black on `#0f3e54`; hence the explicit text color for
both themes. None of the **Manage → Appearance** settings reach this rule.

### Navigation brand and icon hover

The site name and the icons in the navigation bar hover with the same color as
the navigation links, `--bs-navbar-hover-color` (the **Hover color** under
**Manage → Appearance**). Upstream ties them to the *Focus color* instead (the
brand through the `configuration/custom.css` template, the icons through the
`.navbar a:hover path` fill in `main.css`), which with our colors is the
navigation background in dark mode, so they vanished on hover. The brand is
overridden by a rule after `.navbar-brand` and the icon fill is changed in
place, both in `weblate/static/styles/main.css`, rather than by editing that
template, so the Appearance settings keep working as upstream documents them.

### Brand blue instead of turquoise

Upstream's brand turquoise is replaced throughout with the hue of our `#0f3e54`,
shade by shade: each color keeps its saturation and has its lightness solved so
that its WCAG relative luminance is unchanged, which keeps every contrast ratio
the same (within 8-bit rounding). That covers `variables.css`, `main.css`,
`bulk-accept.css`, `style-dark.css`, the mail stylesheet, the theme colors in
`base.html`, `site.webmanifest` and `browserconfig.xml`, the activity chart, the
**Appearance** form defaults in `wladmin/forms.py`, and the `access/0.svg` and
`icons/pencil-mini.svg` icons. Mapping, for redoing it after an upgrade:

| Upstream | Ours |
| --- | --- |
| `#0a3d2f` | `#0d394e` |
| `#0f9375` | `#148ac2` |
| `#107a62` | `#15739f` |
| `#114444` | `#164156` |
| `#124437` | `#164154` |
| `#144d3f` | `#194960` |
| `#158068` | `#1b79a4` |
| `#1c8f75` | `#2487b6` |
| `#1d6f5a` | `#24698a` |
| `#1fa385` | `#289ad0` |
| `#29b396` | `#4baad6` |
| `#2eccaa` | `#78bfe1` |
| `#4eeac9` | `#a4daf4` |

Left alone on purpose: the named label colors (teal, olive, lime and their dark
variants), which admins choose by name; `get_percent_color` in
`weblate/utils/views.py`, where green, blue and red mean good, medium and poor;
and migrations.

### Dark theme accent

The dark theme's link and accent color is `#c7ebeb` instead of upstream's
`#6cf8d8`: links, outline buttons, the search icon, green state icons and
`--wl-navi-color`, all in `weblate/static/style-dark.css` (including
`--bs-link-color-rgb`). The light theme's link color is unchanged.

Primary buttons are `#0f3e54` in both themes (`.btn-primary` in
`variables.css` and `style-dark.css`), with their hover color left as upstream's.

### No Weblate logo

There is no logo until we have our own. `snippets/branding.html` shows only the
site title; `base.html` declares an empty `data:,` favicon so browsers do not fall
back to requesting `/favicon.ico`, and drops the touch, mask and tile icons;
`site.webmanifest` lists no icons; `browserconfig.xml` has no tile image; and the
`/favicon.ico`, `/apple-touch-icon.png` and `/favicon-<size>.png` redirects to the
Weblate PNGs are gone from `weblate/urls.py`. `weblate/static/favicon.ico` is
deleted outright, because the official image's nginx serves `/favicon.ico`
straight from the collected static files without asking Django. The other image
files are untouched. To add a logo later, restore those references pointing at
our own files.

### No light flash in dark mode

With the "auto" theme, `<html>` is served with `data-bs-theme="auto"` and
`loader-bootstrap.js` resolves it only once the deferred scripts run, so Bootstrap
components such as cards and buttons painted light first. `static/js/theme-init.js`,
loaded synchronously at the top of `<head>` in `base.html`, resolves it before the
first paint. A file rather than an inline script, so the CSP stays as it is.

### Navigation search box

Upstream's box is a typeahead only. autoComplete.js swallows Enter unless its
`submit` option is set, so pressing Enter does nothing at all, on any Weblate
instance. The form now points at `search`, and a keydown handler submits it when
no suggestion is highlighted, leaving Enter-to-pick-a-suggestion intact.

### Peon mode

A third editor next to the full editor and Zen, at `/peon/<path>/`. It shows one
task at a time in a full screen overlay and is driven entirely from the keyboard.

**Ways in.** The "Translate now" button in the navigation leads to `/peon/`
(`peon-start`), a job picker that first sends anyone without
`Profile.onboarded` through the onboarding page. It offers the user's languages
in the project (`SINGLE_PROJECT`, else watched projects, else all allowed ones)
with their open counts, and two jobs, picked with <kbd>1</kbd> or <kbd>2</kbd>. Inspect is disabled while there is no suggestion by someone else to review, since nobody can process their own.
The person-digging button on the translation overview, the full editor
(<kbd>Alt+P</kbd> there) and Zen go straight to translate mode on that object,
keeping the current search.

**Modes** (`?mode=`):

* `translate` (default; queue `state:<translated AND NOT has:suggestion`, so a
  string with any suggestion belongs to review instead). Type, <kbd>Enter</kbd> saves,
  <kbd>Shift+Enter</kbd> is a new line, <kbd>Ctrl+→</kbd> skips,
  <kbd>Ctrl+1</kbd> copies the source, <kbd>Ctrl+2</kbd> to <kbd>Ctrl+9</kbd>
  insert glossary terms. Without edit permission it submits a suggestion. Every
  10 finished translations (`PEON_REVIEW_ROUND`) a "Review round" slips in up to
  3 suggestion cards from a separate `mode=review` feed, then announces the way
  back. A unit finished in one feed is dropped from the other.
* `review` (queue `has:suggestion`, narrowed by `PeonNavigation` to strings with a
  suggestion the reviewer neither wrote nor voted on: "pending", see
  `get_pending_suggestions`; the start page counts the same way). One pending
  suggestion: <kbd>→</kbd>
  accepts, <kbd>←</kbd> rejects, <kbd>↓</kbd> skips; dragging the card does the
  same. Without accept permission (for example suggestion voting with
  auto-accept) the sides are upvote and downvote. Several suggestions on the
  string make a choice card instead ("Pick the best suggestion", no dragging):
  every suggestion is listed, the pending ones numbered, <kbd>1</kbd> to
  <kbd>9</kbd> accepts or upvotes that one, <kbd>←</kbd> ("None of these")
  rejects or downvotes all pending ones, and the rest show "Yours", "You
  upvoted" or "You downvoted". <kbd>↑</kbd> ("Suggest different") counters: the
  card turns into a translate card prefilled with the first pending suggestion, and saving
  (action `counter`) stores the user's wording the way a translate card would
  (translation, or suggestion without edit permission), then deletes every
  pending one if the user may (`suggestion.delete`), else downvotes them if voting is
  on, else leaves them for others. The card says which before saving. Writing
  out the text of a pending suggestion, including saving the prefill
  unchanged, counts as agreeing with it, so nobody takes over a suggester's credit.

**Voting.** Weblate counts the author's own upvote, so with auto-accept at 2 a
single upvote from a reviewer accepts. Submitting text identical to someone
else's suggestion upvotes it (Weblate does that silently and reports "already
exists"; Peon treats it as done and says it counted as a vote). When voting
turns the user's saves into suggestions (`suggests_only`), the HUD says
"Suggesting", the start page "Suggest translations", and the key hints say
Suggest, Upvote and Downvote.

**Machine drafts.** AI placeholders from the Unity XLFTranslator are uploaded
with method `fuzzy` ("Needs editing"), not as suggestions, so they never enter
voting. They stay in the translate queue as "Improve this draft" cards with the
draft in the box; the first human suggestion moves the string to review. The
tool's pull skips anything below translated, so the game keeps shipping the
draft until a suggestion is accepted.

Drafts pushed earlier as suggestions are converted with
`weblate suggestions_to_drafts --user <username> [--project <slug>]` (a dry run;
add `--apply`, and `-v 2` to list each string). For each string the account
suggested on, the newest suggestion becomes a "Needs editing" draft recorded as
"Automatically translated" (action 6, which the tool's credits do not count,
and searchable as `is:automatically-translated`), and the account's other
suggestions there are removed. Strings already translated, with a different
draft, or whose suggestion someone else voted on are left alone and counted.

**Sharing a queue.** Each Peon session snapshots its queue under its own session
key (`search_peon_…`, `PeonNavigation`, so the full editor never reuses the
rotated order) and rotates it to a random start, so people starting on the same
language together begin in different places. Every batch is re-checked against
the live query, dropping strings someone else finished since the snapshot. The
progress bar follows the card's position in the snapshot, and <kbd>Esc</kbd>
finds the string by `checksum` rather than by offset.

<kbd>Esc</kbd> drops out to the full editor on the same string with the same
search; its button returns in translate mode. An empty queue shows the finish
screen instead of Zen's "No strings found" redirect (`blank=True`).

The card shows the explanation or note; matching glossary terms sit in a side
panel (below the card on narrow screens), with forbidden and untranslatable
terms marked. History and comments are not shown, a comment count badge says
when it is worth dropping out.

Saves go to `save_peon`, which returns JSON and reuses the full editor's
permission checks, suggestion handling and `perform_translation`. A newly failing
check keeps the card up (the text is saved). Reviews are sent optimistically so
swiping stays instant; a failed one shows its message after the card is gone.
Failures never cost the combo.

The juice is all client side: each typed character bursts from the caret in all directions as coloured letter ghosts and confetti, on a
layer behind the input, so only what clears its edge shows; a combo counter that
quietly resets after 15 minutes without a completed task, with no visible
countdown so volunteers are not rushed; sparks, floating score, screen shake,
milestone banners, and Web Audio blips (no audio assets; <kbd>Alt+M</kbd> mutes,
remembered in `localStorage`). `prefers-reduced-motion` turns the motion off. The overlay follows the resolved `data-bs-theme`: light tokens by default, dark ones under `[data-bs-theme="dark"]`.
The views live in their own module rather than `edit.py` to keep upstream
rebases quiet; the navigation button's style is the last block of
`styles/main.css`.

The icon, `static/icons/person-digging.svg`, is Font Awesome Free
(CC BY 4.0); keep the attribution comment inside the file.

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
* `weblate/static/styles/variables.css` and `weblate/static/styles/main.css` for
  the default font and the open navigation item
* every stylesheet and template in the brand blue table, after any upstream
  restyle
* `weblate/static/style-dark.css` for the dark theme accent
* `weblate/templates/base.html`, `weblate/urls.py` and the icon templates for the
  removed logo
* `weblate/static/loader-bootstrap.js` and `weblate/templates/base.html` for the
  navigation search box
* `weblate/accounts/migrations/0033_profile_onboarded.py` whenever upstream
  adds an accounts migration: renumber ours after upstream's newest and point
  its dependency there, or Django refuses to run with two leaf migrations

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
 && cp /src/docker/nginx-errors/weblate-starting.html \
       /src/docker/nginx-errors/weblate-timeout.html /etc/nginx/errors/ \
 && rm -rf /src
USER 1000
```

The `cp` replaces the image's own "Weblate is temporarily unavailable" and
"Weblate took too long to respond" pages, which its nginx serves on 502 and 504
without involving the application, with ours from `docker/nginx-errors/`. They
must stay self-contained: only `/static/` (served by nginx directly) is
reachable while they show. If an image upgrade renames or moves them, the `cp`
fails the build rather than silently shipping the Weblate pages.

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
