"""Offline regressions for source noise seen in the evidence UI."""

import pytest

from app.extract import extract_claims
from app.judge import judge_claim
from app.providers.base import SearchProviderError, SearchResult
from app.retrieve import SearchBackedRetriever, build_queries
from app.schemas import ClaimLabel, EvidenceCluster, EvidenceItem, EvidenceRelation
from app.source_policy import source_profile


class SearchFixture:
    name = "fixture"

    def __init__(self, *batches):
        self.batches = batches
        self.calls = []

    def search(self, query, *, limit=5):
        self.calls.append(query)
        batch = self.batches[min(len(self.calls) - 1, len(self.batches) - 1)]
        if isinstance(batch, Exception):
            raise batch
        return batch[:limit]


def retrieve(text, *batches, maximum=5):
    claim = extract_claims(text, "t")[0][0]
    provider = SearchFixture(*batches)
    clusters = SearchBackedRetriever(provider, max_evidence=maximum).retrieve(claim)
    return claim, provider, [item for cluster in clusters for item in cluster.items]


def test_sunrise_ignores_forum_game_dictionary_and_music_results():
    text = "太阳从西边升起。"
    noise = [
        SearchResult("https://new-three-kingdoms.fandom.com/wiki/sun", title=text, content=text + "游戏攻略"),
        SearchResult("https://open.spotify.com/track/sun", title=text, content=text + "另一首歌"),
        SearchResult("https://en.wiktionary.org/wiki/sun", title=text, content=text + "成语词典"),
        SearchResult("https://www.reddit.com/r/islam/sun", title=text, content=text + "论坛讨论"),
    ]
    official = SearchResult("https://kepu.gmw.cn/sunrise", title="太阳升起方向",
                            content="太阳每天从东方升起。这是地球自转造成的视运动。")
    claim, provider, items = retrieve(text, noise, [official])
    assert [item.url for item in items] == [official.url]
    assert items[0].relation == EvidenceRelation.UNKNOWN
    assert "科普" in items[0].quality_reason
    assert claim.queries == provider.calls and len(provider.calls) <= 3
    assert any("方向" in query and "西边" not in query for query in provider.calls)
    assert any("site:ac.cn" in query for query in provider.calls)


def test_sound_claim_does_not_keep_light_speed_article_or_forum():
    rows = [
        SearchResult("https://evidentscientific.com/speedoflight", title="什么是光速？",
                     content="光在真空中传播速度最快，每秒约三十万千米。"),
        SearchResult("https://reddit.com/r/science/sound", content="声音在真空中传播最快。"),
        SearchResult("https://physics.ac.cn/sound", title="声音传播的条件",
                     content="声音的传播需要介质，声音不能在真空中传播。"),
    ]
    _, _, items = retrieve("声音在真空中传播最快。", rows)
    assert [item.url for item in items] == [rows[2].url]
    assert "不能" in items[0].excerpt


@pytest.mark.parametrize("text,property_word,answer", [
    ("声音在真空中传播最快。", "速度", "最快"),
    ("太阳从西方升起。", "方向", "西方"),
    ("因为地球自转方向是自东向西。", "方向", "自东向西"),
])
def test_topic_queries_remove_direction_and_comparative_answers(text, property_word, answer):
    queries = build_queries("c", action=text)
    assert queries[0].query == text
    assert any(property_word in q.query and answer not in q.query for q in queries[1:])


def test_irrelevant_official_page_and_title_only_match_are_filtered():
    text = "华为2024年的研发投入为1500亿元。"
    _, _, items = retrieve(text, [
        SearchResult("https://agency.gov.cn/notice", title=text, content="2024年会议共有1500人参加。"),
        SearchResult("https://company.example/report", title="华为年度报告",
                     content="华为2024年的研发投入为1797亿元。"),
    ])
    assert len(items) == 1 and "1797" in items[0].excerpt


def test_filters_do_not_fill_empty_slots_with_noise():
    _, _, items = retrieve("太阳从西方升起。", [
        SearchResult("https://reddit.com/r/sun", content="太阳从西方升起。"),
        SearchResult("https://agency.gov.cn/notice", content="停车管理会议通知。"),
    ])
    assert items == []


def test_actual_dictionary_and_music_topics_keep_relevant_leads():
    _, _, music = retrieve("歌曲Another Sunshine的歌手是某人。", [
        SearchResult("https://open.spotify.com/track/1", content="歌曲Another Sunshine的歌手是某人。"),
    ])
    _, _, dictionary = retrieve("这个成语的词义是罕见的事。", [
        SearchResult("https://en.wiktionary.org/wiki/word", content="这个成语的词义是罕见的事。"),
    ])
    assert music and dictionary


@pytest.mark.parametrize("url,kind", [
    ("https://sub.reddit.com/post", "social"),
    ("https://new-three-kingdoms.fandom.com/wiki/page", "community"),
    ("https://blog.sciencenet.cn/post", "community"),
    ("https://news.sciencenet.cn/post", "science"),
    ("https://kepu.gmw.cn/post", "science"),
    ("https://reddit.com.evil.example/post", "unknown"),
    ("https://evil.example/reddit.com", "unknown"),
    ("https://kepu.gmw.cn.evil.example/post", "unknown"),
])
def test_source_policy_uses_host_boundaries(url, kind):
    assert source_profile(url).kind == kind


@pytest.mark.parametrize("url", ["https://game.fandom.com/wiki/a", "https://reddit.com/a",
                                "https://open.spotify.com/track/a", "https://en.wiktionary.org/wiki/a"])
def test_legacy_low_quality_items_cannot_decide_a_claim(url):
    claim = extract_claims("太阳从西方升起。", "t")[0][0]
    item = EvidenceItem(evidence_id="e", url=url, excerpt=claim.source_text,
                        authority=1, relevance=1, time_fit=1, relation=EvidenceRelation.SUPPORTS)
    result = judge_claim(claim, [EvidenceCluster(cluster_id="ec", items=[item])])
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_rank_order_is_preserved_in_returned_clusters():
    text = "地球自转方向是自东向西。"
    _, _, items = retrieve(text, [
        SearchResult("https://unknown.example/post", content="地球自转方向是自东向西。" * 15),
        SearchResult("https://astro.ac.cn/rotation", content="地球自转方向是自西向东。"),
    ])
    assert len(items) == 2 and items[0].url == "https://astro.ac.cn/rotation"


def test_navigation_and_malformed_urls_do_not_become_evidence():
    _, _, items = retrieve("太阳从西方升起。", [
        SearchResult("https://[broken", content="太阳升起方向"),
        SearchResult("https://unknown.example/menu", content="太阳升起方向 Edit history What links here Permanent link"),
    ])
    assert items == []


def test_partial_search_failure_preserves_relevant_results():
    _, provider, items = retrieve("太阳从西方升起。",
        [SearchResult("https://astro.ac.cn/sun", content="太阳每天从东方升起。")],
        SearchProviderError("SEARCH_TIMEOUT"), [])
    assert len(items) == 1 and len(provider.calls) == 3


def test_query_budget_and_scope_are_preserved():
    claim, provider, _ = retrieve("在标准大气压下，纯水的沸点不是90摄氏度。", [])
    assert len(provider.calls) <= 3 and all(len(q) <= 160 for q in provider.calls)
    assert "不是90" in provider.calls[0]
    assert all("标准大气压" in q for q in provider.calls)
    assert claim.queries == provider.calls


def test_conditions_cannot_reintroduce_the_answer_into_neutral_query():
    queries = build_queries("c", action="声音在真空中传播最快。", entities=["声音"],
                            conditions=["在真空中传播最快"])
    assert "最快" in queries[0].query
    assert all("最快" not in q.query and "真空" in q.query for q in queries[1:])


def test_homonymous_work_on_unknown_site_is_filtered_by_title():
    _, _, items = retrieve("太阳从西方升起。", [
        SearchResult("https://unknown.example/story", title="太阳从西方升起 小说",
                     content="太阳从西方升起。"),
    ])
    assert not items


def test_permanent_failure_records_only_attempted_queries():
    claim, provider, items = retrieve("太阳从西方升起。",
        [SearchResult("https://astro.ac.cn/sun", content="太阳每天从东方升起。")],
        SearchProviderError("SEARCH_QUOTA"), [])
    assert len(items) == 1 and len(provider.calls) == 2
    assert claim.queries == provider.calls
