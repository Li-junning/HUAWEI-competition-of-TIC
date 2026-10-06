"""Shared, host-bound source priors; a prior never establishes factual truth."""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SourceProfile:
    kind: str
    authority: float
    reason: str


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def source_profile(url: str | None) -> SourceProfile:
    host = (urlsplit(url or "").hostname or "").casefold().rstrip(".")
    if _matches(host, ("weibo.com", "zhihu.com", "tieba.baidu.com", "reddit.com",
                       "threads.net", "threads.com", "x.com", "twitter.com", "quora.com")):
        return SourceProfile("social", .25, "社区或社交平台，用户投稿不能作为独立事实依据")
    if _matches(host, ("fandom.com", "baijiahao.baidu.com", "blog.csdn.net", "jianshu.com", "blog.sciencenet.cn")):
        return SourceProfile("community", .3, "用户共建或个人投稿，需追溯原始出处")
    if _matches(host, ("open.spotify.com", "music.163.com", "y.qq.com", "music.apple.com")):
        return SourceProfile("entertainment", .3, "音乐作品页面，仅适用于作品信息核对")
    if _matches(host, ("wiktionary.org",)):
        return SourceProfile("dictionary", .4, "词典页面，仅适用于词义或语言用法核对")
    if _matches(host, ("gov.cn", "gov", "un.org", "who.int", "wmo.int", "ipcc.ch",
                       "imf.org", "worldbank.org", "oecd.org", "unesco.org")):
        return SourceProfile("official", .9, "政府或国际组织网站，仍需核对正文与适用条件")
    if _matches(host, ("edu", "ac.cn", "cas.cn", "edu.cn")):
        return SourceProfile("academic", .8, "高校或科研机构网站，仍需核对正文与适用条件")
    if _matches(host, ("kepu.gmw.cn", "kepuchina.cn", "cast.org.cn", "sciencenet.cn")):
        return SourceProfile("science", .8, "科研或科普机构平台，仍需核对作者、出处与语境")
    if _matches(host, ("wikipedia.org", "baike.baidu.com", "wikivoyage.org")):
        return SourceProfile("encyclopedia", .45, "百科资料，优先追溯其引用的原始来源")
    if _matches(host, ("xinhuanet.com", "news.cn", "people.com.cn", "cctv.com",
                       "chinanews.com.cn", "chinanews.com", "thepaper.cn", "gmw.cn")):
        return SourceProfile("news", .7, "新闻媒体报道，需区分报道事实、评论与引用")
    return SourceProfile("unknown", .55, "来源资质未确认，需结合独立来源交叉核验")


def source_allowed(url: str, claim_text: str, title: str = "") -> bool:
    # Catch homonymous works even when they live outside a known platform.
    genres = (("歌曲", "歌词", "专辑", "歌手", "音乐", "song", "album", "singer"),
              ("小说", "网文", "文学作品"), ("游戏攻略", "角色技能", "手游", "游戏"))
    for genre in genres:
        if any(word in title.casefold() for word in genre) and not any(word in claim_text.casefold() for word in genre):
            return False
    kind = source_profile(url).kind
    if kind in {"social", "community"}:
        return False
    if kind == "entertainment":
        return any(word in claim_text.casefold() for word in ("歌曲", "专辑", "歌手", "音乐", "song", "album", "singer"))
    if kind == "dictionary":
        return any(word in claim_text.casefold() for word in ("词义", "成语", "俗语", "谚语", "词语", "拼写", "读音", "翻译", "idiom", "meaning", "spelling"))
    return True
