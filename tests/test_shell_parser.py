import pytest

from app.shell import ShellParseError, split_pipeline


def test_split_pipeline_respects_quotes():
    assert split_pipeline('grep "a|b" /docs | head -5') == ['grep "a|b" /docs', 'head -5']


def test_split_pipeline_rejects_empty_stage():
    with pytest.raises(ShellParseError):
        split_pipeline('ls | | head')
