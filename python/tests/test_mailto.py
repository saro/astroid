from astroid_mail.modes.edit_message import parse_mailto


def test_simple_to_only():
    out = parse_mailto("mailto:alice@example.org")
    assert out["to"] == "alice@example.org"
    assert out["subject"] == "" and out["body"] == ""


def test_query_parameters():
    out = parse_mailto(
        "mailto:bob@example.org?subject=hello%20world&body=ping&cc=carol%40x.com")
    assert out["to"] == "bob@example.org"
    assert out["subject"] == "hello world"
    assert out["body"] == "ping"
    assert out["cc"] == "carol@x.com"


def test_unknown_fields_ignored():
    out = parse_mailto("mailto:x@y.z?foo=bar&body=ok")
    assert out["body"] == "ok"
    assert "foo" not in out


def test_no_scheme():
    out = parse_mailto("just@a.com?subject=plain")
    assert out["to"] == "just@a.com"
    assert out["subject"] == "plain"
