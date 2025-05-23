# Generated by Django 4.2.16 on 2024-09-24 08:53

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "pages",
            "0008_historicalpage_created_historicalpage_modified_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalpage",
            name="content_markdown",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="historicalpage",
            name="uses_markdown",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="page",
            name="content_markdown",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="page",
            name="uses_markdown",
            field=models.BooleanField(default=True),
        ),
    ]
