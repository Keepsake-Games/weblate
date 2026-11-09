# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0032_alter_subscription_notification"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="onboarded",
            field=models.BooleanField(
                default=False, verbose_name="Completed the first-time setup"
            ),
        ),
    ]
