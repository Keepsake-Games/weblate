# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for models (AuditLog and Profile)."""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.test.utils import override_settings
from social_django.models import UserSocialAuth

from weblate.accounts.models import (
    AUDIT_WARNING,
    NOTIFY_ACTIVITY,
    AuditLog,
    Profile,
    is_private_commit_email,
)
from weblate.auth.models import User
from weblate.trans.models import Project


class AuditLogTestCase(SimpleTestCase):
    def test_address_ipv4(self) -> None:
        audit = AuditLog(address="127.0.0.1")
        self.assertEqual(audit.shortened_address, "127.0.0.0")

    def test_address_ipv6_local(self) -> None:
        audit = AuditLog(address="fe80::54c2:1234:5678:90ab")
        self.assertEqual(audit.shortened_address, "fe80::")

    def test_address_ipv6_weblate(self) -> None:
        audit = AuditLog(address="2a01:4f8:c0c:a84b::1")
        self.assertEqual(audit.shortened_address, "2a01:4f8:c0c::")

    def test_address_blank(self) -> None:
        audit = AuditLog()
        self.assertEqual(audit.shortened_address, "")

    def test_superuser_audit_classification(self) -> None:
        self.assertIn("superuser-granted", AUDIT_WARNING)
        self.assertIn("superuser-revoked", AUDIT_WARNING)
        self.assertIn("superuser-granted", NOTIFY_ACTIVITY)
        self.assertIn("superuser-revoked", NOTIFY_ACTIVITY)

    def test_user_agent_display_empty(self) -> None:
        audit = AuditLog(user_agent="")
        self.assertEqual(audit.get_user_agent_display(), "")

    def test_user_agent_display(self) -> None:
        audit = AuditLog(user_agent="PC / Linux / Chrome 120.0.0")
        self.assertEqual(audit.get_user_agent_display(), "PC / Linux / Chrome 120.0.0")

    def test_user_agent_display_localizes_first_part_via_mapping(self) -> None:
        with mock.patch.dict(
            "weblate.accounts.models.USER_AGENT_DEVICE_TYPES",
            {"PC": "Translated PC"},
            clear=False,
        ):
            audit = AuditLog(user_agent="PC / Linux / Chrome 120.0.0")
            result = audit.get_user_agent_display()
            self.assertEqual(result, "Translated PC / Linux / Chrome 120.0.0")


class AuditLogLoggingTestCase(TestCase):
    def test_sitewide_team_add_logged(self) -> None:
        user = User.objects.create_user(
            username="audit-user",
            email="audit-user@example.com",
        )

        with self.assertLogs("weblate.audit", level="INFO") as captured:
            AuditLog.objects.create(
                user, None, "sitewide-team-add", team="Users", username="admin"
            )

        self.assertTrue(
            any("audit[sitewide-team-add]" in entry for entry in captured.output)
        )


class ProfileCommitNameTestCase(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="testuser",
            full_name="Test User",
            email="test@example.com",
        )
        self.profile = self.user.profile

    @override_settings(
        PRIVATE_COMMIT_NAME_TEMPLATE="{site_title} user {user_id} from {site_domain}",
        SITE_TITLE="WeblateTest",
        SITE_DOMAIN="weblate.test:8080",
    )
    def test_get_site_commit_name(self) -> None:
        name = self.profile.get_site_commit_name()
        self.assertEqual(name, f"WeblateTest user {self.user.pk} from weblate.test")

    @override_settings(
        PRIVATE_COMMIT_NAME_TEMPLATE="Anonymous {username}",
        PRIVATE_COMMIT_NAME_OPT_IN=False,
    )
    def test_get_commit_name_default_private(self) -> None:
        self.profile.commit_name = Profile.CommitNameChoices.DEFAULT
        self.assertEqual(self.profile.get_commit_name(), "Anonymous testuser")

    @override_settings(
        PRIVATE_COMMIT_NAME_TEMPLATE="Anonymous {user_id}",
        PRIVATE_COMMIT_NAME_OPT_IN=True,
    )
    def test_get_commit_name_default_public(self) -> None:
        self.profile.commit_name = Profile.CommitNameChoices.DEFAULT
        self.assertEqual(self.profile.get_commit_name(), "Test User")

    def test_get_commit_name_explicit_public(self) -> None:
        self.profile.commit_name = Profile.CommitNameChoices.PUBLIC
        self.assertEqual(self.profile.get_commit_name(), "Test User")

    @override_settings(PRIVATE_COMMIT_NAME_TEMPLATE="Hidden Name")
    def test_get_commit_name_explicit_private(self) -> None:
        self.profile.commit_name = Profile.CommitNameChoices.PRIVATE
        self.assertEqual(self.profile.get_commit_name(), "Hidden Name")

    @override_settings(
        PRIVATE_COMMIT_NAME_TEMPLATE="Anon",
        PRIVATE_COMMIT_NAME_OPT_IN=False,
    )
    def test_bot_naming_remains_visible(self) -> None:
        self.user.is_bot = True
        self.user.save()
        self.profile.commit_name = Profile.CommitNameChoices.DEFAULT
        self.assertEqual(self.profile.get_commit_name(), "Test User")

    @override_settings(
        PRIVATE_COMMIT_NAME_TEMPLATE="Hidden",
        PRIVATE_COMMIT_NAME_OPT_IN=True,
    )
    def test_get_commit_name_explicit_private_ignores_global_public(self) -> None:
        self.profile.commit_name = Profile.CommitNameChoices.PRIVATE
        self.assertEqual(self.profile.get_commit_name(), "Hidden")

    @override_settings(
        PRIVATE_COMMIT_NAME_TEMPLATE="",
        PRIVATE_COMMIT_NAME_OPT_IN=False,
    )
    def test_get_commit_name_empty_template_fallback(self) -> None:
        self.profile.commit_name = Profile.CommitNameChoices.PRIVATE
        self.assertEqual(self.profile.get_commit_name(), "Test User")


class ProfileTMTestCase(TestCase):
    @override_settings(DEFAULT_AUTOCLEAN_TM=True)
    def test_default_tm_with_autoclean(self) -> None:
        """Test that TM is disabled by default when autoclean is on."""
        user = User.objects.create_user(username="testautoclean")
        user.refresh_from_db()
        self.assertFalse(user.profile.contribute_personal_tm)

    @override_settings(DEFAULT_AUTOCLEAN_TM=False)
    def test_default_tm_without_autoclean(self) -> None:
        """Test that TM is enabled by default when autoclean is off."""
        user = User.objects.create_user(username="testnoautoclean")
        user.refresh_from_db()
        self.assertTrue(user.profile.contribute_personal_tm)


class PrivateCommitEmailTestCase(SimpleTestCase):
    @override_settings(SITE_DOMAIN="example.com")
    def test_generated_address(self) -> None:
        self.assertTrue(is_private_commit_email("dogstar@users.noreply.example.com"))

    @override_settings(SITE_DOMAIN="example.com")
    def test_real_address(self) -> None:
        self.assertFalse(is_private_commit_email("someone@example.com"))
        self.assertFalse(is_private_commit_email("someone@users.noreply.example.org"))

    def test_blank_address(self) -> None:
        # Nothing to contact either way
        self.assertTrue(is_private_commit_email(""))

    @override_settings(PRIVATE_COMMIT_EMAIL_TEMPLATE="")
    def test_no_template(self) -> None:
        self.assertFalse(is_private_commit_email("someone@example.com"))


class DeliverableEmailTestCase(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="dogstar", email="dogstar@users.noreply.example.com"
        )

    def test_without_verified_email(self) -> None:
        """Accounts created outside of an identity provider can be mailed."""
        self.assertTrue(self.user.has_deliverable_email)

    def test_undeliverable(self) -> None:
        social = UserSocialAuth.objects.create(
            user=self.user, provider="discord", uid="424242424242424242"
        )
        social.verifiedemail_set.create(email=self.user.email, is_deliverable=False)
        self.assertFalse(User.objects.get(pk=self.user.pk).has_deliverable_email)

    def test_deliverable(self) -> None:
        social = UserSocialAuth.objects.create(
            user=self.user, provider="github", uid="1"
        )
        social.verifiedemail_set.create(email=self.user.email, is_deliverable=True)
        self.assertTrue(User.objects.get(pk=self.user.pk).has_deliverable_email)

    def test_blank_email(self) -> None:
        self.user.email = None
        self.user.save(update_fields=["email"])
        self.assertFalse(User.objects.get(pk=self.user.pk).has_deliverable_email)


class InitialWatchedProjectsTestCase(TestCase):
    def setUp(self) -> None:
        self.project = Project.objects.create(name="Hello", slug="hello")
        self.other = Project.objects.create(name="Bye", slug="bye")

    def create(self) -> User:
        return User.objects.create_user(username="dogstar")

    def test_default(self) -> None:
        """Upstream behaviour: a new account watches nothing."""
        self.assertEqual(list(self.create().profile.watched.all()), [])

    @override_settings(INITIAL_WATCHED_PROJECTS=["hello"])
    def test_by_slug(self) -> None:
        self.assertEqual(list(self.create().profile.watched.all()), [self.project])

    @override_settings(INITIAL_WATCHED_PROJECTS=["*"])
    def test_all(self) -> None:
        self.assertEqual(
            set(self.create().profile.watched.all()), {self.project, self.other}
        )

    @override_settings(INITIAL_WATCHED_PROJECTS=["nonexistent"])
    def test_unknown_slug(self) -> None:
        self.assertEqual(list(self.create().profile.watched.all()), [])
