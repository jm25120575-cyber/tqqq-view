"""링크 게시글 생성기 - 윈도우 창 프로그램.

링크를 넣고 [가져오기]를 누르면 페이지 내용을 읽어, 예시 글 틀에 채운
제목과 본문을 만든다. [복사] 버튼으로 게시판에 붙여넣는다.
"""

import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from extractor import PageInfo, extract

APP_NAME = "링크 게시글 생성기"
SAVE_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LinkPostGenerator"
SAVE_FILE = SAVE_DIR / "template.json"

DEFAULT_BODY = "{내글}\n\n{발췌}\n\n출처: {사이트}\n{링크}"

# 버튼 이름, 자리표시자
PLACEHOLDERS = [
    ("제목", "{제목}"),
    ("본문 발췌", "{발췌}"),
    ("요약", "{요약}"),
    ("사이트 이름", "{사이트}"),
    ("링크 주소", "{링크}"),
    ("내가 쓴 글", "{내글}"),
]

FONT = ("맑은 고딕", 10)
FONT_BOLD = ("맑은 고딕", 10, "bold")


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
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip()


def load_saved() -> dict:
    try:
        return json.loads(SAVE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("900x860")
        self.minsize(700, 640)
        self.option_add("*Font", FONT)
        self.page: PageInfo | None = None

        saved = load_saved()
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)

        # 1. 링크
        ttk.Label(root, text="① 링크", font=FONT_BOLD).grid(row=0, column=0, sticky="w")
        link_row = ttk.Frame(root)
        link_row.grid(row=1, column=0, sticky="ew", pady=(2, 4))
        link_row.columnconfigure(0, weight=1)
        self.url_var = tk.StringVar()
        url_entry = ttk.Entry(link_row, textvariable=self.url_var)
        url_entry.grid(row=0, column=0, sticky="ew", ipady=3)
        url_entry.bind("<Return>", lambda e: self.fetch())
        self.fetch_btn = ttk.Button(link_row, text="가져오기", command=self.fetch)
        self.fetch_btn.grid(row=0, column=1, padx=(6, 0))
        self.status = ttk.Label(root, text="링크를 붙여넣고 [가져오기]를 누르세요.", foreground="#666")
        self.status.grid(row=2, column=0, sticky="w", pady=(0, 8))

        # 2. 예시 글
        head = ttk.Frame(root)
        head.grid(row=3, column=0, sticky="ew")
        ttk.Label(head, text="② 예시 글", font=FONT_BOLD).pack(side="left")
        ttk.Button(head, text="예시 글 저장", command=self.save).pack(side="right")
        ttk.Label(
            root,
            text="예시 글을 붙여넣고, 링크 내용이 들어갈 자리에 커서를 둔 뒤 아래 버튼을 누르세요. "
                 "비워 두면 기본 틀을 씁니다.",
            foreground="#666", wraplength=860, justify="left",
        ).grid(row=4, column=0, sticky="w")
        btns = ttk.Frame(root)
        btns.grid(row=5, column=0, sticky="w", pady=(4, 2))
        for label, ph in PLACEHOLDERS:
            ttk.Button(btns, text=f"+ {label}", command=lambda p=ph: self.insert_placeholder(p)).pack(side="left", padx=(0, 4))
        self.example = self._text(root, row=6, height=8)
        self.example.insert("1.0", saved.get("body", ""))

        excerpt_row = ttk.Frame(root)
        excerpt_row.grid(row=7, column=0, sticky="w", pady=(2, 8))
        ttk.Label(excerpt_row, text="본문 발췌 길이(글자 수):").pack(side="left")
        self.excerpt_len = tk.IntVar(value=saved.get("excerpt_len", 500))
        ttk.Spinbox(excerpt_row, from_=0, to=5000, increment=100, width=7,
                    textvariable=self.excerpt_len, command=self.update_result).pack(side="left", padx=4)

        # 3. 내가 쓸 내용
        ttk.Label(root, text="③ 내가 쓸 내용", font=FONT_BOLD).grid(row=8, column=0, sticky="w")
        self.my_text = self._text(root, row=9, height=4)

        # 4. 결과
        ttk.Separator(root).grid(row=10, column=0, sticky="ew", pady=10)
        res_head = ttk.Frame(root)
        res_head.grid(row=11, column=0, sticky="ew")
        ttk.Label(res_head, text="④ 결과 - 제목", font=FONT_BOLD).pack(side="left")
        ttk.Button(res_head, text="제목 복사", command=lambda: self.copy(self.result_title.get())).pack(side="right")
        self.result_title = tk.StringVar()
        ttk.Entry(root, textvariable=self.result_title).grid(row=12, column=0, sticky="ew", ipady=3, pady=(2, 8))

        body_head = ttk.Frame(root)
        body_head.grid(row=13, column=0, sticky="ew")
        ttk.Label(body_head, text="④ 결과 - 본문 내용", font=FONT_BOLD).pack(side="left")
        ttk.Button(body_head, text="본문 복사", command=lambda: self.copy(self.result_body.get("1.0", "end").strip())).pack(side="right")
        self.result_body = self._text(root, row=14, height=12)
        root.rowconfigure(14, weight=1)

        for widget in (self.example, self.my_text):
            widget.bind("<KeyRelease>", lambda e: self.update_result())
        self.excerpt_len.trace_add("write", lambda *a: self.update_result())
        url_entry.focus_set()

    def _text(self, parent, row: int, height: int) -> tk.Text:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky="nsew", pady=(2, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = tk.Text(frame, height=height, wrap="word", undo=True, relief="solid", borderwidth=1, padx=6, pady=4)
        text.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(frame, command=text.yview)
        bar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=bar.set)
        return text

    def insert_placeholder(self, placeholder: str):
        self.example.insert("insert", placeholder)
        self.example.focus_set()
        self.update_result()

    def fetch(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showinfo(APP_NAME, "링크를 먼저 넣어 주세요.")
            return
        self.fetch_btn.configure(state="disabled")
        self.status.configure(text="페이지를 읽는 중...", foreground="#666")

        def work():
            try:
                page = extract(url)
                self.after(0, self.on_fetched, page, None)
            except Exception as e:  # 네트워크·주소 오류 등은 모두 화면에 보여 준다
                self.after(0, self.on_fetched, None, e)

        threading.Thread(target=work, daemon=True).start()

    def on_fetched(self, page: PageInfo | None, error: Exception | None):
        self.fetch_btn.configure(state="normal")
        if error:
            self.status.configure(text="가져오지 못했습니다. 링크를 확인해 주세요.", foreground="#c00")
            messagebox.showerror(APP_NAME, f"페이지를 가져오지 못했습니다.\n\n{error}")
            return
        self.page = page
        note = "" if page.text else " (본문은 찾지 못했습니다)"
        self.status.configure(text=f"가져옴: {page.title or page.url}{note}", foreground="#070")
        self.update_result()

    def update_result(self):
        if not self.page:
            return
        try:
            limit = int(self.excerpt_len.get())
        except (tk.TclError, ValueError):
            limit = 500
        p = self.page
        values = {
            "제목": p.title,
            "발췌": make_excerpt(p.text, limit),
            "요약": p.description,
            "사이트": p.site_name,
            "링크": p.url,
            "내글": self.my_text.get("1.0", "end").strip(),
        }
        template = self.example.get("1.0", "end").strip() or DEFAULT_BODY
        self.result_title.set(p.title)
        self.result_body.delete("1.0", "end")
        self.result_body.insert("1.0", fill(template, values))

    def copy(self, text: str):
        if not text:
            messagebox.showinfo(APP_NAME, "복사할 내용이 없습니다. 먼저 링크를 가져와 주세요.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.configure(text="복사했습니다. 게시판에 붙여넣으세요(Ctrl+V).", foreground="#070")

    def save(self):
        try:
            limit = int(self.excerpt_len.get())
        except (tk.TclError, ValueError):
            limit = 500
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        SAVE_FILE.write_text(
            json.dumps({"body": self.example.get("1.0", "end").strip(), "excerpt_len": limit},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.status.configure(text="예시 글을 저장했습니다. 다음에 켤 때도 그대로 나옵니다.", foreground="#070")


if __name__ == "__main__":
    App().mainloop()
