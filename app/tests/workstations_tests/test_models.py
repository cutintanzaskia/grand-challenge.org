from datetime import datetime, timedelta, timezone

import pytest
from django.conf import settings
from django.core import mail
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import ProtectedError
from knox.models import AuthToken

from grandchallenge.components.backends.docker_client import (
    get_container_id,
    inspect_container,
)
from grandchallenge.components.tasks import stop_expired_services
from grandchallenge.reader_studies.models import InteractiveAlgorithmChoices
from grandchallenge.workstations.models import (
    Session,
    SessionCost,
    Workstation,
)
from tests.factories import (
    SessionFactory,
    UserFactory,
    WorkstationFactory,
    WorkstationImageFactory,
)
from tests.reader_studies_tests.factories import (
    QuestionFactory,
    ReaderStudyFactory,
)
from tests.utils import recurse_callbacks
from tests.workstations_tests.factories import FeedbackFactory


def stop_all_sessions():
    sessions = Session.objects.all()
    for s in sessions:
        s.stop()


@pytest.mark.django_db
@pytest.mark.parametrize("debug", [True, False])
def test_session_environ(settings, debug):
    settings.DEBUG = debug

    s = SessionFactory()
    env = s.environment

    assert env["GRAND_CHALLENGE_API_ROOT"] == "https://testserver/api/v1/"
    assert "Bearer " in env["GRAND_CHALLENGE_AUTHORIZATION"]
    assert env["WORKSTATION_SESSION_ID"] == str(s.pk)
    assert "WORKSTATION_SENTRY_DSN" in env

    if debug:
        assert "GRAND_CHALLENGE_UNSAFE" in env
    else:
        assert "GRAND_CHALLENGE_UNSAFE" not in env


@pytest.mark.django_db
def test_session_auth_token():
    s = SessionFactory()

    # Calling environment should generate an auth token for the creator
    assert s.auth_token is None

    _ = s.environment

    expected_duration = (
        s.created
        + timedelta(minutes=settings.WORKSTATIONS_GRACE_MINUTES)
        + timedelta(seconds=settings.WORKSTATIONS_SESSION_DURATION_LIMIT)
    )

    assert s.auth_token.user == s.creator
    assert abs(s.auth_token.expiry - expected_duration) < timedelta(seconds=10)

    # old tokens should be deleted
    old_pk = s.auth_token.pk

    _ = s.environment

    assert s.auth_token.pk != old_pk


@pytest.mark.django_db
def test_session_start(
    http_image, settings, django_capture_on_commit_callbacks
):
    # Execute celery tasks in place
    settings.task_eager_propagates = (True,)
    settings.task_always_eager = (True,)

    with django_capture_on_commit_callbacks() as callbacks:
        wsi = WorkstationImageFactory(image__from_path=http_image)
    recurse_callbacks(
        callbacks=callbacks,
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    with django_capture_on_commit_callbacks(execute=True):
        s = SessionFactory(workstation_image=wsi)

    try:
        assert get_container_id(name=s.service.container_name)

        s.refresh_from_db()
        assert s.status == s.STARTED

        container = inspect_container(name=s.service.container_name)

        expected_labels = {
            "job": f"{s._meta.app_label}-{s._meta.model_name}-{s.pk}",
            "traefik.enable": "true",
            f"traefik.http.routers.{s.hostname}-http.entrypoints": "workstation-http",
            f"traefik.http.routers.{s.hostname}-http.rule": f"Host(`{s.hostname}`)",
            f"traefik.http.routers.{s.hostname}-http.service": f"{s.hostname}-http",
            f"traefik.http.routers.{s.hostname}-websocket.entrypoints": "workstation-websocket",
            f"traefik.http.routers.{s.hostname}-websocket.rule": f"Host(`{s.hostname}`)",
            f"traefik.http.routers.{s.hostname}-websocket.service": f"{s.hostname}-websocket",
            f"traefik.http.services.{s.hostname}-http.loadbalancer.server.port": "8080",
            f"traefik.http.services.{s.hostname}-websocket.loadbalancer.server.port": "4114",
        }

        for k, v in expected_labels.items():
            assert container["Config"]["Labels"][k] == v

        networks = container["NetworkSettings"]["Networks"]
        assert len(networks) == 1
        assert settings.WORKSTATIONS_NETWORK_NAME in networks

        with django_capture_on_commit_callbacks(execute=True):
            s.user_finished = True
            s.save()

        with pytest.raises(ObjectDoesNotExist):
            # noinspection PyStatementEffect
            get_container_id(name=s.service.container_name)
    finally:
        stop_all_sessions()


@pytest.mark.django_db
def test_correct_session_stopped(
    http_image, settings, django_capture_on_commit_callbacks
):
    # Execute celery tasks in place
    settings.task_eager_propagates = (True,)
    settings.task_always_eager = (True,)

    with django_capture_on_commit_callbacks() as callbacks:
        wsi = WorkstationImageFactory(image__from_path=http_image)
    recurse_callbacks(
        callbacks=callbacks,
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    try:
        with django_capture_on_commit_callbacks(execute=True):
            s1, s2 = (
                SessionFactory(workstation_image=wsi),
                SessionFactory(workstation_image=wsi),
            )

        assert get_container_id(name=s1.service.container_name)
        assert get_container_id(name=s2.service.container_name)

        s2.refresh_from_db()
        auth_token_pk = s2.auth_token.pk

        with django_capture_on_commit_callbacks(execute=True):
            s2.user_finished = True
            s2.save()

        assert get_container_id(name=s1.service.container_name)
        with pytest.raises(ObjectDoesNotExist):
            # noinspection PyStatementEffect
            get_container_id(name=s2.service.container_name)

        with pytest.raises(ObjectDoesNotExist):
            # auth token should be deleted when the service is stopped
            AuthToken.objects.get(pk=auth_token_pk)

    finally:
        stop_all_sessions()


@pytest.mark.django_db
def test_session_cleanup(
    http_image, settings, django_capture_on_commit_callbacks
):
    # Execute celery tasks in place
    settings.task_eager_propagates = (True,)
    settings.task_always_eager = (True,)

    with django_capture_on_commit_callbacks() as callbacks:
        wsi = WorkstationImageFactory(image__from_path=http_image)
    recurse_callbacks(
        callbacks=callbacks,
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    default_region = "eu-nl-1"

    try:
        with django_capture_on_commit_callbacks(execute=True):
            s1, s2, s3 = (
                SessionFactory(workstation_image=wsi, region=default_region),
                SessionFactory(
                    workstation_image=wsi,
                    maximum_duration=timedelta(seconds=0),
                    region=default_region,
                ),
                # An expired service in a different region
                SessionFactory(
                    workstation_image=wsi,
                    maximum_duration=timedelta(seconds=0),
                    region="us-east-1",
                ),
            )

        assert get_container_id(name=s1.service.container_name)
        assert get_container_id(name=s2.service.container_name)
        assert get_container_id(name=s3.service.container_name)

        # Stop expired services in the default region
        stop_expired_services(
            app_label="workstations",
            model_name="session",
            region=default_region,
        )

        assert get_container_id(name=s1.service.container_name)
        with pytest.raises(ObjectDoesNotExist):
            # noinspection PyStatementEffect
            get_container_id(name=s2.service.container_name)
        assert get_container_id(name=s3.service.container_name)

    finally:
        stop_all_sessions()


@pytest.mark.django_db
def test_workstation_ready(
    http_image, settings, django_capture_on_commit_callbacks
):
    # Execute celery tasks in place
    settings.task_eager_propagates = (True,)
    settings.task_always_eager = (True,)

    # Do not execute the callbacks as the image should not be ready
    wsi = WorkstationImageFactory(image__from_path=http_image)
    assert wsi.is_manifest_valid is None
    assert wsi.can_execute is False

    with django_capture_on_commit_callbacks(execute=True):
        s = SessionFactory(workstation_image=wsi)

    s.refresh_from_db()
    assert s.status == s.FAILED


@pytest.mark.django_db
def test_session_limit(
    http_image, settings, django_capture_on_commit_callbacks
):
    # Execute celery tasks in place
    settings.WORKSTATIONS_MAXIMUM_SESSIONS = 1
    settings.task_eager_propagates = (True,)
    settings.task_always_eager = (True,)

    with django_capture_on_commit_callbacks() as callbacks:
        wsi = WorkstationImageFactory(image__from_path=http_image)
    recurse_callbacks(
        callbacks=callbacks,
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    try:
        with django_capture_on_commit_callbacks(execute=True):
            s1 = SessionFactory(workstation_image=wsi)
        s1.refresh_from_db()
        assert s1.status == s1.STARTED

        with django_capture_on_commit_callbacks(execute=True):
            s2 = SessionFactory(workstation_image=wsi)
        s2.refresh_from_db()
        assert s2.status == s2.FAILED

        s1.stop()

        with django_capture_on_commit_callbacks(execute=True):
            s3 = SessionFactory(workstation_image=wsi)
        s3.refresh_from_db()
        assert s3.status == s3.STARTED
    finally:
        stop_all_sessions()


@pytest.mark.django_db
def test_group_deletion():
    ws = WorkstationFactory()
    users_group = ws.users_group
    editors_group = ws.editors_group

    assert users_group
    assert editors_group

    Workstation.objects.filter(pk__in=[ws.pk]).delete()

    with pytest.raises(ObjectDoesNotExist):
        users_group.refresh_from_db()

    with pytest.raises(ObjectDoesNotExist):
        editors_group.refresh_from_db()


@pytest.mark.django_db
@pytest.mark.parametrize("group", ["users_group", "editors_group"])
def test_group_deletion_reverse(group):
    ws = WorkstationFactory()
    users_group = ws.users_group
    editors_group = ws.editors_group

    assert users_group
    assert editors_group

    with pytest.raises(ProtectedError):
        getattr(ws, group).delete()


@pytest.mark.django_db
def test_all_regions_are_in_settings(settings):
    for region in Session.Region.values:
        assert region in settings.WORKSTATIONS_RENDERING_SUBDOMAINS
        assert region in settings.DISALLOWED_CHALLENGE_NAMES


@pytest.mark.django_db
def test_staff_email_for_new_feedback():
    staff = UserFactory(is_staff=True)
    user = UserFactory()

    assert len(mail.outbox) == 0

    _ = FeedbackFactory()

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [staff.email]
    assert mail.outbox[0].to != [user.email]
    assert "New Session Feedback" in mail.outbox[0].subject


@pytest.mark.django_db
def test_extra_env_vars():
    session = Session(
        extra_env_vars=[
            {"name": "TEST", "value": "12345"},
            {
                "name": "GRAND_CHALLENGE_API_ROOT",
                "value": "should not be overwritten",
            },
        ],
        id="9863c19d-879f-411e-91da-eb5bcdcc1e41",
    )

    assert session.environment == {
        "CIRRUS_KEEP_ALIVE_METHOD": "old",
        "GRAND_CHALLENGE_API_ROOT": "https://testserver/api/v1/",
        "TEST": "12345",
        "WORKSTATION_SENTRY_DSN": "",
        "WORKSTATION_SESSION_ID": "9863c19d-879f-411e-91da-eb5bcdcc1e41",
        "INTERACTIVE_ALGORITHMS_LAMBDA_FUNCTIONS": "null",
        "AWS_DEFAULT_REGION": "eu-nl-1",
        "WORKSTATIONS_MAX_CONCURRENT_API_REQUESTS": str(
            settings.WORKSTATIONS_MAX_CONCURRENT_API_REQUESTS
        ),
    }


@pytest.mark.django_db
def test_session_cost_created_after_session():
    session = SessionFactory()

    assert SessionCost.objects.count() == 0

    session.stop()

    assert SessionCost.objects.count() == 1

    session_cost = SessionCost.objects.first()

    assert session_cost.session == session


@pytest.mark.django_db
def test_session_cost_retained_when_session_deleted():
    session = SessionFactory()
    session.stop()
    session_pk = session.pk
    session_cost = session.session_cost
    session_cost_pk = session_cost.pk

    session.delete()

    assert not Session.objects.filter(pk__in=[session_pk]).exists()
    assert SessionCost.objects.filter(pk__in=[session_cost_pk]).exists()

    session_cost.refresh_from_db()

    assert session_cost.session is None


@pytest.mark.django_db
def test_session_cost_retained_when_creator_deleted():
    session = SessionFactory()
    session.stop()
    session_cost = session.session_cost
    session_cost_pk = session_cost.pk

    session.creator.delete()

    assert SessionCost.objects.filter(pk__in=[session_cost_pk]).exists()

    session_cost.refresh_from_db()

    assert session_cost.creator is None


@pytest.mark.django_db
def test_session_cost_duration(mocker):
    fixed_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    session = SessionFactory()
    session.created = fixed_now - timedelta(minutes=5)
    mocker.patch(
        "grandchallenge.workstations.models.now",
        return_value=fixed_now,
    )

    session.stop()

    assert session.session_cost.duration == timedelta(minutes=5)


@pytest.mark.django_db
def test_session_cost_reader_studies():
    session = SessionFactory()
    reader_studies = ReaderStudyFactory.create_batch(2)
    session.reader_studies.set(reader_studies)

    session.stop()

    assert session.session_cost.reader_studies.count() == 2
    assert set(reader_studies) == set(
        session.session_cost.reader_studies.all()
    )


@pytest.mark.django_db
def test_session_cost_no_reader_studies():
    session = SessionFactory()
    session.stop()

    assert session.session_cost.reader_studies.count() == 0


@pytest.mark.django_db
def test_session_cost_interactive_algorithm():
    session = SessionFactory()
    question = QuestionFactory(
        interactive_algorithm=InteractiveAlgorithmChoices.ULS23_BASELINE,
    )
    session.reader_studies.add(question.reader_study)

    session.stop()

    assert session.session_cost.interactive_algorithms == [
        InteractiveAlgorithmChoices.ULS23_BASELINE.value
    ]


@pytest.mark.django_db
def test_session_cost_distinct_interactive_algorithms():
    session = SessionFactory()
    questions = QuestionFactory.create_batch(
        2,
        interactive_algorithm=InteractiveAlgorithmChoices.ULS23_BASELINE,
    )
    session.reader_studies.add(questions[0].reader_study)
    session.reader_studies.add(questions[1].reader_study)

    session.stop()

    assert session.session_cost.interactive_algorithms == [
        InteractiveAlgorithmChoices.ULS23_BASELINE.value
    ]


@pytest.mark.django_db
def test_session_cost_interactive_algorithms_credit_rate():
    session_without_interactive_alg = SessionFactory()
    question = QuestionFactory.create()
    session_without_interactive_alg.reader_studies.add(question.reader_study)
    session_without_interactive_alg.stop()

    assert session_without_interactive_alg.session_cost.credits_per_hour == 500

    session_with_interactive_alg = SessionFactory()
    question = QuestionFactory.create(
        interactive_algorithm=InteractiveAlgorithmChoices.ULS23_BASELINE,
    )
    session_with_interactive_alg.reader_studies.add(question.reader_study)
    session_with_interactive_alg.stop()

    assert session_with_interactive_alg.session_cost.credits_per_hour == 1000
