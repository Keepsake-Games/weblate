# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from weblate.glossary.models import get_glossary_terms
from weblate.trans.forms import ChecksumForm, TranslationForm
from weblate.trans.models import Suggestion, Translation, Vote
from weblate.trans.models.unit import fill_in_source_translation
from weblate.trans.util import render
from weblate.trans.views.edit import (
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
    from weblate.auth.models import AuthenticatedHttpRequest, User
    from weblate.trans.models import Project, Unit

PEON_PAGE_SIZE = 20
PEON_MODES = {"translate": "state:<translated", "review": "has:suggestion"}
PEON_REVIEW_ROUND = {"every": 10, "size": 3}
PEON_PATH_TYPES = (Translation, ProjectLanguage, CategoryLanguage)

SWIPE_ACTIONS = {"accept", "delete", "upvote", "downvote"}


def get_review_task(user: User, unit: Unit) -> dict[str, Any] | None:
    suggestion = next(
        (item for item in unit.suggestions if item.user_id != user.id), None
    )
    if suggestion is None:
        return None
    right = left = None
    if user.has_perm("suggestion.accept", unit):
        right = "accept"
        if user.has_perm("suggestion.delete", suggestion):
            left = "delete"
        elif user.has_perm("suggestion.vote", unit):
            left = "downvote"
    elif user.has_perm("suggestion.vote", unit):
        right, left = "upvote", "downvote"
    if right is None:
        return None
    return {"kind": "review", "suggestion": suggestion, "right": right, "left": left}


def get_translate_task(user: User, unit: Unit) -> dict[str, Any] | None:
    if user.has_perm("unit.edit", unit):
        return {"kind": "translate", "submit": "translate"}
    if user.has_perm("suggestion.add", unit):
        return {"kind": "translate", "submit": "suggest"}
    return None


def get_peon_task(user: User, unit: Unit, mode: str) -> dict[str, Any] | None:
    if unit.readonly:
        return None
    if mode == "review":
        return get_review_task(user, unit)
    return get_translate_task(user, unit)


def get_peon_targets(unit: Unit) -> list[dict[str, str]]:
    translation = unit.translation
    values = unit.get_target_plurals()
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
    search_result = SearchNavigation(
        obj, project, unit_set, request, blank=True
    ).page(include_count=include_count, page_size=PEON_PAGE_SIZE)
    if isinstance(search_result, HttpResponse):
        return search_result, None

    units = unit_set.prefetch_full().get_ordered(search_result["ids"])
    fill_in_source_translation(units)
    prepare_glossary_terms(units, project)

    tasks = []
    for pos, unit in enumerate(units):
        task = get_peon_task(request.user, unit, mode)
        if task is None:
            continue
        task.update(
            unit=unit,
            offset=search_result["offset"] + pos,
            form=TranslationForm(request.user, unit),
            glossary=get_glossary_terms(unit),
            targets=get_peon_targets(unit),
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


def count_reviewable(user: User, target: ProjectLanguage) -> int:
    """Count strings with a suggestion someone other than the user made."""
    return (
        Suggestion.objects.filter(
            unit__translation__component__project=target.project,
            unit__translation__language=target.language,
        )
        .exclude(user=user)
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
            "title": gettext("Translate now"),
            "hide_onboarding_banner": True,
        },
    )


def handle_peon_swipe(request: AuthenticatedHttpRequest, unit: Unit) -> bool:
    mode = request.POST.get("action", "")
    try:
        suggestion = Suggestion.objects.get(
            pk=int(request.POST.get("suggestion", "")), unit=unit
        )
    except (Suggestion.DoesNotExist, ValueError):
        messages.error(request, gettext("Invalid suggestion!"))
        return False

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


def handle_peon_translate(request: AuthenticatedHttpRequest, unit: Unit) -> bool:
    form = TranslationForm(request.user, unit, request.POST)
    if not form.is_valid():
        show_form_errors(request, form)
        return False
    if request.POST.get("action") == "suggest":
        return bool(perform_suggestion(unit, form, request))
    if not (edit_permission := request.user.has_perm("unit.edit", unit)):
        show_unit_edit_denied(request, edit_permission)
        return False
    return perform_translation(unit, form, request)


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

    if request.POST.get("action") in SWIPE_ACTIONS:
        done = handle_peon_swipe(request, unit)
    else:
        done = handle_peon_translate(request, unit)

    storage = get_messages(request)
    response = {
        "done": done,
        "translationsum": hash_to_checksum(unit.get_target_hash()),
        "messages": [
            {"kind": get_message_kind(m.tags), "text": m.message} for m in storage
        ],
    }
    return JsonResponse(data=response)
