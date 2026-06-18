from thorondor_cli.secret import generate_searxng_secret


def test_generated_secret_is_token_safe_and_random():
    one = generate_searxng_secret()
    two = generate_searxng_secret()
    assert one
    assert two
    assert one != two
    assert not set("+/=") & set(one)
    assert not set("+/=") & set(two)
