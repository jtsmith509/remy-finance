import remy_finance
from remy_finance.cli import app


def test_package_imports() -> None:
    assert remy_finance.__version__


def test_cli_app_exists() -> None:
    assert app is not None
