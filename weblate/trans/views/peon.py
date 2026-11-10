# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from django.db import transaction
from django.db.models import Exists, F, OuterRef
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from weblate.glossary.models import get_glossary_terms
from weblate.trans.autofixes import fix_target
from weblate.trans.forms import ChecksumForm, TranslationForm
from weblate.trans.models import Suggestion, Translation, Unit, Vote
from weblate.trans.models.unit import fill_in_source_translation
from weblate.trans.util import join_plural, render
from weblate.trans.views.edit import (
    CachedSearchSnapshot,
    SearchNavigation,
    check_suggest_permissions,
    perform_suggestion,
    perform_translation,
    prepare_glossary_terms,
    show_unit_edit_denied,
)
from weblate.utils import messages
from weblate.utils.hash import hash_to_checksum
from weblate.utils.messages import get_message_kind
from weblate.utils.state import STATE_TRANSLATED
from weblate.utils.stats import CategoryLanguage, ProjectLanguage
from weblate.utils.views import parse_path_units, show_form_errors

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.db.models import QuerySet

    from weblate.auth.models import AuthenticatedHttpRequest, User
    from weblate.trans.models import Project

PEON_PAGE_SIZE = 20
PEON_MODES = {
    "translate": "state:<translated AND NOT has:suggestion",
    "review": "has:suggestion",
}
PEON_REVIEW_ROUND = {"every": 10, "size": 3}
PEON_PATH_TYPES = (Translation, ProjectLanguage, CategoryLanguage)
PEON_PICK_KEYS = 9

SWIPE_ACTIONS = {"accept", "delete", "upvote", "downvote"}
SINGLE_ACTIONS = {"accept", "upvote"}
VOTE_STATUS = {Vote.POSITIVE: "upvoted", Vote.NEGATIVE: "downvoted"}


def get_pending_suggestions(user: User) -> QuerySet[Suggestion]:
    """Return suggestions still waiting for the user's verdict."""
    return Suggestion.objects.exclude(user=user).exclude(vote__user=user)


class PeonNavigation(SearchNavigation):
    """
    Search snapshot private to Peon, rotated to a random start.

    People starting on the same queue at the same time then begin in different
    places instead of fighting over the same strings. In review mode the queue
    leaves out strings the reviewer already gave a verdict on.
    """

    def __init__(self, *args, review_user: User | None = None, **kwargs) -> None:
        self.review_user = review_user
        super().__init__(*args, **kwargs)

    @staticmethod
    def get_session_key(base, search_url: str) -> str:
        return f"search_peon_{base.cache_key}_{search_url}"

    def get_matching_full_snapshots(self) -> Iterator[CachedSearchSnapshot]:
        if not self.can_use_session_snapshot:
            return
        session_data = self.get_session_data(self.request.session, self.session_key)
        if session_data is None:
            return
        unit_ids = self._get_unit_ids(session_data)
        ttl = session_data.get("ttl")
        if unit_ids is not None and isinstance(ttl, int):
            yield CachedSearchSnapshot(self.session_key, session_data, unit_ids, ttl)

    def get_matching_units(self):
        units = self.unit_set.search(self.query_string, project=self.project)
        if self.review_user is not None:
            units = units.filter(
                Exists(
                    get_pending_suggestions(self.review_user).filter(
                        unit=OuterRef("pk")
                    )
                )
            )
        return units

    def get_ordered_units(self):
        return self.get_matching_units().order_by_request(self.cleaned_data, self.base)

    def get_ordered_unit_ids(self) -> list[int]:
        unit_ids = super().get_ordered_unit_ids()
        if not unit_ids:
            return unit_ids
        start = secrets.randbelow(len(unit_ids))
        return unit_ids[start:] + unit_ids[:start]

    def get_still_matching(self, unit_ids: list[int]) -> set[int]:
        """Return which snapshot IDs still match, others finished them since."""
        return set(
            self.get_matching_units()
            .filter(pk__in=unit_ids)
            .values_list("pk", flat=True)
        )


def get_submit(user: User, unit: Unit) -> str | None:
    if user.has_perm("unit.edit", unit):
        return "translate"
    if user.has_perm("suggestion.add", unit):
        return "suggest"
    return None


def suggests_only(user: User, project: Project) -> bool:
    """Tell whether suggestion voting turns the user's saves into suggestions."""
    if user.has_perm("unit.override", project):
        return False
    return project.component_set.filter(
        suggestion_voting=True, suggestion_autoaccept__gt=0
    ).exists()


def get_review_actions(
    user: User, unit: Unit, suggestion: Suggestion
) -> tuple[str | None, str | None]:
    """Return the agreeing and the disagreeing action open to the user."""
    if user.has_perm("suggestion.accept", unit):
        if user.has_perm("suggestion.delete", suggestion):
            return "accept", "delete"
        if user.has_perm("suggestion.vote", unit):
            return "accept", "downvote"
        return "accept", None
    if user.has_perm("suggestion.vote", unit):
        return "upvote", "downvote"
    return None, None


def get_counter_reject(user: User, unit: Unit, suggestion: Suggestion) -> str | None:
    """Return what a counter suggestion does to the suggestions it replaces."""
    if user.has_perm("suggestion.delete", suggestion):
        return "delete"
    if user.has_perm("suggestion.vote", unit):
        return "downvote"
    return None


def get_review_task(
    user: User, unit: Unit, votes: dict[int, int]
) -> dict[str, Any] | None:
    suggestions = list(unit.suggestions)
    pending = [
        item for item in suggestions if item.user_id != user.id and item.pk not in votes
    ]
    if not pending:
        return None
    right, left = get_review_actions(user, unit, pending[0])
    counter = get_submit(user, unit)
    if right is None and counter is None:
        return None
    task: dict[str, Any] = {
        "kind": "review",
        "suggestion": pending[0],
        "pending": pending,
        "right": right,
        "left": left,
        "counter": counter,
        "counter_reject": counter and get_counter_reject(user, unit, pending[0]),
        "choices": None,
    }
    if len(suggestions) > 1:
        keys = iter(range(1, PEON_PICK_KEYS + 1))
        choices = []
        for item in suggestions:
            pickable = right is not None and item in pending
            if item.user_id == user.id:
                status = "mine"
            else:
                status = VOTE_STATUS.get(votes.get(item.pk, 0))
            choices.append(
                {
                    "suggestion": item,
                    "pickable": pickable,
                    "key": next(keys, None) if pickable else None,
                    "status": status,
                }
            )
        task["choices"] = choices
    return task


def get_translate_task(user: User, unit: Unit) -> dict[str, Any] | None:
    if submit := get_submit(user, unit):
        return {"kind": "translate", "submit": submit}
    return None


def get_peon_targets(
    unit: Unit, values: list[str] | None = None
) -> list[dict[str, str]]:
    translation = unit.translation
    current = unit.get_target_plurals()
    values = current if values is None else values + [""] * (len(current) - len(values))
    labelled = len(values) > 1 and not translation.component.is_multivalue
    return [
        {
            "name": f"target_{idx}",
            "value": value,
            "label": translation.plural.get_plural_name(idx) if labelled else "",
        }
        for idx, value in enumerate(values)
    ]


def get_peon_tasks(obj, project, unit_set, request, mode, *, include_count=False):
    user = request.user
    navigation = PeonNavigation(
        obj,
        project,
        unit_set,
        request,
        blank=True,
        review_user=user if mode == "review" else None,
    )
    search_result = navigation.page(
        include_count=include_count, page_size=PEON_PAGE_SIZE
    )
    if isinstance(search_result, HttpResponse):
        return search_result, None

    offsets = {
        pk: search_result["offset"] + pos for pos, pk in enumerate(search_result["ids"])
    }
    matching = navigation.get_still_matching(search_result["ids"])
    units = unit_set.prefetch_full().get_ordered(
        [pk for pk in search_result["ids"] if pk in matching]
    )
    fill_in_source_translation(units)
    prepare_glossary_terms(units, project)

    votes: dict[int, int] = {}
    if mode == "review":
        votes = dict(
            Vote.objects.filter(
                user=user, suggestion__unit__in=[unit.pk for unit in units]
            ).values_list("suggestion_id", "value")
        )

    tasks = []
    for unit in units:
        if unit.readonly:
            continue
        if mode == "review":
            task = get_review_task(user, unit, votes)
        else:
            task = get_translate_task(user, unit)
        if task is None:
            continue
        suggestion = task.get("suggestion")
        task.update(
            unit=unit,
            offset=offsets[unit.pk],
            form=TranslationForm(user, unit),
            glossary=get_glossary_terms(unit),
            targets=get_peon_targets(
                unit, suggestion.target_list if suggestion else None
            ),
        )
        tasks.append(task)
    return search_result, tasks


def get_peon_mode(request: AuthenticatedHttpRequest) -> str:
    """Return the mode and default the search to that mode's queue."""
    mode = request.GET.get("mode", "")
    if mode not in PEON_MODES:
        mode = "translate"
    if not request.GET.get("q"):
        query = request.GET.copy()
        query["q"] = PEON_MODES[mode]
        request.GET = query
    return mode


def peon(request: AuthenticatedHttpRequest, path):
    obj, unit_set, context = parse_path_units(request, path, PEON_PATH_TYPES)
    if isinstance(obj, Translation) and obj.is_source:
        return redirect("translate", path=obj.get_url_path())
    project = context["project"]

    mode = get_peon_mode(request)
    search_result, tasks = get_peon_tasks(
        obj, project, unit_set, request, mode, include_count=True
    )
    if isinstance(search_result, HttpResponse):
        return search_result

    return render(
        request,
        "peon.html",
        {
            "object": obj,
            "path_object": obj,
            "project": project,
            "component": obj.component if isinstance(obj, Translation) else None,
            "mode": mode,
            "suggesting": suggests_only(request.user, project),
            "tasks": tasks,
            "search_url": search_result["url"],
            "review_query": urlencode({"mode": "review", "q": PEON_MODES["review"]}),
            "review_round": PEON_REVIEW_ROUND,
            "filter_count": search_result["total"],
            "offset": search_result["offset"],
            "last_section": search_result["last_section"],
            "hide_onboarding_banner": True,
        },
    )


def load_peon(request: AuthenticatedHttpRequest, path):
    obj, unit_set, context = parse_path_units(request, path, PEON_PATH_TYPES)
    mode = get_peon_mode(request)
    search_result, tasks = get_peon_tasks(
        obj, context["project"], unit_set, request, mode
    )
    if isinstance(search_result, HttpResponse):
        return search_result

    return render(
        request,
        "peon-tasks.html",
        {
            "object": obj,
            "tasks": tasks,
            "search_url": search_result["url"],
            "last_section": search_result["last_section"],
        },
    )


def get_start_projects(user: User) -> list[Project]:
    projects = user.allowed_projects
    if isinstance(settings.SINGLE_PROJECT, str) and settings.SINGLE_PROJECT:
        return list(projects.filter(slug=settings.SINGLE_PROJECT))
    watched = projects.filter(pk__in=user.profile.watched.values("pk"))
    return list(watched if watched.exists() else projects.order())


def count_translatable(target: ProjectLanguage) -> int:
    """Count strings in the translate queue: not translated, nothing suggested."""
    return (
        Unit.objects.filter(
            translation__component__project=target.project,
            translation__language=target.language,
            state__lt=STATE_TRANSLATED,
        )
        .exclude(translation__language=F("translation__component__source_language"))
        .filter(~Exists(Suggestion.objects.filter(unit=OuterRef("pk"))))
        .count()
    )


def count_reviewable(user: User, target: ProjectLanguage) -> int:
    """Count strings with a suggestion still waiting for the user's verdict."""
    return (
        get_pending_suggestions(user)
        .filter(
            unit__translation__component__project=target.project,
            unit__translation__language=target.language,
        )
        .values("unit")
        .distinct()
        .count()
    )


@login_required
def peon_start(request: AuthenticatedHttpRequest):
    profile = request.user.profile
    if not profile.onboarded:
        query = urlencode({"next": request.get_full_path()})
        return redirect(f"{reverse('onboarding')}?{query}")

    projects = get_start_projects(request.user)
    project = next(
        (item for item in projects if item.slug == request.GET.get("project")),
        projects[0] if projects else None,
    )
    targets = []
    if project is not None:
        languages = profile.languages.filter(
            translation__component__project=project
        ).distinct()
        targets = [ProjectLanguage(project, language) for language in languages]
        for item in targets:
            item.translatable = count_translatable(item)
            item.reviewable = count_reviewable(request.user, item)
    target = next(
        (item for item in targets if item.language.code == request.GET.get("lang")),
        targets[0] if targets else None,
    )

    mode = request.GET.get("mode")
    if (
        target is not None
        and mode in PEON_MODES
        and (mode != "review" or target.reviewable)
    ):
        url = reverse("peon", kwargs={"path": target.get_url_path()})
        return redirect(f"{url}?{urlencode({'mode': mode})}")

    return render(
        request,
        "peon-start.html",
        {
            "projects": projects,
            "project": project,
            "targets": targets,
            "target": target,
            "suggesting": project is not None and suggests_only(request.user, project),
            "title": gettext("Translate now"),
            "hide_onboarding_banner": True,
        },
    )


def get_posted_suggestions(
    request: AuthenticatedHttpRequest, unit: Unit
) -> list[Suggestion] | None:
    try:
        pks = {int(pk) for pk in request.POST.getlist("suggestion")}
    except ValueError:
        pks = set()
    suggestions = list(Suggestion.objects.filter(unit=unit, pk__in=pks))
    if not pks or len(suggestions) != len(pks):
        messages.error(request, gettext("Invalid suggestion!"))
        return None
    return suggestions


def find_same_suggestion(unit: Unit, target: list[str]) -> Suggestion | None:
    """Find an existing suggestion with the text, as Weblate would store it."""
    if not unit.translation.is_template:
        target, _fixups = fix_target(target, unit)
    return Suggestion.objects.filter(unit=unit, target=join_plural(target)).first()


def apply_review(
    request: AuthenticatedHttpRequest, unit: Unit, suggestion: Suggestion, mode: str
) -> bool:
    if not check_suggest_permissions(request, mode, unit, suggestion):
        return False

    if mode == "accept":
        suggestion.accept(request, state=STATE_TRANSLATED)
    elif mode == "delete":
        suggestion.delete_log(request.user, old=unit.target)
    elif mode == "upvote":
        suggestion.add_vote(request, Vote.POSITIVE)
    else:
        suggestion.add_vote(request, Vote.NEGATIVE)
    return True


def handle_peon_swipe(request: AuthenticatedHttpRequest, unit: Unit) -> bool:
    action = request.POST.get("action", "")
    if (suggestions := get_posted_suggestions(request, unit)) is None:
        return False
    if action in SINGLE_ACTIONS and len(suggestions) != 1:
        messages.error(request, gettext("Invalid suggestion!"))
        return False
    results = [
        apply_review(request, unit, suggestion, action) for suggestion in suggestions
    ]
    return all(results)


def get_translation_form(
    request: AuthenticatedHttpRequest, unit: Unit
) -> TranslationForm | None:
    form = TranslationForm(request.user, unit, request.POST)
    if not form.is_valid():
        show_form_errors(request, form)
        return None
    return form


def save_translation(
    request: AuthenticatedHttpRequest,
    unit: Unit,
    form: TranslationForm,
    submit: str | None,
) -> bool:
    user = request.user
    if submit != "translate":
        same = find_same_suggestion(unit, form.cleaned_data["target"])
        if (
            same is not None
            and same.user != user
            and user.has_perm("suggestion.vote", unit)
        ):
            same.add_vote(request, Vote.POSITIVE)
            messages.info(
                request,
                gettext(
                    "Someone already suggested exactly this, so it counts as your vote."
                ),
            )
            return True
        return bool(perform_suggestion(unit, form, request))
    if not (edit_permission := user.has_perm("unit.edit", unit)):
        show_unit_edit_denied(request, edit_permission)
        return False
    return perform_translation(unit, form, request)


def handle_peon_translate(
    request: AuthenticatedHttpRequest, unit: Unit, submit: str | None
) -> bool:
    if (form := get_translation_form(request, unit)) is None:
        return False
    return save_translation(request, unit, form, submit)


def handle_peon_counter(request: AuthenticatedHttpRequest, unit: Unit) -> bool:
    """Replace the pending suggestions with the user's own wording."""
    user = request.user
    if (suggestions := get_posted_suggestions(request, unit)) is None:
        return False
    if (form := get_translation_form(request, unit)) is None:
        return False

    same = find_same_suggestion(unit, form.cleaned_data["target"])
    if same is not None and same in suggestions:
        # Writing out one of the pending suggestions is agreeing with it
        right, _left = get_review_actions(user, unit, same)
        if right is None:
            messages.error(request, gettext("Your suggestion already exists!"))
            return False
        return apply_review(request, unit, same, right)

    if not save_translation(request, unit, form, get_submit(user, unit)):
        return False
    if reject := get_counter_reject(user, unit, suggestions[0]):
        for suggestion in suggestions:
            if Suggestion.objects.filter(pk=suggestion.pk).exists():
                apply_review(request, unit, suggestion, reject)
    return True


@login_required
@require_POST
@transaction.atomic
def save_peon(request: AuthenticatedHttpRequest, path):
    _obj, unit_set, _context = parse_path_units(request, path, PEON_PATH_TYPES)

    checksum_form = ChecksumForm(unit_set, request.POST)
    if not checksum_form.is_valid():
        show_form_errors(request, checksum_form)
        return HttpResponseBadRequest("Invalid checksum")
    unit = checksum_form.cleaned_data["unit"]

    action = request.POST.get("action", "")
    if action in SWIPE_ACTIONS:
        done = handle_peon_swipe(request, unit)
    elif action == "counter":
        done = handle_peon_counter(request, unit)
    else:
        done = handle_peon_translate(request, unit, action)

    storage = get_messages(request)
    response = {
        "done": done,
        "translationsum": hash_to_checksum(unit.get_target_hash()),
        "messages": [
            {"kind": get_message_kind(m.tags), "text": m.message} for m in storage
        ],
    }
    return JsonResponse(data=response)
