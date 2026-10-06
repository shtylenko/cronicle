import pytest

from cronicle.nlparse import parse_natural_schedule as parse


@pytest.mark.parametrize("text,expected", [
    ("Every Wednesday at 5pm", "0 17 * * 3"),
    ("every wednesday at 5pm", "0 17 * * 3"),
    ("Every day at 9am", "0 9 * * *"),
    ("daily at midnight", "0 0 * * *"),
    ("daily", "0 0 * * *"),
    ("every 15 minutes", "*/15 * * * *"),
    ("every minute", "* * * * *"),
    ("every hour", "0 * * * *"),
    ("hourly", "0 * * * *"),
    ("every 2 hours", "0 */2 * * *"),
    ("every 2 hours on weekdays", "0 */2 * * 1,2,3,4,5"),
    ("mon and fri at 8:30am", "30 8 * * 1,5"),
    ("Mon, Wed, Fri at 9am", "0 9 * * 1,3,5"),
    ("mon-fri at 9am", "0 9 * * 1,2,3,4,5"),
    ("weekdays at noon", "0 12 * * 1,2,3,4,5"),
    ("weekends at 10am", "0 10 * * 0,6"),
    ("sunday at 9am", "0 9 * * 0"),
    ("fridays at 5pm", "0 17 * * 5"),
    ("every month on the 1st at 3pm", "0 15 1 * *"),
    ("monthly at midnight", "0 0 1 * *"),
    ("weekly at 8am", "0 8 * * 0"),
    ("yearly at noon", "0 12 1 1 *"),
    ("at 17:30", "30 17 * * *"),
    ("5pm", "0 17 * * *"),
    ("12am", "0 0 * * *"),
    ("12pm", "0 12 * * *"),
    ("tuesday to thursday at 6pm", "0 18 * * 2,3,4"),
])
def test_parses(text, expected):
    assert parse(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "do the thing soon",
    "sometime next week maybe",
    "every other wednesday at 5pm",  # would silently mean every wednesday
    "first monday at 9am",
    "twice daily",
    "9am and 5pm",  # two times
    "every 2 hours at 9am",  # contradictory
    "13pm",
    "every 99 minutes",
    "on the 5th on fridays at noon",  # dom + dow together
])
def test_rejects(text):
    with pytest.raises(ValueError):
        parse(text)
