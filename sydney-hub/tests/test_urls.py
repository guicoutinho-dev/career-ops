import pytest

from hub.domain.urls import InvalidGroupUrl, normalize_group_url


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.facebook.com/groups/BrasileirosEmSydneyAustralia/",
        "facebook.com/groups/BrasileirosEmSydneyAustralia",
        "https://m.facebook.com/groups/BrasileirosEmSydneyAustralia?ref=share",
        "  https://web.facebook.com/groups/BrasileirosEmSydneyAustralia/posts/123/  ",
    ],
)
def test_variants_normalise_to_the_same_canonical_url(raw):
    assert normalize_group_url(raw) == "https://www.facebook.com/groups/BrasileirosEmSydneyAustralia/"


def test_numeric_group_id_is_accepted():
    assert normalize_group_url("https://www.facebook.com/groups/1434585006807674") == (
        "https://www.facebook.com/groups/1434585006807674/"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not a url",
        "https://example.com/groups/abc",
        "https://www.facebook.com/someprofile",
        "https://www.facebook.com/groups/",
        "https://evilfacebook.com/groups/abc",
        "javascript:alert(1)",
    ],
)
def test_invalid_urls_are_rejected(raw):
    with pytest.raises(InvalidGroupUrl):
        normalize_group_url(raw)
