from scraper.normalize import canonical_url, make_article_id, dedupe_by_id


def test_canonical_url_strips_tracking_params():
    a = canonical_url("https://Example.com/news/story-1?utm_source=fb&utm_medium=cpc")
    b = canonical_url("https://example.com/news/story-1")
    assert a == b


def test_canonical_url_strips_www_and_trailing_slash():
    a = canonical_url("https://www.example.com/news/story-1/")
    b = canonical_url("https://example.com/news/story-1")
    assert a == b


def test_canonical_url_keeps_non_tracking_query_params():
    url = canonical_url("https://example.com/article?id=123")
    assert "id=123" in url


def test_canonical_url_drops_fragment():
    url = canonical_url("https://example.com/article?id=123#section2")
    assert "#" not in url


def test_make_article_id_deterministic_for_same_url():
    id1 = make_article_id("https://example.com/news/story-1?utm_source=fb")
    id2 = make_article_id("https://www.example.com/news/story-1/")
    assert id1 == id2


def test_make_article_id_differs_for_different_urls():
    id1 = make_article_id("https://example.com/news/story-1")
    id2 = make_article_id("https://example.com/news/story-2")
    assert id1 != id2


def test_dedupe_by_id_keeps_first_occurrence():
    articles = [
        {"id": "a", "title": "first"},
        {"id": "b", "title": "second"},
        {"id": "a", "title": "duplicate"},
    ]
    result = dedupe_by_id(articles)
    assert len(result) == 2
    assert result[0]["title"] == "first"
