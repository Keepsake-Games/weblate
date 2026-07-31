# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for user handling."""

from __future__ import annotations

from io import BytesIO

import responses
from django.urls import reverse
from PIL import Image
from social_django.models import UserSocialAuth

from weblate.accounts import avatar
from weblate.auth.models import User
from weblate.trans.tests.test_views import FixtureTestCase

TEST_URL = (
    "https://www.gravatar.com/avatar/55502f40dc8b7c769880b10874abc9d0?d=identicon&s=32"
)


class AvatarTest(FixtureTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user.email = "test@example.com"
        self.user.save()

    def test_avatar_for_email(self) -> None:
        url = avatar.avatar_for_email(self.user.email, size=32)
        self.assertEqual(TEST_URL, url)

    @responses.activate
    def test_avatar(self) -> None:
        image = Image.new("RGB", (32, 32))
        storage = BytesIO()
        image.save(storage, "PNG")
        imagedata = storage.getvalue()
        responses.add(responses.GET, TEST_URL, body=imagedata)
        # Real user
        response = self.client.get(
            reverse("user_avatar", kwargs={"user": self.user.username, "size": 32})
        )
        self.assert_png(response)
        self.assertEqual(response.content, imagedata)
        # Test caching
        response = self.client.get(
            reverse("user_avatar", kwargs={"user": self.user.username, "size": 32})
        )
        self.assert_png(response)
        self.assertEqual(response.content, imagedata)

    @responses.activate
    def test_avatar_error(self) -> None:
        responses.add(responses.GET, TEST_URL, status=503)
        # Choose different username to avoid using cache
        self.user.username = "test2"
        self.user.save()
        response = self.client.get(
            reverse("user_avatar", kwargs={"user": self.user.username, "size": 32})
        )
        self.assert_png(response)

    @responses.activate
    def test_avatar_rejects_unsupported_size_before_fetch(self) -> None:
        response = self.client.get(
            reverse("user_avatar", kwargs={"user": self.user.username, "size": 999})
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(responses.calls), 0)

    def test_anonymous_avatar(self) -> None:
        anonymous = User.objects.get(username="anonymous")
        # Anonymous user
        response = self.client.get(
            reverse("user_avatar", kwargs={"user": anonymous.username, "size": 32})
        )
        self.assertRedirects(
            response,
            "/static/weblate-32.png",
            fetch_redirect_response=False,
            status_code=301,
        )

    def test_fallback_avatar(self) -> None:
        self.assert_png_data(avatar.get_fallback_avatar(32))


class DiscordAvatarTest(FixtureTestCase):
    def add_discord(self, avatar_hash: str | None = "abc123") -> None:
        UserSocialAuth.objects.create(
            user=self.user,
            provider="discord",
            uid="424242424242424242",
            extra_data={"avatar": avatar_hash},
        )

    def test_no_association(self) -> None:
        self.assertIsNone(avatar.discord_avatar_url(self.user, 32))
        # Falls back to the generic handling
        self.assertIn("gravatar.com", avatar.get_avatar_url(self.user, 32))

    def test_no_avatar_set(self) -> None:
        self.add_discord(avatar_hash=None)
        self.assertIsNone(avatar.discord_avatar_url(self.user, 32))

    def test_url(self) -> None:
        self.add_discord()
        self.assertEqual(
            avatar.discord_avatar_url(self.user, 32),
            "https://cdn.discordapp.com/avatars/424242424242424242/abc123.png?size=32",
        )
        self.assertEqual(
            avatar.get_avatar_url(self.user, 32),
            avatar.discord_avatar_url(self.user, 32),
        )

    def test_size_snapping(self) -> None:
        """The CDN only serves powers of two, Weblate also uses 24 and 80."""
        self.assertEqual(avatar.snap_discord_avatar_size(24), 32)
        self.assertEqual(avatar.snap_discord_avatar_size(32), 32)
        self.assertEqual(avatar.snap_discord_avatar_size(80), 128)
        self.assertEqual(avatar.snap_discord_avatar_size(128), 128)
        # Never asks for more than the CDN serves
        self.assertEqual(avatar.snap_discord_avatar_size(9999), 4096)

    @responses.activate
    def test_avatar_served(self) -> None:
        self.add_discord()
        image = Image.new("RGB", (32, 32))
        storage = BytesIO()
        image.save(storage, "PNG")
        imagedata = storage.getvalue()
        url = avatar.discord_avatar_url(self.user, 32)
        assert url is not None
        responses.add(responses.GET, url, body=imagedata)
        response = self.client.get(
            reverse("user_avatar", kwargs={"user": self.user.username, "size": 32})
        )
        self.assert_png(response)
        self.assertEqual(response.content, imagedata)

    def test_url_follows_avatar_change(self) -> None:
        """The cache key is derived from the URL, so it has to change as well."""
        self.add_discord()
        first = avatar.get_avatar_url(self.user, 32)
        association = self.user.get_discord_association()
        assert association is not None
        association.extra_data = {"avatar": "def456"}
        association.save()
        self.assertNotEqual(first, avatar.get_avatar_url(self.user, 32))
