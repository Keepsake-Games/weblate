# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.urls import reverse

from weblate.trans.actions import ActionEvents
from weblate.trans.models import Project, Suggestion, Vote
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.tests.utils import create_another_user
from weblate.utils.hash import hash_to_checksum
from weblate.utils.state import STATE_FUZZY, STATE_TRANSLATED


class PeonViewTest(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.rotation = self.patch_rotation(0)

    def patch_rotation(self, start: int):
        patcher = mock.patch(
            "weblate.trans.views.peon.secrets.randbelow", return_value=start
        )
        self.addCleanup(patcher.stop)
        return patcher.start()

    def make_translator(self) -> None:
        """Limit the user to the Translate role, which can not delete suggestions."""
        self.project.access_control = Project.ACCESS_PROTECTED
        self.project.save()
        self.project.add_user(self.user, "Translate")

    def get_card_units(self, response) -> list[str]:
        return [
            task["unit"].source.split("\n")[0] for task in response.context["tasks"]
        ]

    def get_params(self, unit, **kwargs):
        params = {
            "checksum": unit.checksum,
            "contentsum": hash_to_checksum(unit.content_hash),
            "translationsum": hash_to_checksum(unit.get_target_hash()),
        }
        params.update(kwargs)
        return params

    def save(self, **params):
        return self.client.post(
            reverse("save_peon", kwargs=self.kw_translation), params
        ).json()

    def add_foreign_suggestion(self, unit, target="Nazdar svete!\n"):
        return Suggestion.objects.create(
            unit=unit, target=target, user=self.anotheruser
        )

    def test_peon(self) -> None:
        response = self.client.get(reverse("peon", kwargs=self.kw_translation))
        self.assertContains(response, 'id="peon"')
        self.assertContains(response, "Thank you for using Weblate.")
        self.assertContains(response, 'data-kind="translate"')
        self.assertContains(response, "data-last-section")

    def test_peon_skips_translated(self) -> None:
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")
        response = self.client.get(reverse("peon", kwargs=self.kw_translation))
        self.assertNotContains(response, "Hello, world!")
        self.assertContains(response, "Thank you for using Weblate.")

    def test_peon_source_redirects(self) -> None:
        response = self.client.get(
            reverse(
                "peon",
                kwargs={"path": self.component.source_translation.get_url_path()},
            )
        )
        self.assertRedirects(
            response,
            reverse(
                "translate",
                kwargs={"path": self.component.source_translation.get_url_path()},
            ),
        )

    def test_load_peon(self) -> None:
        response = self.client.get(
            reverse("load_peon", kwargs=self.kw_translation), {"offset": "2"}
        )
        self.assertNotContains(response, "Hello, world!")
        self.assertContains(response, 'class="peon-end"')

    def test_peon_review_card(self) -> None:
        self.make_manager()
        self.edit_unit("Hello, world!\n", "Ahoj svete!\n")
        unit = self.get_unit()
        suggestion = self.add_foreign_suggestion(unit)
        response = self.client.get(
            reverse("peon", kwargs=self.kw_translation), {"mode": "review"}
        )
        self.assertContains(response, 'data-kind="review"')
        self.assertContains(response, f'data-suggestion="{suggestion.pk}"')
        self.assertContains(response, 'data-right="accept"')
        self.assertContains(response, 'data-left="delete"')

    def test_translate_mode_has_review_rounds(self) -> None:
        self.make_manager()
        unit = self.get_unit()
        self.add_foreign_suggestion(unit)
        response = self.client.get(reverse("peon", kwargs=self.kw_translation))
        self.assertNotContains(response, 'data-kind="review"')
        self.assertContains(response, "data-review-url=")

        response = self.client.get(
            reverse("load_peon", kwargs=self.kw_translation),
            {"mode": "review", "q": "has:suggestion"},
        )
        self.assertContains(response, 'data-kind="review"')
        self.assertContains(response, f'data-unit="{unit.pk}"')

    def test_review_mode_has_no_review_rounds(self) -> None:
        response = self.client.get(
            reverse("peon", kwargs=self.kw_translation), {"mode": "review"}
        )
        self.assertNotContains(response, "data-review-url=")

    def test_peon_own_suggestion_is_not_reviewed(self) -> None:
        self.make_manager()
        unit = self.get_unit()
        Suggestion.objects.create(unit=unit, target="Mine!\n", user=self.user)
        response = self.client.get(
            reverse("peon", kwargs=self.kw_translation), {"mode": "review"}
        )
        self.assertNotContains(response, 'data-kind="review"')

    def test_save_translate(self) -> None:
        unit = self.get_unit()
        result = self.save(
            **self.get_params(unit, target_0="Nazdar svete!\n", action="translate")
        )
        self.assertTrue(result["done"])
        unit = self.get_unit()
        self.assertEqual(unit.target, "Nazdar svete!\n")
        self.assertEqual(unit.state, STATE_TRANSLATED)
        self.assertEqual(
            result["translationsum"], hash_to_checksum(unit.get_target_hash())
        )

    def test_save_suggest(self) -> None:
        unit = self.get_unit()
        result = self.save(
            **self.get_params(unit, target_0="Nazdar svete!\n", action="suggest")
        )
        self.assertTrue(result["done"])
        self.assertEqual(self.get_unit().suggestions.count(), 1)
        self.assertEqual(self.get_unit().target, "")

    def test_save_locked(self) -> None:
        self.component.locked = True
        self.component.save()
        unit = self.get_unit()
        result = self.save(
            **self.get_params(unit, target_0="Nazdar svete!\n", action="translate")
        )
        self.assertFalse(result["done"])
        self.assertIn(
            "This translation is currently locked.",
            [message["text"] for message in result["messages"]],
        )

    def test_save_accept(self) -> None:
        self.make_manager()
        unit = self.get_unit()
        suggestion = self.add_foreign_suggestion(unit)
        result = self.save(
            **self.get_params(unit, action="accept", suggestion=suggestion.pk)
        )
        self.assertTrue(result["done"])
        unit = self.get_unit()
        self.assertEqual(unit.target, "Nazdar svete!\n")
        self.assertFalse(unit.suggestions.exists())

    def test_save_delete(self) -> None:
        self.make_manager()
        unit = self.get_unit()
        suggestion = self.add_foreign_suggestion(unit)
        result = self.save(
            **self.get_params(unit, action="delete", suggestion=suggestion.pk)
        )
        self.assertTrue(result["done"])
        unit = self.get_unit()
        self.assertEqual(unit.target, "")
        self.assertFalse(unit.suggestions.exists())

    def test_voting_turns_review_into_votes(self) -> None:
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 2
        self.component.save()
        unit = self.get_unit()
        suggestion = self.add_foreign_suggestion(unit)

        response = self.client.get(
            reverse("peon", kwargs=self.kw_translation), {"mode": "review"}
        )
        self.assertContains(response, 'data-right="upvote"')
        self.assertContains(response, 'data-left="downvote"')

        result = self.save(
            **self.get_params(unit, action="accept", suggestion=suggestion.pk)
        )
        self.assertFalse(result["done"])
        self.assertTrue(self.get_unit().suggestions.exists())

    def test_start_is_rotated(self) -> None:
        url = reverse("peon", kwargs=self.kw_translation)
        first = self.get_card_units(self.client.get(url))
        self.assertGreater(len(first), 1)

        self.rotation.return_value = 1
        rotated = self.get_card_units(self.client.get(url))
        self.assertEqual(rotated, first[1:] + first[:1])

    def test_rotation_is_kept_while_paging(self) -> None:
        self.rotation.return_value = 1
        started = self.get_card_units(
            self.client.get(reverse("peon", kwargs=self.kw_translation))
        )
        self.rotation.return_value = 0
        response = self.client.get(
            reverse("load_peon", kwargs=self.kw_translation), {"offset": "1"}
        )
        self.assertEqual(self.get_card_units(response), started)
        self.rotation.assert_called_once()

    def test_peon_snapshot_is_separate_from_editor(self) -> None:
        self.client.get(reverse("peon", kwargs=self.kw_translation))
        session = self.client.session
        keys = [key for key in session.keys() if key.startswith("search_")]  # noqa: SIM118
        self.assertTrue(keys)
        self.assertTrue(all(key.startswith("search_peon_") for key in keys))

    def test_drops_units_finished_since_snapshot(self) -> None:
        self.client.get(reverse("peon", kwargs=self.kw_translation))
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")
        response = self.client.get(
            reverse("load_peon", kwargs=self.kw_translation), {"offset": "1"}
        )
        self.assertNotIn("Hello, world!", self.get_card_units(response))
        self.assertTrue(response.context["tasks"])

    def test_full_editor_link_uses_checksum(self) -> None:
        unit = self.get_unit()
        response = self.client.get(reverse("peon", kwargs=self.kw_translation))
        self.assertContains(response, f"checksum={unit.checksum}")

    def test_review_card_offers_counter(self) -> None:
        self.make_manager()
        self.edit_unit("Hello, world!\n", "Ahoj svete!\n")
        self.add_foreign_suggestion(self.get_unit())
        response = self.client.get(
            reverse("peon", kwargs=self.kw_translation), {"mode": "review"}
        )
        self.assertContains(response, 'data-counter="translate"')
        self.assertContains(response, "Saving rejects their suggestion.")
        self.assertContains(response, ">Nazdar svete!\n</textarea>")

    def test_counter_without_delete_keeps_suggestion(self) -> None:
        self.make_translator()
        unit = self.get_unit()
        self.add_foreign_suggestion(unit)
        response = self.client.get(
            reverse("peon", kwargs=self.kw_translation), {"mode": "review"}
        )
        self.assertContains(response, 'data-counter="translate"')
        self.assertNotContains(response, "data-left=")
        self.assertContains(response, "The suggestions stay up for others to review.")

    def test_save_counter_replaces_suggestion(self) -> None:
        self.make_manager()
        unit = self.get_unit()
        suggestion = self.add_foreign_suggestion(unit)
        result = self.save(
            **self.get_params(
                unit,
                target_0="Ahoj svete!\n",
                action="counter",
                suggestion=suggestion.pk,
            )
        )
        self.assertTrue(result["done"])
        unit = self.get_unit()
        self.assertEqual(unit.target, "Ahoj svete!\n")
        self.assertFalse(unit.suggestions.exists())

    def test_save_counter_without_delete_permission(self) -> None:
        self.make_translator()
        unit = self.get_unit()
        suggestion = self.add_foreign_suggestion(unit)
        result = self.save(
            **self.get_params(
                unit,
                target_0="Ahoj svete!\n",
                action="counter",
                suggestion=suggestion.pk,
            )
        )
        self.assertTrue(result["done"])
        unit = self.get_unit()
        self.assertEqual(unit.target, "Ahoj svete!\n")
        self.assertTrue(unit.suggestions.filter(pk=suggestion.pk).exists())

    def test_save_counter_with_voting_suggests_and_downvotes(self) -> None:
        self.make_translator()
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 2
        self.component.save()
        unit = self.get_unit()
        suggestion = self.add_foreign_suggestion(unit)
        result = self.save(
            **self.get_params(
                unit,
                target_0="Ahoj svete!\n",
                action="counter",
                suggestion=suggestion.pk,
            )
        )
        self.assertTrue(result["done"])
        unit = self.get_unit()
        self.assertEqual(unit.target, "")
        self.assertEqual(
            sorted(unit.suggestions.values_list("target", flat=True)),
            ["Ahoj svete!\n", "Nazdar svete!\n"],
        )
        self.assertEqual(
            Vote.objects.get(suggestion=suggestion, user=self.user).value,
            Vote.NEGATIVE,
        )

    def test_save_counter_failure_keeps_suggestion(self) -> None:
        self.make_manager()
        unit = self.get_unit()
        suggestion = self.add_foreign_suggestion(unit)
        result = self.save(
            **self.get_params(
                unit,
                target_0="Ahoj svete!\n",
                translationsum="0000000000000000",
                action="counter",
                suggestion=suggestion.pk,
            )
        )
        self.assertFalse(result["done"])
        self.assertTrue(Suggestion.objects.filter(pk=suggestion.pk).exists())
        self.assertEqual(self.get_unit().target, "")

    def enable_voting(self) -> None:
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 2
        self.component.save()

    def add_third_suggestion(self, unit, target="Ahoj svete!\n"):
        return Suggestion.objects.create(
            unit=unit, target=target, user=create_another_user("3")
        )

    def get_review(self):
        return self.client.get(
            reverse("peon", kwargs=self.kw_translation), {"mode": "review"}
        )

    def test_translate_queue_skips_suggested(self) -> None:
        self.add_foreign_suggestion(self.get_unit())
        response = self.client.get(reverse("peon", kwargs=self.kw_translation))
        self.assertNotIn("Hello, world!", self.get_card_units(response))
        self.assertTrue(response.context["tasks"])

    def test_review_queue_skips_voted(self) -> None:
        self.make_translator()
        self.enable_voting()
        unit = self.get_unit()
        suggestion = self.add_foreign_suggestion(unit)
        self.assertContains(self.get_review(), 'data-kind="review"')

        Vote.objects.create(suggestion=suggestion, user=self.user, value=-1)
        response = self.get_review()
        self.assertNotContains(response, 'data-kind="review"')
        self.assertEqual(response.context["filter_count"], 0)

    def test_review_queue_skips_own(self) -> None:
        Suggestion.objects.create(
            unit=self.get_unit(), target="Mine!\n", user=self.user
        )
        self.assertEqual(self.get_review().context["filter_count"], 0)

    def test_several_suggestions_make_a_choice_card(self) -> None:
        self.make_translator()
        self.enable_voting()
        unit = self.get_unit()
        first = self.add_foreign_suggestion(unit)
        second = self.add_third_suggestion(unit)
        Suggestion.objects.create(unit=unit, target="Mine!\n", user=self.user)
        response = self.get_review()
        self.assertContains(response, "data-multi")
        self.assertContains(response, "Pick the best suggestion")
        self.assertContains(response, 'data-key="1"')
        self.assertContains(response, 'data-key="2"')
        self.assertNotContains(response, 'data-key="3"')
        self.assertContains(response, "Yours")
        for item in (first, second):
            self.assertContains(
                response, f'name="suggestion" value="{item.pk}"', count=1
            )
        self.assertContains(response, "Saving downvotes the 2 suggestions")

    def test_choice_card_marks_votes(self) -> None:
        self.make_translator()
        self.enable_voting()
        unit = self.get_unit()
        first = self.add_foreign_suggestion(unit)
        self.add_third_suggestion(unit)
        Vote.objects.create(suggestion=first, user=self.user, value=-1)
        response = self.get_review()
        self.assertContains(response, "You downvoted")
        self.assertContains(response, 'data-key="1"')
        self.assertNotContains(response, 'data-key="2"')
        self.assertNotContains(response, f'name="suggestion" value="{first.pk}"')

    def test_save_none_of_these(self) -> None:
        self.make_manager()
        unit = self.get_unit()
        first = self.add_foreign_suggestion(unit)
        second = self.add_third_suggestion(unit)
        result = self.save(
            **self.get_params(unit, action="delete", suggestion=[first.pk, second.pk])
        )
        self.assertTrue(result["done"])
        self.assertFalse(self.get_unit().suggestions.exists())

    def test_save_accept_needs_one_suggestion(self) -> None:
        self.make_manager()
        unit = self.get_unit()
        first = self.add_foreign_suggestion(unit)
        second = self.add_third_suggestion(unit)
        result = self.save(
            **self.get_params(unit, action="accept", suggestion=[first.pk, second.pk])
        )
        self.assertFalse(result["done"])
        self.assertEqual(self.get_unit().suggestions.count(), 2)

    def test_save_counter_matching_pending_agrees(self) -> None:
        self.make_manager()
        unit = self.get_unit()
        first = self.add_foreign_suggestion(unit)
        second = self.add_third_suggestion(unit)
        result = self.save(
            **self.get_params(
                unit,
                target_0="Ahoj svete!\n",
                action="counter",
                suggestion=[first.pk, second.pk],
            )
        )
        self.assertTrue(result["done"])
        unit = self.get_unit()
        self.assertEqual(unit.target, "Ahoj svete!\n")
        self.assertEqual(
            list(unit.suggestions.values_list("pk", flat=True)), [first.pk]
        )

    def test_save_counter_replaces_all_pending(self) -> None:
        self.make_manager()
        unit = self.get_unit()
        first = self.add_foreign_suggestion(unit)
        second = self.add_third_suggestion(unit)
        result = self.save(
            **self.get_params(
                unit,
                target_0="Zdravim svete!\n",
                action="counter",
                suggestion=[first.pk, second.pk],
            )
        )
        self.assertTrue(result["done"])
        unit = self.get_unit()
        self.assertEqual(unit.target, "Zdravim svete!\n")
        self.assertFalse(unit.suggestions.exists())

    def test_suggesting_existing_text_counts_as_vote(self) -> None:
        self.make_translator()
        self.enable_voting()
        unit = self.get_unit()
        suggestion = self.add_foreign_suggestion(unit)
        result = self.save(
            **self.get_params(unit, target_0="Nazdar svete!\n", action="suggest")
        )
        self.assertTrue(result["done"])
        self.assertEqual(self.get_unit().suggestions.count(), 1)
        self.assertEqual(
            Vote.objects.get(suggestion=suggestion, user=self.user).value,
            Vote.POSITIVE,
        )

    def test_draft_is_offered_for_improvement(self) -> None:
        self.make_translator()
        self.enable_voting()
        unit = self.get_unit()
        unit.translate(self.user, "Nazdar svete!\n", STATE_FUZZY)
        response = self.client.get(reverse("peon", kwargs=self.kw_translation))
        self.assertIn("Hello, world!", self.get_card_units(response))
        self.assertContains(response, "Improve this draft")
        self.assertContains(response, ">Nazdar svete!\n</textarea>")

        result = self.save(
            **self.get_params(
                self.get_unit(), target_0="Ahoj svete!\n", action="suggest"
            )
        )
        self.assertTrue(result["done"])
        unit = self.get_unit()
        self.assertTrue(unit.fuzzy)
        self.assertEqual(unit.target, "Nazdar svete!\n")
        response = self.client.get(reverse("peon", kwargs=self.kw_translation))
        self.assertNotIn("Hello, world!", self.get_card_units(response))

    def test_hud_says_suggesting_with_voting(self) -> None:
        url = reverse("peon", kwargs=self.kw_translation)
        self.assertContains(self.client.get(url), "Translating")
        self.make_translator()
        self.enable_voting()
        response = self.client.get(url)
        self.assertContains(response, "Suggesting")
        self.assertNotContains(response, "Translating")

    def test_save_invalid_suggestion(self) -> None:
        unit = self.get_unit()
        result = self.save(**self.get_params(unit, action="accept", suggestion="x"))
        self.assertFalse(result["done"])

    def test_save_invalid_checksum(self) -> None:
        response = self.client.post(
            reverse("save_peon", kwargs=self.kw_translation),
            {"checksum": "0000000000000000", "action": "translate"},
        )
        self.assertEqual(response.status_code, 400)


class SuggestionsToDraftsTest(ViewTestCase):
    def run_command(self, *args: str) -> str:
        output = StringIO()
        call_command(
            "suggestions_to_drafts", "--user", "testuser", *args, stdout=output
        )
        return output.getvalue()

    def add(self, unit, target, user=None):
        return Suggestion.objects.create(
            unit=unit, target=target, user=user or self.user
        )

    def test_dry_run_changes_nothing(self) -> None:
        self.add(self.get_unit(), "Nazdar svete!\n")
        output = self.run_command()
        self.assertIn("dry run", output)
        self.assertIn("1  turned into a draft", output)
        unit = self.get_unit()
        self.assertEqual(unit.target, "")
        self.assertEqual(unit.suggestions.count(), 1)

    def test_apply_makes_draft(self) -> None:
        self.add(self.get_unit(), "Old!\n")
        self.add(self.get_unit(), "Nazdar svete!\n")
        output = self.run_command("--apply")
        self.assertIn("1  turned into a draft", output)
        self.assertIn("1  older duplicate", output)
        unit = self.get_unit()
        self.assertEqual(unit.target, "Nazdar svete!\n")
        self.assertTrue(unit.fuzzy)
        self.assertTrue(unit.automatically_translated)
        self.assertFalse(unit.suggestions.exists())
        self.assertFalse(
            unit.change_set.filter(
                action__in=[ActionEvents.CHANGE, ActionEvents.NEW, ActionEvents.ACCEPT]
            ).exists()
        )

    def test_keeps_translated_and_voted(self) -> None:
        self.edit_unit("Hello, world!\n", "Ahoj svete!\n")
        self.add(self.get_unit(), "Nazdar svete!\n")
        other = self.get_unit("Thank you for using Weblate.")
        suggestion = self.add(other, "Diky!\n")
        Vote.objects.create(suggestion=suggestion, user=self.anotheruser, value=1)
        output = self.run_command("--apply")
        self.assertIn("1  kept: string already translated", output)
        self.assertIn("1  kept: someone else voted on it", output)
        self.assertEqual(Suggestion.objects.count(), 2)

    def test_leaves_other_users_alone(self) -> None:
        self.add(self.get_unit(), "Nazdar svete!\n", user=self.anotheruser)
        self.run_command("--apply")
        unit = self.get_unit()
        self.assertEqual(unit.target, "")
        self.assertEqual(unit.suggestions.count(), 1)


class PeonStartTest(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.profile = self.user.profile
        self.profile.languages.set([self.translation.language])

    def set_onboarded(self, value: bool) -> None:
        self.profile.onboarded = value
        self.profile.save(update_fields=["onboarded"])

    def test_requires_onboarding(self) -> None:
        self.set_onboarded(False)
        response = self.client.get(reverse("peon-start"))
        self.assertRedirects(
            response,
            f"{reverse('onboarding')}?next=%2Fpeon%2F",
            fetch_redirect_response=False,
        )

    def test_pick_job(self) -> None:
        self.set_onboarded(True)
        response = self.client.get(reverse("peon-start"))
        self.assertContains(response, 'value="translate"')
        self.assertContains(response, 'value="review"')

    def test_inspect_needs_foreign_suggestions(self) -> None:
        self.set_onboarded(True)
        unit = self.get_unit()
        Suggestion.objects.create(unit=unit, target="Mine!\n", user=self.user)
        response = self.client.get(reverse("peon-start"))
        self.assertContains(response, "Nothing to inspect right now")
        response = self.client.get(reverse("peon-start"), {"mode": "review"})
        self.assertEqual(response.status_code, 200)

        Suggestion.objects.create(unit=unit, target="Theirs!\n", user=self.anotheruser)
        response = self.client.get(reverse("peon-start"))
        self.assertContains(response, "1 suggestion waiting")

    def test_translate_count_skips_suggested(self) -> None:
        self.set_onboarded(True)
        response = self.client.get(reverse("peon-start"))
        before = response.context["targets"][0].translatable
        Suggestion.objects.create(
            unit=self.get_unit(), target="Theirs!\n", user=self.anotheruser
        )
        response = self.client.get(reverse("peon-start"))
        self.assertEqual(response.context["targets"][0].translatable, before - 1)

    def test_job_redirects_to_peon(self) -> None:
        self.set_onboarded(True)
        Suggestion.objects.create(
            unit=self.get_unit(), target="Theirs!\n", user=self.anotheruser
        )
        response = self.client.get(
            reverse("peon-start"), {"mode": "review", "project": self.project.slug}
        )
        path = [self.project.slug, "-", self.translation.language.code]
        self.assertRedirects(
            response,
            f"{reverse('peon', kwargs={'path': path})}?mode=review",
            fetch_redirect_response=False,
        )

    def test_navbar_button(self) -> None:
        response = self.client.get(reverse("home"), follow=True)
        self.assertContains(response, 'id="peon-cta"')
