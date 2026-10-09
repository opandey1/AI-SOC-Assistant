"""Browser readiness checks on controlled HTML, without a running app or database."""

from pathlib import Path

import pytest

from browser_tests.support import wait_for_review_database


def test_database_readiness_waits_for_default_value(page, tmp_path):
    expected = tmp_path / "path with spaces" / "soc_feedback.db"
    page.set_content('<label for="database">Review database</label><input id="database">')
    field = page.get_by_role("textbox", name="Review database", exact=True)
    assert field.input_value() == ""
    assert Path(field.input_value()).resolve() != expected
    page.evaluate(
        """path => {
            setTimeout(() => { document.getElementById('database').value = path; }, 300);
        }""",
        arg=str(expected),
    )

    wait_for_review_database(page, expected)

    assert field.input_value() == str(expected)


@pytest.mark.parametrize("wrong", ["", "different-database.db"], ids=["empty", "wrong-path"])
def test_database_readiness_fails_closed_without_changing_input(page, tmp_path, wrong):
    expected = tmp_path / "soc_feedback.db"
    page.set_content('<label for="database">Review database</label><input id="database">')
    field = page.get_by_role("textbox", name="Review database", exact=True)
    field.fill(wrong)

    with pytest.raises(AssertionError):
        wait_for_review_database(page, expected, timeout=250)

    assert field.input_value() == wrong
    assert not expected.exists()
