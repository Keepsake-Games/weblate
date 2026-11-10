# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from django.core.management.base import CommandError
from django.db import transaction

from weblate.auth.models import User
from weblate.trans.actions import ActionEvents
from weblate.trans.models import Suggestion
from weblate.trans.util import split_plural
from weblate.utils.management.base import BaseCommand
from weblate.utils.state import STATE_FUZZY

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

    from weblate.trans.models import Unit

DRAFTED = "turned into a draft"
SAME_DRAFT = "already the draft, suggestion removed"
DUPLICATE = "older duplicate, suggestion removed"
TRANSLATED = "kept: string already translated"
OTHER_DRAFT = "kept: string has a different draft"
VOTED = "kept: someone else voted on it"
READONLY = "kept: string is read-only"


class Command(BaseCommand):
    """Turn one account's suggestions into drafts marked as needing editing."""

    help = (
        "turns suggestions by one account into 'Needs editing' drafts on strings "
        "nobody has translated; dry run unless --apply is given"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--user", required=True, help="Username whose suggestions to convert"
        )
        parser.add_argument("--project", help="Limit to one project slug")
        parser.add_argument(
            "--apply", action="store_true", help="Make the changes instead of listing"
        )

    def handle(self, *args, **options) -> None:
        try:
            user = User.objects.get(username=options["user"])
        except User.DoesNotExist as error:
            msg = f"No such user: {options['user']}"
            raise CommandError(msg) from error

        suggestions = Suggestion.objects.filter(user=user).select_related(
            "unit__translation__component__project", "unit__translation__language"
        )
        if options["project"]:
            suggestions = suggestions.filter(
                unit__translation__component__project__slug=options["project"]
            )

        by_unit: dict[int, list[Suggestion]] = {}
        for suggestion in suggestions.order_by("-timestamp"):
            by_unit.setdefault(suggestion.unit_id, []).append(suggestion)

        outcomes: Counter[str] = Counter()
        for items in by_unit.values():
            newest, *older = items
            outcome = self.convert(user, newest.unit, newest, apply=options["apply"])
            outcomes[outcome] += 1
            if outcome in {DRAFTED, SAME_DRAFT}:
                for suggestion in older:
                    if options["apply"]:
                        suggestion.delete_log(user, rejection_reason=DUPLICATE)
                    outcomes[DUPLICATE] += 1
            if options["verbosity"] > 1:
                unit = newest.unit
                self.stdout.write(
                    f"{unit.translation} {unit.context or unit.pk}: {outcome}"
                )

        mode = "Changed" if options["apply"] else "Would change (dry run, pass --apply)"
        self.stdout.write(f"{mode}, {len(by_unit)} string(s) with suggestions:")
        for outcome, count in sorted(outcomes.items()):
            self.stdout.write(f"  {count:6d}  {outcome}")

    def convert(
        self, user: User, unit: Unit, suggestion: Suggestion, *, apply: bool
    ) -> str:
        if unit.readonly:
            return READONLY
        if unit.translated:
            return TRANSLATED
        if suggestion.vote_set.exclude(user=user).exists():
            return VOTED
        if unit.target and unit.target != suggestion.target:
            return OTHER_DRAFT
        if unit.target == suggestion.target:
            if apply:
                suggestion.delete_log(user, rejection_reason=SAME_DRAFT)
            return SAME_DRAFT
        if apply:
            with transaction.atomic():
                unit.translate(
                    user,
                    split_plural(suggestion.target),
                    STATE_FUZZY,
                    change_action=ActionEvents.AUTO,
                    author=user,
                    propagate=False,
                )
                suggestion.delete_log(user, rejection_reason=DRAFTED)
        return DRAFTED
