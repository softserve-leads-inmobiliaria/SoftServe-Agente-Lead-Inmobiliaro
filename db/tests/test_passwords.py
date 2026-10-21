"""B-17: las contraseñas del panel se guardan como scrypt con sal propia y nunca en claro."""
from db.passwords import hash_password, verify_password


def test_the_right_password_verifies_and_a_wrong_one_does_not():
    stored = hash_password("asesor-demo")
    assert verify_password("asesor-demo", stored)
    assert not verify_password("asesor-dem0", stored)
    assert not verify_password("", stored)


def test_the_hash_never_contains_the_password_and_uses_its_own_salt():
    first, second = hash_password("asesor-demo"), hash_password("asesor-demo")
    assert "asesor-demo" not in first
    assert first.startswith("scrypt$")
    assert first != second  # sal por usuario: la misma contraseña no da el mismo hash


def test_a_malformed_hash_never_verifies():
    for stored in ("", "texto-plano", "bcrypt$1$2$3$YQ==$YQ==", "scrypt$x$8$1$YQ==$YQ==", "scrypt$16384$8$1$!!$!!"):
        assert not verify_password("asesor-demo", stored)
