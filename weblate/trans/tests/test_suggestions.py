# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for suggestion views."""

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from weblate.auth.models import User
from weblate.trans.actions import ActionEvents
from weblate.trans.models import Change, Suggestion, WorkflowSetting
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.tests.utils import TESTPASSWORD


class SuggestionsTest(ViewTestCase):
    def add_suggestion_1(self):
        return self.edit_unit("Hello, world!\n", "Nazdar svete!\n", suggest="yes")

    def add_suggestion_2(self):
        return self.edit_unit("Hello, world!\n", "Ahoj svete!\n", suggest="yes")

    def test_add(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        # Try empty suggestion (should not be added)
        response = self.edit_unit("Hello, world!\n", "", suggest="yes")
        # We should stay on same message
        self.assert_redirects_offset(response, translate_url, 1)

        # Add first suggestion
        response = self.add_suggestion_1()
        # We should get to second message
        self.assert_redirects_offset(response, translate_url, 2)

        # Add second suggestion
        response = self.add_suggestion_2()
        # We should get to second message
        self.assert_redirects_offset(response, translate_url, 2)

        # Reload from database
        unit = self.get_unit()
        translation = self.component.translation_set.get(language_code="cs")
        # Check number of suggestions
        self.assertEqual(translation.stats.suggestions, 1)
        self.assert_backend(0)

        # Unit should not be translated
        self.assertEqual(len(unit.all_checks), 0)
        self.assertFalse(unit.translated)
        self.assertFalse(unit.fuzzy)
        self.assertEqual(len(self.get_unit().suggestions), 2)

    def test_add_same(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        # Add first suggestion
        response = self.add_suggestion_1()
        # We should get to second message
        self.assert_redirects_offset(response, translate_url, 2)
        # Add first suggestion
        response = self.add_suggestion_1()
        # We should stay on same message
        self.assert_redirects_offset(response, translate_url, 1)

        # Reload from database
        unit = self.get_unit()
        translation = self.component.translation_set.get(language_code="cs")

        # Check number of suggestions
        self.assertEqual(translation.stats.suggestions, 1)
        self.assert_backend(0)

        # Unit should not be translated
        self.assertEqual(len(unit.all_checks), 0)
        self.assertFalse(unit.translated)
        self.assertFalse(unit.fuzzy)
        self.assertEqual(len(self.get_unit().suggestions), 1)

    def test_delete(self, **kwargs) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        # Create two suggestions
        self.add_suggestion_1()
        self.add_suggestion_2()

        # Get ids of created suggestions
        suggestions = self.get_unit().suggestions.values_list("pk", flat=True)
        self.assertEqual(len(suggestions), 2)

        # Delete one of suggestions
        response = self.edit_unit(
            "Hello, world!\n", "", delete=suggestions[0], **kwargs
        )
        self.assert_redirects_offset(response, translate_url, 1)

        # Ensure we have just one
        suggestions = self.get_unit().suggestions.values_list("pk", flat=True)
        self.assertEqual(len(suggestions), 1)

    def test_change_diff_for_deleted_suggestion(self, **kwargs) -> None:
        """
        Check that the diff for deleted suggestion is correctly displayed.

        When a suggestion is deleted, the diff for the deleted suggestion
        should be the same as the corresponding "Suggestion added"
        """
        translate_url = reverse("translate", kwargs=self.kw_translation)
        self.edit_unit("Hello, world!\n", "Nazdar!\n")
        self.add_suggestion_1()
        response = self.client.get(translate_url)
        self.assertNotContains(response, "Suggestion removed")
        # 1st diff occurrence is in the "Suggestions" tab, the 2nd in "History" tab
        self.assertContains(
            response,
            """Nazdar<ins><span class="hlspace"><span class="space-space"> </span></span>svete</ins>!""",
            count=2,
        )

        suggestions = self.get_unit().suggestions.values_list("pk", flat=True)
        self.assertEqual(len(suggestions), 1)
        response = self.edit_unit(
            "Hello, world!\n", "", delete=suggestions[0], **kwargs
        )

        response = self.client.get(translate_url)
        self.assertContains(response, "Suggestion removed", count=1)
        self.assertContains(response, "Suggestion added", count=1)
        # both diff occurrence are in "History" tab,
        # as suggestion is no longer visible in the "Suggestion" tab
        self.assertContains(
            response,
            """Nazdar<ins><span class="hlspace"><span class="space-space"> </span></span>svete</ins>!""",
            count=2,
        )

    def test_delete_spam(self) -> None:
        self.test_delete(spam="1")

    def test_accept_edit(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        # Create suggestion
        self.add_suggestion_1()

        # Get ids of created suggestions
        suggestion = self.get_unit().suggestions[0].pk

        # Accept one of suggestions
        response = self.edit_unit("Hello, world!\n", "", accept_edit=suggestion)
        self.assert_redirects_offset(response, translate_url, 1)

    def test_accept(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        # Create two suggestions
        self.add_suggestion_1()
        self.add_suggestion_2()

        # Get ids of created suggestions
        suggestions = self.get_unit().suggestions
        self.assertEqual(suggestions.count(), 2)

        # Accept one of suggestions
        response = self.edit_unit(
            "Hello, world!\n", "", accept=suggestions.get(target="Ahoj svete!\n").pk
        )
        self.assert_redirects_offset(response, translate_url, 2)

        # Reload from database
        unit = self.get_unit()
        translation = self.component.translation_set.get(language_code="cs")
        # Check number of suggestions
        self.assertEqual(translation.stats.suggestions, 1)

        # Unit should be translated
        self.assertEqual(len(unit.all_checks), 0)
        self.assertTrue(unit.translated)
        self.assertFalse(unit.fuzzy)
        self.assertEqual(unit.target, "Ahoj svete!\n")
        self.assert_backend(1)
        self.assertEqual(len(self.get_unit().suggestions), 1)

    def test_accept_anonymous(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        self.client.logout()
        # Create suggestions
        self.add_suggestion_1()

        self.client.login(username="testuser", password="testpassword")

        # Get ids of created suggestion
        suggestions = list(self.get_unit().suggestions)
        self.assertEqual(len(suggestions), 1)

        suggestion = suggestions[0]
        if suggestion.user is None:
            msg = "Suggestion user should not be None"
            raise AssertionError(msg)

        user = suggestion.user
        self.assertEqual(user.username, settings.ANONYMOUS_USER_NAME)

        # Accept one of suggestions
        with self.captureOnCommitCallbacks(execute=True):
            response = self.edit_unit("Hello, world!\n", "", accept=suggestions[0].pk)
        self.assert_redirects_offset(response, translate_url, 2)

        # Reload from database
        unit = self.get_unit()
        translation = self.component.translation_set.get(language_code="cs")
        # Check number of suggestions
        self.assertEqual(translation.stats.suggestions, 0)

        # Unit should be translated
        self.assertEqual(unit.target, "Nazdar svete!\n")

    def test_vote_language(self) -> None:
        WorkflowSetting.objects.create(
            project=self.project,
            language=self.translation.language,
            enable_suggestions=True,
            suggestion_voting=True,
            suggestion_autoaccept=0,
        )

        self.assert_vote()

    def test_vote(self) -> None:
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 0
        self.component.save()

        self.assert_vote()

    def test_vote_without_autoaccept_keeps_direct_translation(self) -> None:
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 0
        self.component.save()

        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")

        unit = self.get_unit()
        self.assertEqual(unit.target, "Nazdar svete!\n")
        self.assert_backend(1)

    def test_vote_autoaccept_direct_translation_denied(self) -> None:
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 1
        self.component.save()

        response = self.edit_unit("Hello, world!\n", "Nazdar svete!\n", follow=True)

        self.assertContains(
            response,
            "This translation is configured for suggestion voting. Add a suggestion instead of saving a direct translation.",
        )
        self.assertFalse(self.get_unit().translated)
        self.assert_backend(0)

    def test_vote_when_voting_disabled(self) -> None:
        self.add_suggestion_1()
        suggestion_id = self.get_unit().suggestions[0].pk

        response = self.edit_unit(
            "Hello, world!\n", "", upvote=suggestion_id, follow=True
        )

        self.assertContains(response, "Suggestion voting is disabled.")
        suggestion = Suggestion.objects.get(pk=suggestion_id)
        self.assertEqual(suggestion.get_num_votes(), 0)

    def assert_vote(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        self.add_suggestion_1()

        suggestion_id = self.get_unit().suggestions[0].pk

        with self.captureOnCommitCallbacks(execute=True):
            response = self.edit_unit("Hello, world!\n", "", upvote=suggestion_id)
        self.assert_redirects_offset(response, translate_url, 2)

        suggestion = Suggestion.objects.get(pk=suggestion_id)
        self.assertEqual(suggestion.get_num_votes(), 1)

        response = self.edit_unit("Hello, world!\n", "", downvote=suggestion_id)
        self.assert_redirects_offset(response, translate_url, 1)

        suggestion = Suggestion.objects.get(pk=suggestion_id)
        self.assertEqual(suggestion.get_num_votes(), -1)

    def test_vote_autoaccept(self) -> None:
        self.add_suggestion_1()

        translate_url = reverse("translate", kwargs=self.kw_translation)
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 1
        self.component.save()

        suggestion_id = self.get_unit().suggestions[0].pk

        with self.captureOnCommitCallbacks(execute=True):
            response = self.edit_unit("Hello, world!\n", "", upvote=suggestion_id)
        self.assert_redirects_offset(response, translate_url, 2)

        # Reload from database
        unit = self.get_unit()
        translation = self.component.translation_set.get(language_code="cs")
        # Check number of suggestions
        self.assertEqual(translation.stats.suggestions, 0)

        # Unit should be translated
        self.assertEqual(len(unit.all_checks), 0)
        self.assertTrue(unit.translated)
        self.assertFalse(unit.fuzzy)
        self.assertEqual(unit.target, "Nazdar svete!\n")
        self.assert_backend(1)

    def test_vote_when_same_suggestion(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 0
        self.component.save()

        # Add the first suggestion as default test-user
        response = self.add_suggestion_1()
        suggestion_id = self.get_unit().suggestions[0].pk
        suggestion = Suggestion.objects.get(pk=suggestion_id)

        # Suggestion get vote from the user that makes suggestion
        self.assertEqual(suggestion.get_num_votes(), 1)

        # Add suggestion as second user
        self.log_as_jane()
        response = self.add_suggestion_1()

        # When adding the same suggestion, we stay on the same page
        self.assert_redirects_offset(response, translate_url, 1)
        suggestion = Suggestion.objects.get(pk=suggestion_id)

        # and the suggestion gets an upvote
        self.assertEqual(suggestion.get_num_votes(), 2)

    def test_rendering_check_isolation(self) -> None:
        """Verify that suggestion checks do not reuse the dirty cache of the unit."""
        # Force SameCheck to ensure the suggestion system actually runs checks.
        self.component.checks = "weblate.checks.same.SameCheck"  # type: ignore[attr-defined]
        self.component.save()

        unit = self.get_unit()

        # Set dirty cache in memory
        unit.check_cache = {"render": "DIRTY_PARENT_CACHE"}

        # Create suggestion
        suggestion = Suggestion.objects.create(
            unit=unit,
            target=unit.source,
            user=self.user,
        )

        # We explicitly assign the dirty unit to the suggestion.
        suggestion.unit = unit

        # Explicitly trigger check calculation
        checks = list(suggestion.get_checks())
        self.assertTrue(checks, "Setup Error: No checks were generated.")

        check_obj = next((check for check in checks if hasattr(check, "unit")), None)
        if check_obj is None:
            msg = "No check with a 'unit' attribute found."
            raise AssertionError(msg)

        fake_unit = check_obj.unit

        # Verification of Isolation
        self.assertTrue(
            hasattr(fake_unit, "check_cache"),
            "Test Failure: Fake unit does not expose 'check_cache' attribute.",
        )

        # We do not check for empty cache ({}) because running checks fills it.
        # We check that the parent's dirt is gone.
        self.assertIsNot(
            fake_unit, unit, "Fake unit should be a different instance/copy"
        )

        self.assertIsNot(
            fake_unit.check_cache,
            unit.check_cache,
            "Isolation Failure: check_cache dictionary is shared by reference.",
        )

        self.assertNotEqual(fake_unit.check_cache.get("render"), "DIRTY_PARENT_CACHE")

        # Ensure the process didn't accidentally wipe the parent's cache
        self.assertEqual(
            unit.check_cache.get("render"),
            "DIRTY_PARENT_CACHE",
            "Integrity Failure: Parent unit cache was wiped during check execution.",
        )


class SuggestionModelTest(TestCase):
    def test_target_list(self):
        """Test that target_list property correctly splits plurals."""
        sep = "\x1e\x1e"

        suggestion = Suggestion(target="Hello world")
        self.assertEqual(suggestion.target_list, ["Hello world"])

        target_string = f"One apple{sep}Two apples{sep}Five apples"
        suggestion_plural = Suggestion(target=target_string)

        self.assertEqual(
            suggestion_plural.target_list, ["One apple", "Two apples", "Five apples"]
        )

        suggestion_empty = Suggestion(target="")
        self.assertEqual(suggestion_empty.target_list, [""])


class WorkflowAttributionTest(ViewTestCase):
    """
    Pin down who is credited under each of the translation workflows.

    Credits and any release notes are built from the author of a change, see
    :ref:`discord-attribution`, so which of the two user references a workflow
    fills in decides who gets the credit.
    """

    def setUp(self) -> None:
        super().setUp()
        self.reviewer = User.objects.create(
            username="reviewer",
            email="reviewer@example.org",
            password=TESTPASSWORD,
            full_name="Reviewer",
        )
        self.project.add_user(self.reviewer, "Administration")

    def get_content_changes(self):
        return [
            (
                change.action,
                change.user.username if change.user else None,
                change.author.username if change.author else None,
            )
            for change in Change.objects.content().order_by("timestamp", "pk")
        ]

    def test_direct_translation(self) -> None:
        """The translator is both the user and the author."""
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")
        self.assertEqual(
            self.get_content_changes(),
            [(ActionEvents.NEW, "testuser", "testuser")],
        )

    def test_peer_review(self) -> None:
        """Accepting credits the suggester, not whoever accepted."""
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n", suggest="yes")
        suggestion = self.get_unit().suggestions[0]

        self.client.login(username="reviewer", password="testpassword")
        self.edit_unit("Hello, world!\n", "", accept=suggestion.pk)

        # Adding a suggestion is not a content change, so it earns no credit,
        # and the acceptance is credited to the suggester
        self.assertEqual(
            self.get_content_changes(),
            [(ActionEvents.ACCEPT, "reviewer", "testuser")],
        )

    def test_dedicated_reviewers(self) -> None:
        """Approving is a content change credited to the reviewer."""
        self.project.translation_review = True
        self.project.save(update_fields=["translation_review"])
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")

        self.client.login(username="reviewer", password="testpassword")
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n", review="30")

        # Both are credited, each with their own change
        self.assertEqual(
            self.get_content_changes(),
            [
                (ActionEvents.NEW, "testuser", "testuser"),
                (ActionEvents.APPROVE, "reviewer", "reviewer"),
            ],
        )


class SuggestionVoterCreditTest(ViewTestCase):
    """
    Voters are recorded on the change, so that review effort can be credited.

    The votes are destroyed together with the suggestion, so the information has
    to be captured while accepting or rejecting, see :ref:`discord-attribution`.
    """

    def setUp(self) -> None:
        super().setUp()
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 0
        self.component.save()
        self.voters = []
        for name in ("alice", "bob"):
            voter = User.objects.create(
                username=name,
                email=f"{name}@example.org",
                password=TESTPASSWORD,
                full_name=name.title(),
            )
            self.project.add_user(voter, "Administration")
            self.voters.append(voter)

    def vote(self, username: str, suggestion_pk: int, *, approve: bool = True) -> None:
        self.client.login(username=username, password="testpassword")
        if approve:
            self.edit_unit("Hello, world!\n", "", upvote=suggestion_pk)
        else:
            self.edit_unit("Hello, world!\n", "", downvote=suggestion_pk)

    def get_change_voters(self, action):
        change = Change.objects.filter(action=action).order_by("-timestamp")[0]
        return change.details.get("voters")

    def test_accept_records_voters(self) -> None:
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n", suggest="yes")
        suggestion = self.get_unit().suggestions[0]

        self.vote("alice", suggestion.pk)
        self.vote("bob", suggestion.pk, approve=False)

        self.client.login(username="testuser", password="testpassword")
        self.edit_unit("Hello, world!\n", "", accept=suggestion.pk)

        self.assertEqual(
            self.get_change_voters(ActionEvents.ACCEPT),
            {"approve": ["alice"], "reject": ["bob"]},
        )

    def test_reject_records_voters(self) -> None:
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n", suggest="yes")
        suggestion = self.get_unit().suggestions[0]

        self.vote("bob", suggestion.pk, approve=False)

        self.client.login(username="testuser", password="testpassword")
        self.edit_unit("Hello, world!\n", "", delete=suggestion.pk)

        self.assertEqual(
            self.get_change_voters(ActionEvents.SUGGESTION_DELETE),
            {"reject": ["bob"]},
        )

    def test_suggester_excluded(self) -> None:
        """The suggester is credited as author, so is left out of the voters."""
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n", suggest="yes")
        suggestion = self.get_unit().suggestions[0]
        # The suggester votes for their own suggestion
        self.vote("testuser", suggestion.pk)
        self.vote("alice", suggestion.pk)

        self.client.login(username="testuser", password="testpassword")
        self.edit_unit("Hello, world!\n", "", accept=suggestion.pk)

        self.assertEqual(
            self.get_change_voters(ActionEvents.ACCEPT), {"approve": ["alice"]}
        )

    def test_no_votes_records_nothing(self) -> None:
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n", suggest="yes")
        suggestion = self.get_unit().suggestions[0]

        self.edit_unit("Hello, world!\n", "", accept=suggestion.pk)

        self.assertIsNone(self.get_change_voters(ActionEvents.ACCEPT))

    def test_autoaccept_records_voters(self) -> None:
        """Voting can accept the suggestion itself, which still records voters."""
        self.component.suggestion_autoaccept = 2
        self.component.save()

        self.edit_unit("Hello, world!\n", "Nazdar svete!\n", suggest="yes")
        suggestion = self.get_unit().suggestions[0]

        # This vote reaches the threshold and accepts the suggestion
        self.vote("alice", suggestion.pk)

        unit = self.get_unit()
        self.assertEqual(unit.target, "Nazdar svete!\n")
        self.assertEqual(len(unit.suggestions), 0)
        self.assertEqual(
            self.get_change_voters(ActionEvents.ACCEPT), {"approve": ["alice"]}
        )
