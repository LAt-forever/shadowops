import shadowops


def test_package_exposes_initial_version() -> None:
    assert getattr(shadowops, "__version__", None) == "0.1.0"
