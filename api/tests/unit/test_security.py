import uuid

import pytest

from app.security import (
    InvalidEmail, code_hmac, constant_time_equal, hash_token, new_code, new_token, normalize_email,
)


def test_tokens_are_long_unique_and_hash_to_32_bytes():
    tokens = {new_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 43 for t in tokens)
    assert len(hash_token("abc")) == 32
    assert hash_token("abc") == hash_token("abc") and hash_token("abc") != hash_token("abd")


def test_codes_are_always_six_digits():
    for _ in range(500):
        code = new_code()
        assert len(code) == 6 and code.isdigit()


def test_code_hmac_depends_on_every_input():
    cid, pepper = uuid.uuid4(), b"p" * 32
    base = code_hmac(pepper, "a@b.test", cid, "123456")
    assert base == code_hmac(pepper, "a@b.test", cid, "123456")
    assert base != code_hmac(pepper, "c@b.test", cid, "123456")
    assert base != code_hmac(pepper, "a@b.test", uuid.uuid4(), "123456")
    assert base != code_hmac(pepper, "a@b.test", cid, "123457")
    assert base != code_hmac(b"q" * 32, "a@b.test", cid, "123456")
    assert b"123456" not in base


def test_constant_time_compare():
    assert constant_time_equal(b"abc", b"abc")
    assert not constant_time_equal(b"abc", b"abd")
    assert not constant_time_equal(b"abc", b"abcd")


def test_email_normalisation():
    assert normalize_email("  Alice@Example.COM ") == "alice@example.com"
    assert normalize_email("bob+tag@sub.example.org") == "bob+tag@sub.example.org"


@pytest.mark.parametrize("bad", ["", "   ", "nope", "a@", "@b.test", "a b@c.test", "a@b", "x" * 250 + "@example.com"])
def test_malformed_emails_are_rejected(bad):
    with pytest.raises(InvalidEmail):
        normalize_email(bad)
