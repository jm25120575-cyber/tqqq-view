"""링크를 넣으면 게시판에 붙여넣을 제목과 본문을 만들어 주는 Streamlit 앱.

실행: streamlit run app.py
"""

import json
from pathlib import Path

import requests
import streamlit as st

from extractor import extract

TEMPLATE_FILE = Path(__file__).with_name("template.json")

DEFAULT_TEMPLATE = {
    "title": "{title}",
    "body": "{my_text}\n\n{excerpt}\n\n출처: {site_name}\n{url}",
}

PLACEHOLDER_HELP = """사용할 수 있는 자리표시자
- `{title}` 페이지 제목
- `{description}` 페이지 요약(메타 설명)
- `{site_name}` 사이트 이름
- `{url}` 원문 링크
- `{image}` 대표 이미지 주소
- `{excerpt}` 본문 발췌 (아래 '발췌 길이'만큼)
- `{my_text}` 직접 쓴 내용"""


def load_template() -> dict:
    if TEMPLATE_FILE.exists():
        try:
            return {"title": DEFAULT_TEMPLATE["title"], "body": "",
                    **json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))}
        except json.JSONDecodeError:
            pass
    # 예시 글 칸은 비워 두고, 비어 있으면 결과를 만들 때 기본 틀을 쓴다
    return {"title": DEFAULT_TEMPLATE["title"], "body": ""}


def make_excerpt(text: str, limit: int) -> str:
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # 문장 중간에서 끊기지 않게 마지막 줄바꿈이나 마침표 뒤에서 자른다
    end = max(cut.rfind("\n"), cut.rfind(". ") + 1, cut.rfind("다.") + 2)
    if end > limit // 2:
        cut = cut[:end]
    return cut.rstrip() + " …"


def fill(template: str, values: dict) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    # 비어 있는 항목 때문에 생긴 빈 줄을 정리
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip()


st.set_page_config(page_title="링크 게시글 생성기", page_icon="📝", layout="centered")
st.title("링크 게시글 생성기")

if "template" not in st.session_state:
    st.session_state.template = load_template()

with st.sidebar:
    st.header("설정")
    tpl_title = st.text_input("제목 틀", st.session_state.template["title"])
    excerpt_len = st.slider("발췌 길이(글자)", 0, 3000, 500, step=100)
    st.markdown(PLACEHOLDER_HELP)

with st.form("fetch"):
    url = st.text_input("링크", placeholder="https://...")
    submitted = st.form_submit_button("가져오기", type="primary")

if submitted and url:
    with st.spinner("페이지를 읽는 중..."):
        try:
            st.session_state.page = extract(url)
        except requests.RequestException as e:
            st.session_state.pop("page", None)
            st.error(f"페이지를 가져오지 못했습니다: {e}")

page = st.session_state.get("page")
if page:
    with st.expander("가져온 내용", expanded=False):
        if page.image:
            st.image(page.image, width=240)
        st.write(f"**제목** {page.title or '(없음)'}")
        st.write(f"**사이트** {page.site_name}")
        st.write(f"**요약** {page.description or '(없음)'}")
        st.text_area("본문 전체", page.text or "(본문을 찾지 못했습니다)", height=200, disabled=True)

    tpl_body = st.text_area(
        "예시 글",
        st.session_state.template["body"],
        height=220,
        placeholder="여기에 예시 글을 붙여넣으세요.",
        help="예시 글을 붙여넣고, 링크 내용이 들어갈 자리에 {title}, {excerpt} 같은 "
             "자리표시자를 넣으세요. 비워 두면 기본 틀을 씁니다.",
    )
    col1, col2 = st.columns(2)
    if col1.button("예시 글·제목 틀 저장", use_container_width=True):
        st.session_state.template = {"title": tpl_title, "body": tpl_body}
        TEMPLATE_FILE.write_text(
            json.dumps(st.session_state.template, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        st.success("저장했습니다. 다음 실행 때도 그대로 불러옵니다.")
    if col2.button("기본값으로", use_container_width=True):
        TEMPLATE_FILE.unlink(missing_ok=True)
        st.session_state.template = load_template()
        st.rerun()

    my_text = st.text_area("직접 쓸 내용", placeholder="내 의견, 소개 문구 등을 적으세요. 예시 글의 {my_text} 자리에 들어갑니다.", height=150)

    values = {
        "title": page.title,
        "description": page.description,
        "site_name": page.site_name,
        "url": page.url,
        "image": page.image,
        "excerpt": make_excerpt(page.text, excerpt_len),
        "my_text": my_text.strip(),
    }

    st.subheader("결과")
    st.caption("오른쪽 위 복사 버튼을 눌러 게시판의 제목·본문 칸에 붙여넣으세요.")
    st.markdown("**제목**")
    st.code(fill(tpl_title, values), language=None)
    st.markdown("**본문 내용**")
    st.code(fill(tpl_body.strip() or DEFAULT_TEMPLATE["body"], values), language=None, wrap_lines=True)
