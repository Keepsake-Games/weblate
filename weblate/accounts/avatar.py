# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import os.path
from pathlib import Path
from ssl import CertificateError
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.cache import InvalidCacheBackendError, caches
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext, pgettext

from weblate.utils.errors import report_error
from weblate.utils.requests import fetch_url

if TYPE_CHECKING:
    from weblate.auth.models import User


DISCORD_CDN_URL = "https://cdn.discordapp.com/"
# The Discord CDN only serves powers of two, from 16 up to 4096
DISCORD_AVATAR_SIZES = (16, 32, 64, 128, 256, 512, 1024, 2048, 4096)


def avatar_for_email(email: str | None, size: int = 80) -> str:
    """Generate url for avatar."""
    # Safely handle blank e-mail
    if not email:
        email = "noreply@weblate.org"

    mail_hash = hashlib.md5(email.lower().encode(), usedforsecurity=False).hexdigest()

    querystring = urlencode({"d": settings.AVATAR_DEFAULT_IMAGE, "s": str(size)})

    return f"{settings.AVATAR_URL_PREFIX}avatar/{mail_hash}?{querystring}"


def get_fallback_avatar_url(size: int, name: Literal["weblate", "api"] = "weblate"):
    """Return URL of fallback avatar."""
    return os.path.join(settings.STATIC_URL, f"{name}-{size}.png")


def get_fallback_avatar(size: int) -> bytes:
    """Return fallback avatar."""
    filename = finders.find(f"weblate-{size}.png")
    if filename is None:
        msg = f"Missing fallback avatar file for {size=}!"
        raise OSError(msg)
    return Path(filename).read_bytes()


def snap_discord_avatar_size(size: int) -> int:
    """Round a size up to one the Discord CDN serves."""
    return next(
        (allowed for allowed in DISCORD_AVATAR_SIZES if allowed >= size),
        DISCORD_AVATAR_SIZES[-1],
    )


def discord_avatar_url(user: User, size: int) -> str | None:
    """
    Generate URL of the Discord avatar of a user.

    Returns None when the account has no Discord association or no avatar set
    there, in which case the generic avatar handling applies.
    """
    association = user.get_discord_association()
    if association is None:
        return None
    avatar_hash = (association.extra_data or {}).get("avatar")
    if not avatar_hash:
        return None
    querystring = urlencode({"size": str(snap_discord_avatar_size(size))})
    return f"{DISCORD_CDN_URL}avatars/{association.uid}/{avatar_hash}.png?{querystring}"


def get_avatar_url(user: User, size: int) -> str:
    """Generate URL of the avatar of a user."""
    return discord_avatar_url(user, size) or avatar_for_email(user.email, size)


def get_avatar_image(user: User, size: int) -> bytes:
    """Return avatar image from cache (if available) or download it."""
    username = user.username
    url = get_avatar_url(user, size)
    # The URL is part of the cache key so that a changed Discord avatar shows up
    # right away instead of only once the cached image expires.
    url_hash = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()[:12]
    cache_key = f"avatar-img-{username}-{size!s}-{url_hash}"

    # Try using avatar specific cache if available
    try:
        cache = caches["avatar"]
    except InvalidCacheBackendError:
        cache = caches["default"]

    image = cache.get(cache_key)
    if image is None:
        try:
            image = download_avatar_url(url)
            cache.set(cache_key, image)
        except (OSError, CertificateError):
            report_error(f"Could not fetch avatar for {username}")
            return get_fallback_avatar(size)

    return image


def download_avatar_url(url: str) -> bytes:
    """Download avatar image from remote server."""
    response = fetch_url("get", url, timeout=1.0)
    return response.content


def download_avatar_image(email: str, size: int) -> bytes:
    """Download gravatar image for an e-mail address."""
    return download_avatar_url(avatar_for_email(email, size))


def get_user_display(user: User | None, icon: bool = True, link: bool = False) -> str:
    """Nicely format user for display."""
    # Did we get any user?
    if user is None:
        # None user, probably remotely triggered action
        username = full_name = pgettext("No known user", "None")
        email = "noreply@weblate.org"
    else:
        # Get basic info
        username = user.username
        email = user.email
        full_name = user.full_name.strip()

        if not full_name:
            # Use user name if full name is empty
            full_name = username
        elif username == email:
            # Use full name in case username matches e-mail
            username = full_name

    # Icon requested?
    if icon and settings.ENABLE_AVATARS:
        if email == "noreply@weblate.org":
            avatar = get_fallback_avatar_url(32)
        else:
            avatar = reverse(
                "user_avatar", kwargs={"user": cast("User", user).username, "size": 32}
            )

        username = format_html(
            '<img src="{}" class="avatar w32" loading="lazy" alt="{}" /> {}',
            avatar,
            gettext("User avatar"),
            username,
        )

    if link and user is not None:
        return format_html(
            '<a href="{}" title="{}" class="user-link"><span>{}</span></a>',
            user.get_absolute_url(),
            full_name,
            username,
        )
    return format_html('<span title="{}">{}</span>', full_name, username)
