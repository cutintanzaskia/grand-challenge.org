from django import template
from django.conf import settings

register = template.Library()


@register.filter
def millicents_to_euro(millicents):
    try:
        euros = millicents / 1000 / 100
    except TypeError:
        return "-"

    return f"€ {euros:.2f}"


@register.filter
def storage_bytes_to_euro_per_year(storage_size):
    return millicents_to_euro(
        settings.COMPONENTS_S3_USD_MILLICENTS_PER_YEAR_PER_TB
        * settings.COMPONENTS_USD_TO_EUR
        * storage_size
        / settings.TERABYTE
    )


@register.filter
def registry_bytes_to_euro_per_year(storage_size):
    return millicents_to_euro(
        settings.COMPONENTS_ECR_USD_MILLICENTS_PER_YEAR_PER_TB
        * settings.COMPONENTS_USD_TO_EUR
        * storage_size
        / settings.TERABYTE
    )
