"""게시판 자동 글올리기 - 윈도우 창 프로그램.

글 목록을 만들어 두고 [올리기 시작]을 누르면 브라우저를 띄워 하나씩 올린다.
"로봇이 아닙니다" 체크만 사람이 누른다.
"""

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

import storage
from poster import Poster, PostError, Settings, StopRequested

APP_NAME = "게시판 자동 글올리기"
FONT = ("맑은 고딕", 10)
FONT_BOLD = ("맑은 고딕", 10, "bold")
SEPARATOR = "==="

STATUS_WAIT = "대기"
STATUS_DONE = "완료"
STATUS_RUNNING = "올리는 중"


def default_login_url(write_url: str) -> str:
    u = urlparse(write_url)
    return f"{u.scheme}://{u.netloc}/bbs/login.php" if u.netloc else ""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1100x820")
        self.minsize(900, 640)
        self.option_add("*Font", FONT)
        style = ttk.Style(self)
        style.configure("Treeview", rowheight=26)
        style.configure("Big.TButton", font=("맑은 고딕", 11, "bold"), padding=(16, 6))

        self.posts: list[dict] = storage.load_posts()
        for p in self.posts:
            if p.get("status") == STATUS_RUNNING:
                p["status"] = STATUS_WAIT
        self.current: int | None = None
        self.events: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.worker: threading.Thread | None = None

        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self._build_settings(root)
        self._build_posts(root)
        self._build_run(root)

        self.refresh_list()
        if self.posts:
            self.select(0)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self.drain_events)

    # ---------- 화면 구성 ----------

    def _build_settings(self, root):
        box = ttk.LabelFrame(root, text=" ① 설정 ", padding=8)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)
        s = storage.load_settings()

        self.write_url = tk.StringVar(value=s.get("write_url", "https://freebene.com/diablo/write"))
        self.user_id = tk.StringVar(value=s.get("user_id", ""))
        self.password = tk.StringVar(value=s.get("password", ""))
        self.login_url = tk.StringVar(value=s.get("login_url", ""))
        self.interval = tk.IntVar(value=s.get("interval", 60))

        ttk.Label(box, text="글쓰기 주소").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(box, textvariable=self.write_url).grid(row=0, column=1, columnspan=5, sticky="ew", ipady=2)

        row = ttk.Frame(box)
        row.grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))
        ttk.Label(row, text="아이디", width=9).pack(side="left")
        ttk.Entry(row, textvariable=self.user_id, width=18).pack(side="left")
        ttk.Label(row, text="비밀번호").pack(side="left", padx=(16, 6))
        ttk.Entry(row, textvariable=self.password, show="●", width=18).pack(side="left")
        ttk.Label(row, text="글 사이 간격(초)").pack(side="left", padx=(16, 6))
        ttk.Spinbox(row, from_=10, to=3600, increment=10, width=6, textvariable=self.interval).pack(side="left")

        ttk.Label(box, text="로그인 주소").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(box, textvariable=self.login_url).grid(row=2, column=1, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Button(box, text="설정 저장", command=self.save_settings).grid(row=2, column=5, sticky="e", pady=(6, 0))
        ttk.Label(box, text="로그인 주소는 비워 두면 글쓰기 주소의 사이트에서 자동으로 정합니다.",
                  foreground="#777").grid(row=3, column=1, columnspan=5, sticky="w")

    def _build_posts(self, root):
        box = ttk.LabelFrame(root, text=" ② 올릴 글 ", padding=8)
        box.grid(row=1, column=0, sticky="nsew", pady=8)
        box.columnconfigure(1, weight=1)
        box.rowconfigure(0, weight=1)

        left = ttk.Frame(box)
        left.grid(row=0, column=0, sticky="nsw")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, columns=("no", "title", "status"), show="headings",
                                 selectmode="browse", height=12)
        self.tree.heading("no", text="번호")
        self.tree.heading("title", text="제목")
        self.tree.heading("status", text="상태")
        self.tree.column("no", width=44, anchor="center", stretch=False)
        self.tree.column("title", width=260)
        self.tree.column("status", width=150)
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(left, command=self.tree.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        btns = ttk.Frame(left)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for text, cmd in (("새 글", self.add_post), ("삭제", self.delete_post), ("▲", lambda: self.move(-1)),
                          ("▼", lambda: self.move(1)), ("파일에서 불러오기", self.import_file),
                          ("상태 초기화", self.reset_status)):
            ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=(0, 3))

        right = ttk.Frame(box)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        ttk.Label(right, text="제목", font=FONT_BOLD).grid(row=0, column=0, sticky="w")
        self.title_var = tk.StringVar()
        self.title_entry = ttk.Entry(right, textvariable=self.title_var)
        self.title_entry.grid(row=1, column=0, sticky="ew", ipady=3, pady=(2, 8))
        ttk.Label(right, text="본문 내용", font=FONT_BOLD).grid(row=2, column=0, sticky="w")
        body_frame = ttk.Frame(right)
        body_frame.grid(row=3, column=0, sticky="nsew", pady=(2, 0))
        body_frame.columnconfigure(0, weight=1)
        body_frame.rowconfigure(0, weight=1)
        self.body = tk.Text(body_frame, wrap="word", undo=True, relief="solid", borderwidth=1, padx=6, pady=4)
        self.body.grid(row=0, column=0, sticky="nsew")
        bbar = ttk.Scrollbar(body_frame, command=self.body.yview)
        bbar.grid(row=0, column=1, sticky="ns")
        self.body.configure(yscrollcommand=bbar.set)
        ttk.Label(right, text="왼쪽 목록에서 글을 고르고 여기서 고치면 바로 저장됩니다.",
                  foreground="#777").grid(row=4, column=0, sticky="w", pady=(4, 0))

        self.title_var.trace_add("write", lambda *a: self.on_edit())
        self.body.bind("<KeyRelease>", lambda e: self.on_edit())
        self.body.bind("<<Paste>>", lambda e: self.after(10, self.on_edit))

    def _build_run(self, root):
        box = ttk.LabelFrame(root, text=" ③ 올리기 ", padding=8)
        box.grid(row=2, column=0, sticky="ew")
        box.columnconfigure(2, weight=1)
        self.start_btn = ttk.Button(box, text="올리기 시작", style="Big.TButton", command=self.start)
        self.start_btn.grid(row=0, column=0, sticky="w")
        self.stop_btn = ttk.Button(box, text="멈추기", command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="w", padx=6)
        self.summary = ttk.Label(box, text="")
        self.summary.grid(row=0, column=2, sticky="w", padx=8)
        ttk.Label(box, text="브라우저가 열리면 '로봇이 아닙니다'만 직접 눌러 주세요. 나머지는 자동입니다.",
                  foreground="#c05000").grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 4))
        self.log_box = tk.Text(box, height=8, wrap="word", state="disabled", relief="solid", borderwidth=1,
                               background="#f7f7f7", padx=6, pady=4)
        self.log_box.grid(row=2, column=0, columnspan=3, sticky="ew")

    # ---------- 글 목록 ----------

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for i, p in enumerate(self.posts):
            self.tree.insert("", "end", iid=str(i),
                             values=(i + 1, p.get("title") or "(제목 없음)", p.get("status", STATUS_WAIT)))
        done = sum(p.get("status") == STATUS_DONE for p in self.posts)
        self.summary.configure(text=f"전체 {len(self.posts)}개 · 완료 {done}개")

    def select(self, index: int | None):
        self.current = None  # 아래에서 칸을 채우는 동안 on_edit가 저장하지 않게 한다
        self.title_var.set("")
        self.body.delete("1.0", "end")
        if index is None or not (0 <= index < len(self.posts)):
            return
        p = self.posts[index]
        self.title_var.set(p.get("title", ""))
        self.body.insert("1.0", p.get("body", ""))
        self.current = index
        if self.tree.selection() != (str(index),):
            self.tree.selection_set(str(index))
            self.tree.see(str(index))

    def on_tree_select(self, _event):
        sel = self.tree.selection()
        if sel and int(sel[0]) != self.current:
            self.select(int(sel[0]))

    def on_edit(self):
        if self.current is None:
            return
        p = self.posts[self.current]
        p["title"] = self.title_var.get()
        p["body"] = self.body.get("1.0", "end-1c")
        if p.get("status") not in (STATUS_RUNNING, STATUS_WAIT, STATUS_DONE):
            p["status"] = STATUS_WAIT  # 실패한 글을 고치면 다시 대기로
        self.tree.item(str(self.current), values=(self.current + 1, p["title"] or "(제목 없음)", p["status"]))
        storage.save_posts(self.posts)

    def add_post(self):
        self.posts.append({"title": "", "body": "", "status": STATUS_WAIT})
        storage.save_posts(self.posts)
        self.refresh_list()
        self.select(len(self.posts) - 1)
        self.title_entry.focus_set()

    def delete_post(self):
        if self.current is None or self.running():
            return
        p = self.posts[self.current]
        if not messagebox.askyesno(APP_NAME, f"'{p.get('title') or '(제목 없음)'}' 글을 목록에서 지울까요?"):
            return
        idx = self.current
        del self.posts[idx]
        storage.save_posts(self.posts)
        self.refresh_list()
        self.select(min(idx, len(self.posts) - 1) if self.posts else None)

    def move(self, step: int):
        if self.current is None or self.running():
            return
        i, j = self.current, self.current + step
        if not (0 <= j < len(self.posts)):
            return
        self.posts[i], self.posts[j] = self.posts[j], self.posts[i]
        storage.save_posts(self.posts)
        self.refresh_list()
        self.select(j)

    def reset_status(self):
        if self.running():
            return
        if not messagebox.askyesno(APP_NAME, "모든 글을 '대기'로 되돌릴까요?\n완료된 글도 다시 올라가게 됩니다."):
            return
        for p in self.posts:
            p["status"] = STATUS_WAIT
            p.pop("url", None)
        storage.save_posts(self.posts)
        self.refresh_list()

    def import_file(self):
        if self.running():
            return
        messagebox.showinfo(
            APP_NAME,
            "텍스트 파일(.txt) 형식\n\n"
            "· 글마다 첫 줄은 제목, 그 아래는 본문입니다.\n"
            f"· 글과 글 사이에는 {SEPARATOR} 만 있는 줄을 넣습니다.\n\n"
            f"예)\n첫 번째 제목\n첫 번째 본문...\n{SEPARATOR}\n두 번째 제목\n두 번째 본문...",
        )
        path = filedialog.askopenfilename(filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")])
        if not path:
            return
        raw = open(path, "rb").read()
        for enc in ("utf-8-sig", "cp949"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            messagebox.showerror(APP_NAME, "파일 글자를 읽지 못했습니다. UTF-8로 저장해 주세요.")
            return
        added = 0
        chunk: list[str] = []
        for line in text.splitlines() + [SEPARATOR]:
            if line.strip() == SEPARATOR:
                lines = "\n".join(chunk).strip().splitlines()
                if lines:
                    self.posts.append({"title": lines[0].strip(), "body": "\n".join(lines[1:]).strip(),
                                       "status": STATUS_WAIT})
                    added += 1
                chunk = []
            else:
                chunk.append(line)
        storage.save_posts(self.posts)
        self.refresh_list()
        messagebox.showinfo(APP_NAME, f"글 {added}개를 추가했습니다.")

    # ---------- 설정 ----------

    def collect_settings(self) -> dict:
        try:
            interval = max(10, int(self.interval.get()))
        except (tk.TclError, ValueError):
            interval = 60
        return {
            "write_url": self.write_url.get().strip(),
            "user_id": self.user_id.get().strip(),
            "password": self.password.get(),
            "login_url": self.login_url.get().strip(),
            "interval": interval,
        }

    def save_settings(self, quiet: bool = False):
        storage.save_settings(self.collect_settings())
        if not quiet:
            self.log("설정을 저장했습니다.")

    # ---------- 올리기 ----------

    def running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def start(self):
        if self.running():
            return
        s = self.collect_settings()
        if not s["write_url"].startswith("http"):
            messagebox.showwarning(APP_NAME, "글쓰기 주소를 넣어 주세요. (https://로 시작)")
            return
        targets = [i for i, p in enumerate(self.posts) if p.get("status") != STATUS_DONE]
        empty = [i + 1 for i in targets if not self.posts[i].get("title", "").strip()
                 or not self.posts[i].get("body", "").strip()]
        if not targets:
            messagebox.showinfo(APP_NAME, "올릴 글이 없습니다. 모두 '완료' 상태입니다.")
            return
        if empty:
            messagebox.showwarning(APP_NAME, f"제목이나 본문이 비어 있는 글이 있습니다: {empty}번")
            return
        if not messagebox.askyesno(APP_NAME, f"글 {len(targets)}개를 {s['interval']}초 간격으로 올립니다. 시작할까요?"):
            return
        self.save_settings(quiet=True)
        self.stop_flag.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        settings = Settings(
            write_url=s["write_url"],
            login_url=s["login_url"] or default_login_url(s["write_url"]),
            user_id=s["user_id"], password=s["password"],
            profile_dir=str(storage.PROFILE_DIR),
        )
        jobs = [(i, self.posts[i]["title"], self.posts[i]["body"]) for i in targets]
        self.worker = threading.Thread(target=self.run_jobs, args=(settings, jobs, s["interval"]), daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_flag.set()
        self.log("멈추는 중입니다...")

    def run_jobs(self, settings: Settings, jobs: list, interval: int):
        """작업 스레드. 화면은 직접 건드리지 않고 self.events로 알린다."""
        emit = self.events.put
        log = lambda msg: emit(("log", msg))
        poster = Poster(settings, log, self.stop_flag.is_set)
        fails_in_row = 0
        try:
            poster.open()
            poster.login()
            for n, (index, title, body) in enumerate(jobs):
                if n > 0:
                    log(f"다음 글까지 {interval}초 기다립니다.")
                    poster._sleep(interval)
                emit(("status", index, STATUS_RUNNING, None))
                log(f"[{index + 1}번] '{title}' 올리는 중")
                try:
                    url = poster.post(title, body)
                except PostError as e:
                    fails_in_row += 1
                    emit(("status", index, f"실패: {e}", None))
                    log(f"[{index + 1}번] 실패 - {e}")
                    if fails_in_row >= 2:
                        log("연속으로 2번 실패해서 멈춥니다. 원인을 확인한 뒤 다시 시작해 주세요.")
                        break
                    continue
                fails_in_row = 0
                emit(("status", index, STATUS_DONE, url))
                log(f"[{index + 1}번] 완료 {url}")
            else:
                log("모든 글을 올렸습니다.")
        except StopRequested:
            log("멈췄습니다.")
        except PostError as e:
            log(f"중단: {e}")
        except Exception as e:  # 브라우저를 사용자가 닫은 경우 등
            log(f"중단: 예상하지 못한 오류 - {e}")
        finally:
            poster.close()
            emit(("finished",))

    def drain_events(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "log":
                    self.log(ev[1])
                elif ev[0] == "status":
                    _, index, status, url = ev
                    if index < len(self.posts):
                        self.posts[index]["status"] = status
                        if url:
                            self.posts[index]["url"] = url
                        storage.save_posts(self.posts)
                        self.refresh_list()
                        if self.current is not None:
                            self.tree.selection_set(str(self.current))
                elif ev[0] == "finished":
                    for p in self.posts:
                        if p.get("status") == STATUS_RUNNING:
                            p["status"] = STATUS_WAIT
                    storage.save_posts(self.posts)
                    self.refresh_list()
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(200, self.drain_events)

    def log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def on_close(self):
        if self.running() and not messagebox.askyesno(APP_NAME, "글을 올리는 중입니다. 그래도 끌까요?"):
            return
        self.stop_flag.set()
        self.save_settings(quiet=True)
        self.destroy()


def selftest() -> int:
    """빌드 확인용: 브라우저를 띄울 수 있는지만 확인한다."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        for channel in ("chrome", "msedge"):
            try:
                browser = pw.chromium.launch(channel=channel, headless=True)
                browser.close()
                return 0
            except Exception:
                continue
    return 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    App().mainloop()
