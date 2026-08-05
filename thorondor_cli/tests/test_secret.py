from thorondor_cli.secret import generate_crawl4ai_api_key, generate_searxng_secret


def test_generated_secret_is_token_safe_and_random():
    one = generate_searxng_secret()
    two = generate_searxng_secret()
    assert one
    assert two
    assert one != two
    assert not set("+/=") & set(one)
    assert not set("+/=") & set(two)


def test_generated_crawl4ai_api_key_is_hex_and_random():
    one = generate_crawl4ai_api_key()
    two = generate_crawl4ai_api_key()
    assert len(one) == 64
    assert len(two) == 64
    assert one != two
    assert all(character in "0123456789abcdef" for character in one + two)
