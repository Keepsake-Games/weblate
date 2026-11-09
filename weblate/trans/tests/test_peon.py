# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.urls import reverse

from weblate.trans.models import Suggestion
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.hash import hash_to_checksum
from weblate.utils.state import STATE_TRANSLATED


class PeonViewTest(ViewTestCase):
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

        Suggestion.objects.create(
            unit=unit, target="Theirs!\n", user=self.anotheruser
        )
        response = self.client.get(reverse("peon-start"))
        self.assertContains(response, "1 suggestion waiting")

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
