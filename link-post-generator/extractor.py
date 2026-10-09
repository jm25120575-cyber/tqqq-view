"""링크에서 제목, 설명, 대표 이미지, 본문을 뽑아낸다."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# 본문 후보로 먼저 찾아볼 영역 (네이버 블로그·뉴스, 티스토리, 일반 기사 순)
CONTENT_SELECTORS = [
    "div.se-main-container",
    "div#postViewArea",
    "article#dic_area",
    "div#newsct_article",
    "div.tt_article_useless_p_margin",
    "div.entry-content",
    "article",
    "main",
    "div#content",
    "div.content",
]

NOISE_TAGS = ["script", "style", "noscript", "iframe", "nav", "header", "footer",
              "aside", "form", "button", "svg"]


@dataclass
class PageInfo:
    url: str
    title: str = ""
    description: str = ""
    site_name: str = ""
    image: str = ""
    text: str = ""


def _fetch(url: str) -> requests.Response:
    res = requests.get(url, headers=HEADERS, timeout=15)
    res.raise_for_status()
    # charset 선언이 없으면 requests가 ISO-8859-1로 읽어 한글이 깨진다
    if "charset" not in res.headers.get("Content-Type", "").lower():
        res.encoding = res.apparent_encoding
    return res


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _naver_blog_frame(url: str, soup: BeautifulSoup) -> str | None:
    """네이버 블로그는 본문이 iframe(mainFrame) 안에 있다."""
    if "blog.naver.com" not in urlparse(url).netloc:
        return None
    frame = soup.find("iframe", id="mainFrame")
    if frame and frame.get("src"):
        return urljoin("https://blog.naver.com", frame["src"])
    return None


def _clean_text(node) -> str:
    for tag in node.find_all(NOISE_TAGS):
        tag.decompose()
    lines = [line.strip() for line in node.get_text("\n").splitlines()]
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", text)


def _main_text(soup: BeautifulSoup) -> str:
    best = ""
    for selector in CONTENT_SELECTORS:
        node = soup.select_one(selector)
        if node:
            text = _clean_text(node)
            if len(text) > 100:
                return text
            best = max(best, text, key=len)
    # 본문 영역이 짧거나 없으면 <p> 문단을 모은 것과 비교해 긴 쪽을 쓴다
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    fallback = "\n\n".join(p for p in paragraphs if len(p) > 30)
    return max(best, fallback, key=len)


def extract(url: str) -> PageInfo:
    url = url.strip()
    if not urlparse(url).scheme:
        url = "https://" + url

    res = _fetch(url)
    soup = BeautifulSoup(res.text, "lxml")

    frame_url = _naver_blog_frame(res.url, soup)
    if frame_url:
        soup = BeautifulSoup(_fetch(frame_url).text, "lxml")

    title = _meta(soup, "og:title", "twitter:title")
    if not title and soup.title:
        title = soup.title.get_text(strip=True)

    image = _meta(soup, "og:image", "twitter:image")
    if image:
        image = urljoin(res.url, image)

    return PageInfo(
        url=url,
        title=title,
        description=_meta(soup, "og:description", "description", "twitter:description"),
        site_name=_meta(soup, "og:site_name") or urlparse(res.url).netloc,
        image=image,
        text=_main_text(soup),
    )
