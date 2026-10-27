def test_package_can_be_imported() -> None:
    import crypto_harness

    assert crypto_harness.__version__ == "0.1.0"
