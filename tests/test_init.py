def test_import_and_version():
    import ahtp
    assert isinstance(ahtp.__version__, str) and len(ahtp.__version__) > 0