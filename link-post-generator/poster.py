"""브라우저를 띄워 게시판에 글을 올린다.

사용자 PC에 설치된 크롬(없으면 엣지)을 화면에 보이게 띄운다.
로그인과 제목·본문 입력, 등록 버튼 클릭은 프로그램이 하고,
"로봇이 아닙니다"나 Cloudflare 확인은 사람이 직접 누른다.
"""

import os
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright


class StopRequested(Exception):
    pass


class PostError(Exception):
    pass


@dataclass
class Settings:
    write_url: str
    login_url: str
    user_id: str
    password: str
    profile_dir: str


# 페이지 안에서 실행해 입력 칸과 버튼을 찾는 스크립트.
# 찾은 요소에 data-lpg 속성을 붙여 두고, 파이썬 쪽에서는 그 속성으로 다룬다.
FIND_FIELDS_JS = r"""
() => {
  const visible = el => !!el && el.offsetParent !== null && !el.disabled;
  const first = (sels, check = visible) => {
    for (const s of sels) {
      for (const el of document.querySelectorAll(s)) if (check(el)) return el;
    }
    return null;
  };
  document.querySelectorAll('[data-lpg]').forEach(el => el.removeAttribute('data-lpg'));

  const title = first([
    'input[name="wr_subject"]', '#wr_subject', 'input[name="subject"]', 'input[name="title"]',
    'input[name*="subject"]', 'input[name*="title"]', 'input[placeholder*="제목"]',
  ]);
  let body = first(['textarea[name="wr_content"]', '#wr_content', 'textarea[name="content"]',
                    'textarea[name*="content"]'], el => !!el);
  if (!body) {
    let best = null;
    for (const el of document.querySelectorAll('textarea')) {
      if (!visible(el) || el.name === 'g-recaptcha-response') continue;
      if (!best || el.offsetHeight > best.offsetHeight) best = el;
    }
    body = best;
  }
  const password = first(['input[type="password"]']);

  let submit = null;
  const form = (title && title.form) || (body && body.form);
  if (form) {
    submit = first(['#btn_submit'], el => visible(el) && form.contains(el));
    if (!submit) {
      const buttons = [...form.querySelectorAll('button, input[type="submit"]')].filter(visible);
      const label = el => (el.innerText || el.value || '').trim();
      submit = buttons.find(b => /작성|등록|완료|글쓰기|올리기/.test(label(b)) && !/임시|취소/.test(label(b)))
            || buttons.find(b => b.type === 'submit' && !/임시|취소/.test(label(b)));
    }
  }
  const recaptcha = document.querySelector('[name="g-recaptcha-response"], .g-recaptcha, iframe[src*="recaptcha"]');

  if (title) title.setAttribute('data-lpg', 'title');
  if (body) body.setAttribute('data-lpg', 'body');
  if (submit) submit.setAttribute('data-lpg', 'submit');
  if (password) password.setAttribute('data-lpg', 'password');
  return {title: !!title, body: !!body, submit: !!submit, password: !!password, recaptcha: !!recaptcha};
}
"""

# 로그인 폼에서 아이디 칸과 로그인 버튼을 찾는다
FIND_LOGIN_JS = r"""
() => {
  const visible = el => !!el && el.offsetParent !== null && !el.disabled;
  const pw = [...document.querySelectorAll('input[type="password"]')].find(visible);
  if (!pw) return false;
  const scope = pw.form || document;
  const inputs = [...scope.querySelectorAll('input')].filter(visible);
  const idx = inputs.indexOf(pw);
  const id = scope.querySelector('input[name="mb_id"]')
          || inputs.slice(0, idx).reverse().find(el => ['text', 'email', 'tel', ''].includes(el.type));
  const btn = [...scope.querySelectorAll('button, input[type="submit"]')].filter(visible)
          .find(b => /로그인|login|확인/i.test(b.innerText || b.value || '') || b.type === 'submit');
  pw.setAttribute('data-lpg', 'login-pw');
  if (id) id.setAttribute('data-lpg', 'login-id');
  if (btn) btn.setAttribute('data-lpg', 'login-btn');
  return true;
}
"""

RECAPTCHA_DONE_JS = r"""
() => {
  const r = document.querySelector('[name="g-recaptcha-response"]');
  return !!r && r.value.length > 0;
}
"""


class Poster:
    """게시판 글쓰기 한 세션. 반드시 같은 스레드에서 열고 쓰고 닫는다."""

    def __init__(self, settings: Settings, log: Callable[[str], None], should_stop: Callable[[], bool]):
        self.s = settings
        self.log = log
        self.should_stop = should_stop
        self.dialogs: list[str] = []
        self._pw = None
        self._ctx = None
        self.page: Page | None = None

    # ---------- 브라우저 ----------

    def open(self):
        self._pw = sync_playwright().start()
        last_error = None
        # LPG_BROWSER_PATH: 테스트용으로 특정 브라우저 실행 파일을 쓸 때
        custom = os.environ.get("LPG_BROWSER_PATH")
        candidates = [({"executable_path": custom}, "지정한")] if custom else \
            [({"channel": "chrome"}, "크롬"), ({"channel": "msedge"}, "엣지")]
        for opts, name in candidates:
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(
                    self.s.profile_dir, **opts, headless=False,
                    no_viewport=True, args=["--start-maximized"],
                )
                self.log(f"{name} 브라우저를 열었습니다.")
                break
            except PlaywrightError as e:
                last_error = e
        else:
            raise PostError(f"크롬이나 엣지를 열지 못했습니다. 크롬을 설치해 주세요.\n{last_error}")
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.page.on("dialog", self._on_dialog)

    def close(self):
        for closer in (lambda: self._ctx and self._ctx.close(), lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:
                pass

    def _on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        self.log(f"사이트 알림: {dialog.message}")
        try:
            dialog.accept()
        except PlaywrightError:
            pass

    # ---------- 공통 ----------

    def _sleep(self, seconds: float):
        end = time.time() + seconds
        while time.time() < end:
            if self.should_stop():
                raise StopRequested()
            self.page.wait_for_timeout(300)

    def _find(self) -> dict:
        try:
            return self.page.evaluate(FIND_FIELDS_JS)
        except PlaywrightError:
            # 페이지 이동 중이면 잠시 뒤 다시 찾는다
            return {"title": False, "body": False, "submit": False, "password": False, "recaptcha": False}

    def _wait_for(self, check: Callable[[dict], bool], timeout: float, waiting_msg: str, every: float = 20) -> dict:
        start = last_msg = time.time()
        while True:
            found = self._find()
            if check(found):
                return found
            if time.time() - start > timeout:
                raise PostError(waiting_msg)
            if time.time() - last_msg > every:
                self.log(waiting_msg)
                last_msg = time.time()
            self._sleep(1)

    def _is_write_page(self) -> bool:
        return urlparse(self.page.url).path.rstrip("/") == urlparse(self.s.write_url).path.rstrip("/")

    # ---------- 로그인 ----------

    def login(self):
        self.log("로그인 상태를 확인합니다.")
        self.dialogs.clear()
        self.page.goto(self.s.write_url, wait_until="domcontentloaded")
        found = self._wait_for(
            lambda f: f["title"] or f["password"], 300,
            "글쓰기 화면을 기다리는 중입니다. 브라우저에 보안 확인 화면이 떠 있으면 직접 눌러 주세요.",
        )
        if found["title"] and not found["password"]:
            self.log("이미 로그인되어 있습니다.")
            return
        self._do_login()

    def _do_login(self):
        if not self.s.user_id or not self.s.password:
            raise PostError("로그인이 필요합니다. [설정]에 아이디와 비밀번호를 넣어 주세요.")
        if not self.page.evaluate(FIND_LOGIN_JS):
            self.log("로그인 화면으로 이동합니다.")
            self.page.goto(self.s.login_url, wait_until="domcontentloaded")
            self._wait_for(lambda f: f["password"], 300,
                           "로그인 화면을 기다리는 중입니다. 보안 확인 화면이 떠 있으면 직접 눌러 주세요.")
            if not self.page.evaluate(FIND_LOGIN_JS):
                raise PostError("로그인 칸을 찾지 못했습니다. [설정]의 로그인 주소를 확인해 주세요.")

        self.log("자동 로그인합니다.")
        self.dialogs.clear()
        id_box = self.page.locator('[data-lpg="login-id"]')
        if id_box.count():
            id_box.fill(self.s.user_id)
        self.page.locator('[data-lpg="login-pw"]').fill(self.s.password)
        btn = self.page.locator('[data-lpg="login-btn"]')
        if btn.count():
            btn.click()
        else:
            self.page.locator('[data-lpg="login-pw"]').press("Enter")
        self._sleep(3)
        if self.dialogs:
            raise PostError(f"로그인하지 못했습니다: {self.dialogs[-1]}")

        self.page.goto(self.s.write_url, wait_until="domcontentloaded")
        found = self._wait_for(lambda f: f["title"] or f["password"], 120,
                               "로그인 후 글쓰기 화면을 기다리는 중입니다.")
        if found["password"] and not found["title"]:
            raise PostError("로그인이 되지 않았습니다. 아이디와 비밀번호를 확인해 주세요.")
        self.log("로그인했습니다.")

    # ---------- 글쓰기 ----------

    def post(self, title: str, body: str) -> str:
        """글 하나를 올리고, 올라간 글의 주소를 돌려준다."""
        self.dialogs.clear()
        self.page.goto(self.s.write_url, wait_until="domcontentloaded")
        found = self._wait_for(
            lambda f: (f["title"] and f["body"]) or f["password"], 300,
            "글쓰기 화면을 기다리는 중입니다. 보안 확인 화면이 떠 있으면 직접 눌러 주세요.",
        )
        if found["password"] and not found["title"]:
            self._do_login()
            found = self._find()
        if not (found["title"] and found["body"]):
            raise PostError("글쓰기 화면에서 제목이나 본문 칸을 찾지 못했습니다.")

        self.page.locator('[data-lpg="title"]').fill(title)
        body_box = self.page.locator('[data-lpg="body"]')
        if body_box.is_visible():
            body_box.fill(body)
        else:
            # 본문 칸이 숨어 있으면(편집기 사용) 값만 직접 넣는다
            body_box.evaluate("(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); }", body)
        self.log("제목과 본문을 넣었습니다.")

        if found["recaptcha"] and found["submit"]:
            self.page.locator('[data-lpg="submit"]').scroll_into_view_if_needed()
            self.log("브라우저에서 '로봇이 아닙니다'를 눌러 주세요. 누르면 자동으로 등록합니다.")
            while not self.page.evaluate(RECAPTCHA_DONE_JS):
                if not self._is_write_page():
                    break  # 사용자가 직접 등록을 누른 경우
                self._sleep(1)
            else:
                self._find()  # 다시 찾아서 data-lpg 표시를 새로 붙인다
                self.page.locator('[data-lpg="submit"]').click()
                self.log("등록 버튼을 눌렀습니다.")
        elif found["submit"]:
            self.page.locator('[data-lpg="submit"]').click()
            self.log("등록 버튼을 눌렀습니다.")
        else:
            self.log("등록 버튼을 찾지 못했습니다. 브라우저에서 직접 등록을 눌러 주세요.")

        return self._wait_posted()

    def _wait_posted(self) -> str:
        start = time.time()
        seen = len(self.dialogs)
        while time.time() - start < 600:
            if len(self.dialogs) > seen:
                # 등록 실패 시 사이트가 알림을 띄우고 글쓰기 화면으로 되돌린다
                raise PostError(f"등록되지 않았습니다: {self.dialogs[-1]}")
            if not self._is_write_page():
                # 처리 페이지(write_update 등)를 지나 글 화면에 도착할 때까지 잠시 지켜본다
                self._sleep(3)
                if len(self.dialogs) > seen:
                    raise PostError(f"등록되지 않았습니다: {self.dialogs[-1]}")
                if not self._is_write_page() and "update" not in self.page.url:
                    return self.page.url
            self._sleep(1)
        raise PostError("10분 동안 등록되지 않아 건너뜁니다.")
