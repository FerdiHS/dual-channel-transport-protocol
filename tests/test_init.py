"""
Unit test for importing the dctp module and checking its version attribute.
"""


def test_import_and_version():
    """
    Test that the dctp module can be imported and has a valid __version__ attribute.
    """
    import dctp

    assert isinstance(dctp.__version__, str) and len(dctp.__version__) > 0
