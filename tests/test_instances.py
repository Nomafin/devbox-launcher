from launcher import instances
from launcher.discovery import slugify


def test_slug_joins_project_and_name():
    assert instances.slug("homelab", "fix-auth") == "homelab--fix-auth"


def test_split_returns_the_pair():
    assert instances.split("homelab--fix-auth") == ("homelab", "fix-auth")


def test_split_is_none_for_a_plain_project_slug():
    assert instances.split("homelab") is None
    assert instances.split("--fix-auth") is None
    assert instances.split("homelab--") is None


def test_split_keeps_a_dashed_project_slug_whole():
    # kauppalehti-xenforo is a real project; its single dashes are not separators
    assert instances.split("kauppalehti-xenforo--wip") == ("kauppalehti-xenforo", "wip")


def test_valid_accepts_what_slugify_leaves_alone():
    assert instances.valid("fix-auth") is True
    assert instances.valid("wip2") is True


def test_valid_rejects_anything_that_would_be_rewritten():
    for name in ("Fix Auth", "fix_auth", "..", "", "fix--auth", "-wip", "wip/x"):
        assert instances.valid(name) is False, name


def test_no_project_name_can_slugify_into_an_instance_slug():
    # the separator is safe only because slugify collapses punctuation runs
    for name in ("homelab -- fix", "homelab__fix", "homelab.. fix"):
        assert instances.SEP not in slugify(name), name
