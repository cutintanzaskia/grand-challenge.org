# Generated by Django 4.2.20 on 2025-05-01 16:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("algorithms", "0071_algorithmimage_is_archived"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="job",
            index=models.Index(
                fields=["status", "created"],
                name="algorithms__status_4143fb_idx",
            ),
        ),
    ]
