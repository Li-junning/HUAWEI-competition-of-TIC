from app.retrieve import EvidenceItem, cluster_evidence, _relevant_excerpt, build_queries, SearchBackedRetriever
from app.providers import DeterministicMockSearchProvider
from app.providers.base import SearchResult
from app.schemas import Claim

def test_excerpt_finds_body_after_navigation():
    text = ('目录\n语言\n历史\n' * 200) + '犬属于哺乳纲，而节肢动物具有外骨骼。'
    out = _relevant_excerpt(text, '狗是节肢动物', '')
    assert '犬属于哺乳纲' in out and len(out) <= 1000 and out in text

def test_cluster_unions_same_site_or_hash():
    def item(i, url, body):
        return EvidenceItem(evidence_id=i, url=url, title='', publisher=None, published_at=None, retrieved_at='', excerpt=body, content_hash=body)
    clusters = cluster_evidence([item('a','https://a.com/x','X'), item('b','https://b.org/x','X'), item('c','https://b.org/y','Y'), item('d','https://news.a.com/z','Z')])
    assert len(clusters) == 1

def test_neutral_queries_are_recordable():
    qs = [q.query for q in build_queries('c', action='X的Y是Z')]
    assert qs[0] == 'X的Y是Z' and 'Z' not in qs[1]


def test_named_treaty_search_preserves_evidence_despite_false_context():
    text = '三国时期，诸葛亮与凯撒大帝在华盛顿签署《马关条约》，'
    queries = build_queries('c', action=text, conditions=['在华盛顿签署《马关条约》'])
    assert queries[0].query == text
    assert queries[1].query == '马关条约'
    provider = DeterministicMockSearchProvider({'马关条约': [
        SearchResult('https://history.example.org/treaty', content='马关条约于1895年签订，涉及割地与赔款。')
    ]})
    claim = Claim(claim_id='c', task_id='t', source_text=text, char_start=0,
                  char_end=len(text), normalized_claim=text)
    clusters = SearchBackedRetriever(provider).retrieve(claim)
    assert len(claim.queries) <= 3
    assert clusters and clusters[0].items[0].relation.value == 'unknown'
    assert claim.normalized_claim == text


def test_query_variants_do_not_repeat_only_punctuation_changes():
    queries = build_queries('c', action='三国时期，甲与乙签署协议，', conditions=['签署协议'])
    keys = [__import__('re').sub(r'[\s，,。]+', '', q.query) for q in queries]
    assert len(keys) == len(set(keys))

def test_later_query_independent_source_survives():
    first = [SearchResult(f'https://p{i}.gov.cn/x', content='猫的颜色有黑色。') for i in range(5)]
    provider = DeterministicMockSearchProvider({'猫的颜色是黑色': first, '猫 颜色': [SearchResult('https://source.edu.cn/f', content='猫的颜色有黑色、白色等。')]})
    claim = Claim(claim_id='c_x', task_id='t_x', source_text='猫的颜色是黑色', char_start=0, char_end=7, normalized_claim='猫的颜色是黑色')
    clusters = SearchBackedRetriever(provider, max_evidence=5).retrieve(claim)
    assert any('source.edu.cn' in (i.url or '') for c in clusters for i in c.items)

def test_body_ranks_ahead_of_social_and_snippet():
    social = SearchResult('https://x.com/post', content='事实的社交讨论')
    snippet = SearchResult('https://official.gov.cn/a', content='事实摘要', metadata={'content_kind':'search_snippet'})
    body = SearchResult('https://plain.org/f', content='事实的普通正文')
    provider = DeterministicMockSearchProvider({'事实': [social, snippet, body]})
    claim = Claim(claim_id='c_y', task_id='t_y', source_text='事实', char_start=0, char_end=2, normalized_claim='事实')
    clusters = SearchBackedRetriever(provider, max_evidence=1).retrieve(claim)
    assert clusters[0].items[0].url == 'https://plain.org/f'
