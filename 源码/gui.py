"""
SpeedMovie - Douyin Short Video One-Click Generator (tkinter Desktop)
"""

import os, sys, json, re, time, shutil, subprocess, queue, threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, simpledialog, messagebox
from ai_client import call_openrouter_chat

NW = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}

# ============ Paths ============
BASE = Path(__file__).resolve().parent.parent
SRC_DIR = BASE / '源码'
CFG_DIR = BASE / '配置'
OUT_DIR = BASE / '作品'
CACHE   = BASE / '缓存'
for e in [OUT_DIR, CACHE]:
    e.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(SRC_DIR))

os.environ["HF_HOME"] = r"D:\models\huggingface"
os.environ["HF_HUB_CACHE"] = r"D:\models\huggingface\hub"

CFG_PATH = CFG_DIR / 'config.json'


def load_config():
    config = {}
    template = CFG_DIR / 'config_template.json'
    if template.exists():
        config.update(json.loads(template.read_text(encoding='utf-8')))
    if CFG_PATH.exists():
        config.update(json.loads(CFG_PATH.read_text(encoding='utf-8')))
    return config


def probe_media_duration(ffmpeg_path, media_path, fallback=0.0):
    """Read media duration using ffmpeg stderr output."""
    try:
        result = subprocess.run(
            [ffmpeg_path, '-i', str(media_path), '-f', 'null', '-'],
            capture_output=True,
            text=True,
            timeout=30,
            **NW,
        )
        match = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', result.stderr)
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return fallback


# ============ Learning Records ============
LEARN_PATH = CFG_DIR / '学习记录.json'


def _load_learning():
    """Load learning records"""
    try:
        if LEARN_PATH.exists():
            return json.loads(LEARN_PATH.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {'rewrite_corrections': [], 'keyword_corrections': []}


def _save_learning(data):
    """Save learning records"""
    try:
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        LEARN_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass


def _add_rewrite_correction(original, corrected):
    """Record an AI rewrite correction (user manually edited version)"""
    if original.strip() == corrected.strip():
        return
    data = _load_learning()
    data.setdefault('rewrite_corrections', []).append({
        'original': original[:500],
        'corrected': corrected[:500],
        'time': datetime.now().strftime('%Y-%m-%d %H:%M')
    })
    data['rewrite_corrections'] = data['rewrite_corrections'][-100:]
    _save_learning(data)


def _add_keyword_correction(old_kw, new_kw):
    """Record a search keyword correction"""
    if old_kw.strip() == new_kw.strip():
        return
    data = _load_learning()
    data.setdefault('keyword_corrections', []).append({
        'original': old_kw.strip(),
        'corrected': new_kw.strip(),
        'time': datetime.now().strftime('%Y-%m-%d %H:%M')
    })
    data['keyword_corrections'] = data['keyword_corrections'][-100:]
    _save_learning(data)


def _get_learning_prompt():
    """Generate learning examples to inject into AI prompts"""
    data = _load_learning()
    parts = []

    rewrites = data.get('rewrite_corrections', [])
    if rewrites:
        parts.append('【用户过往改写偏好】')
        parts.append('请先分析下面这些修改案例中体现的用户风格偏好(如语气、用词习惯、段落结构等), 然后应用到本次改写中。')
        for c in rewrites[-10:]:
            parts.append(f'原文片段: {c["original"][:200]}...')
            parts.append(f'用户改为: {c["corrected"][:200]}...')

    keywords = data.get('keyword_corrections', [])
    if keywords:
        parts.append('【用户偏好的配图关键词修正, 请参考风格选择类似调性的关键词】')
        for c in keywords[-15:]:
            parts.append(f'"{c["original"]}" → "{c["corrected"]}"')

    return '\n'.join(parts) if parts else ''


# ============ GUI ============

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('速影 - 抖音短视频一键生成')
        self.root.geometry('750x550')
        self.root.resizable(True, True)
        self.root.minsize(750, 420)

        self.log_q = queue.Queue()
        self.stop_flag = threading.Event()
        self.running = False
        self.output_dir = str(OUT_DIR)

        # Debug mode intermediate state
        self.raw_narration = ''
        self.title = ''
        self.narration = ''
        self.segments = []
        self.images = []
        self.audio_path = None
        self.run_dir = None
        self.current_step = 1
        self.completed_up_to = 0
        self.cover_portrait_path = None   # Portrait cover 3:4
        self.cover_landscape_path = None  # Landscape cover 4:3
        self._last_ai_rewrite_text = ''   # AI rewrite raw output, for detecting user edits
        self.word_boundaries = []          # Edge TTS word boundary data, for precise subtitle alignment
        self.task_queue = []              # Task queue
        self.last_publish_time = None     # Last publish time

        # Remote listener state
        self.listener_active = False
        self.listener_thread = None
        self.listener_stop = threading.Event()

        # Task queue (multiple links executed in order)
        self.task_queue = []
        self.last_publish_time = None  # Last publish time

        self._build_ui()
        self._poll()

        # Auto-start remote listener if configured
        if self.listener_var.get():
            self.root.after(1000, self._toggle_listener)

        # Check Douyin login status on startup
        self.root.after(2000, self._refresh_douyin_status)

    # ----------------------------------------------------------------
    # UI Construction
    # ----------------------------------------------------------------
    def _build_ui(self):
        """Build main UI: status bar (bottom) + generation page (full)"""

        # ---- Bottom status bar (pack to bottom first) ----
        status_frame = ttk.Frame(self.root, relief='sunken', padding=(10, 3))
        status_frame.pack(fill='x', side='bottom')
        self.status_label = ttk.Label(status_frame, text='就绪')
        self.status_label.pack(side='left')

        sep = ttk.Separator(status_frame, orient='vertical')
        sep.pack(side='left', fill='y', padx=(15, 8))

        ttk.Label(status_frame, text='抖音:').pack(side='left')
        self.douyin_status_label = ttk.Label(
            status_frame, text='未知', foreground='gray', cursor='hand2')
        self.douyin_status_label.pack(side='left', padx=(4, 0))
        self.douyin_status_label.bind('<Button-1>', lambda e: self._login_douyin_dialog())

        # ---- Main content area: video generation page (full width) ----
        self._build_generate_page()

    # ----------------------------------------------------------------
    # Video generation page (single page, full width)
    # ----------------------------------------------------------------
    def _build_generate_page(self):
        """Build video generation page: toolbar + dual workspace + 7-step Notebook"""
        main = ttk.Frame(self.root)
        main.pack(fill='both', expand=True)

        # ---- Top toolbar ----
        toolbar = ttk.Frame(main, padding=(10, 8, 10, 4))
        toolbar.pack(fill='x')

        ttk.Label(toolbar, text='链接:').pack(side='left')
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(toolbar, textvariable=self.url_var, width=40)
        self.url_entry.pack(side='left', padx=(6, 8))
        self._bind_right_click(self.url_entry)

        self.main_btn = ttk.Button(toolbar, text='开始生成', command=self._on_main_btn)
        self.main_btn.pack(side='left', padx=(0, 8))

        # Remote listener toggle (visible on main interface)
        config = load_config()
        self.listener_var = tk.BooleanVar(value=config.get('listener_enabled', False))
        self.listener_chk = ttk.Checkbutton(
            toolbar, text='本地远程监听', variable=self.listener_var,
            command=self._toggle_listener)
        self.listener_chk.pack(side='left', padx=(0, 8))
        self.listener_status = ttk.Label(toolbar, text='', foreground='gray')
        self.listener_status.pack(side='left')

        # Settings button (right-aligned, vertically aligned with save button)
        ttk.Button(toolbar, text='设置', command=self._open_settings, width=6).pack(side='right')

        # ---- Workspace switch buttons ----
        ws_btns = ttk.Frame(main, padding=(10, 2, 10, 0))
        ws_btns.pack(fill='x')
        self.ws_oneclick_btn = ttk.Button(
            ws_btns, text='一键生成', width=10,
            command=lambda: self._switch_workspace(0))
        self.ws_oneclick_btn.pack(side='left', padx=(0, 4))
        self.ws_debug_btn = ttk.Button(
            ws_btns, text='逐步调试', width=10,
            command=lambda: self._switch_workspace(1))
        self.ws_debug_btn.pack(side='left')

        self._ws_current_tab = 0

        # ======== Workspace: One-Click Generate ========
        self._ws_oneclick_frame = ttk.Frame(main)
        self._ws_oneclick_frame.pack(fill='both', expand=True)

        # One-click generation log area
        self.log_frame = ttk.Frame(self._ws_oneclick_frame, padding=(10, 4))
        self.log_frame.pack(fill='both', expand=True)
        self.log_text = scrolledtext.ScrolledText(
            self.log_frame, wrap='word', height=4, state='disabled',
            font=('Consolas', 10))
        self.log_text.pack(fill='both', expand=True)
        self.log_text.tag_configure('error', foreground='red')
        self.log_text.tag_configure('success', foreground='green')
        self.log_text.tag_configure('info', foreground='black')

        # ======== Workspace: Step-by-Step Debug ========
        self._ws_debug_frame = ttk.Frame(main)

        # PanedWindow split equally: Notebook (top) + log (bottom)
        self._debug_paned = ttk.PanedWindow(self._ws_debug_frame, orient='vertical')
        self._debug_paned.pack(fill='both', expand=True)

        # Force sash to center after window renders
        self.root.after(300, self._center_debug_sash)

        # ---- Upper: 7-step Notebook ----
        nb_frame = ttk.Frame(self._debug_paned)
        self._debug_paned.add(nb_frame, weight=1)

        self.notebook = ttk.Notebook(nb_frame)
        self.notebook.pack(fill='both', expand=True, padx=1, pady=1)
        self.notebook.bind('<<NotebookTabChanged>>', self._on_tab_changed)

        # Save button placed to the right of tab headers (vertically aligned with settings button)
        self.save_btn = ttk.Button(self.notebook, text='保存修改', command=self._save_rewrite)
        self.save_btn.place(relx=1.0, x=-10, y=1, height=24, anchor='ne')
        self.save_lbl = ttk.Label(self.notebook, text='', foreground='green')
        self.save_lbl.place(relx=1.0, x=-110, y=4, anchor='ne')

        self.step_names = ['提取文案', 'AI改写', '分镜切分', '搜索配图',
                           'TTS合成', '渲染视频', '发布视频']

        # Tab1 Extract narration
        t1 = ttk.Frame(self.notebook, padding=2)
        self.notebook.add(t1, text=' 1.提取文案 ')
        self.t1_text = scrolledtext.ScrolledText(t1, wrap='word')
        self.t1_text.pack(fill='both', expand=True)

        # Tab2 AI rewrite
        t2 = ttk.Frame(self.notebook, padding=2)
        self.notebook.add(t2, text=' 2.AI改写 ')
        self.t2_text = scrolledtext.ScrolledText(t2, wrap='word')
        self.t2_text.pack(fill='both', expand=True)
        self._bind_right_click(self.t2_text)

        # Tab3 Storyboard split
        t3 = ttk.Frame(self.notebook, padding=2)
        self.notebook.add(t3, text=' 3.分镜切分 ')
        self.t3_text = scrolledtext.ScrolledText(t3, wrap='word')
        self.t3_text.pack(fill='both', expand=True)

        # Tab4 Search images
        t4 = ttk.Frame(self.notebook, padding=2)
        self.notebook.add(t4, text=' 4.搜索配图 ')
        self.t4_container = ttk.Frame(t4)
        self.t4_container.pack(fill='both', expand=True)

        # Tab5 TTS voiceover
        t5 = ttk.Frame(self.notebook, padding=2)
        self.notebook.add(t5, text=' 5.TTS合成 ')
        rf = ttk.Frame(t5)
        rf.pack(fill='x', pady=(0, 10))
        ttk.Label(rf, text='语速:').pack(side='left')
        self.tts_rate_var = tk.StringVar(value=config.get('tts_rate', '-5%'))
        rate_entry = ttk.Entry(rf, textvariable=self.tts_rate_var, width=8)
        rate_entry.pack(side='left', padx=4)
        self._bind_right_click(rate_entry)
        ttk.Label(rf, text='(如 -5%, +10%, 0%)').pack(side='left')
        bf5 = ttk.Frame(t5)
        bf5.pack(fill='x')
        ttk.Button(bf5, text='试听', command=self._preview_tts).pack(side='left')
        self.t5_status = ttk.Label(t5, text='')
        self.t5_status.pack(anchor='w', pady=(10, 0))

        # Tab6 Render video
        t6 = ttk.Frame(self.notebook, padding=2)
        self.notebook.add(t6, text=' 6.渲染视频 ')
        self.t6_log = scrolledtext.ScrolledText(t6, wrap='word')
        self.t6_log.pack(fill='both', expand=True)
        self.t6_result = ttk.Label(t6, text='', foreground='blue')
        self.t6_result.pack(anchor='w', pady=(2, 0))

        # Tab7 Publish video
        t7 = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(t7, text=' 7.发布视频 ')
        self._build_step7_publish(t7)

        # ---- Lower: Debug mode log area ----
        dbg_log = ttk.Frame(self._debug_paned, padding=(2, 0))
        self._debug_paned.add(dbg_log, weight=1)
        self.dbg_log_text = scrolledtext.ScrolledText(
            dbg_log, wrap='word', height=4, state='disabled',
            font=('Consolas', 10))
        self.dbg_log_text.pack(fill='both', expand=True)
        self.dbg_log_text.tag_configure('error', foreground='red')
        self.dbg_log_text.tag_configure('success', foreground='green')
        self.dbg_log_text.tag_configure('info', foreground='black')

        self._debug_log_widget = self.dbg_log_text
        self._update_workspace_buttons()

    # ----------------------------------------------------------------
    # Step 7: Publish video (embedded in debug Notebook)
    # ----------------------------------------------------------------
    def _build_step7_publish(self, parent):
        """Build step 7 publish content: Douyin account status only."""
        # Login status row
        login_row = ttk.Frame(parent)
        login_row.pack(fill='x', padx=10, pady=(0, 8))
        ttk.Label(login_row, text='抖音状态:').pack(side='left')
        self.step7_status_label = ttk.Label(login_row, text='未知', foreground='gray')
        self.step7_status_label.pack(side='left', padx=(4, 0))
        ttk.Button(login_row, text='扫码登录', command=self._login_douyin_dialog).pack(
            side='left', padx=(12, 4))
        ttk.Button(login_row, text='刷新状态', command=self._check_douyin_status).pack(
            side='left', padx=(4, 0))

    # ----------------------------------------------------------------
    # Settings dialog (Toplevel)
    # ----------------------------------------------------------------
    def _open_settings(self):
        """Open settings dialog"""
        win = tk.Toplevel(self.root)
        win.title('设置')
        # Center on main window
        win.update_idletasks()
        pw, ph = 520, 360
        rx = self.root.winfo_x()
        ry = self.root.winfo_y()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        x = rx + (rw - pw) // 2
        y = ry + (rh - ph) // 2
        win.geometry(f'{pw}x{ph}+{x}+{y}')
        win.transient(self.root)
        win.grab_set()

        config = load_config()

        # -- Local listener --
        local_section = ttk.LabelFrame(win, text='本地监听', padding=(12, 8))
        local_section.pack(fill='x', padx=15, pady=(15, 10))

        local_poll_row = ttk.Frame(local_section)
        local_poll_row.pack(fill='x', pady=3)
        ttk.Label(local_poll_row, text='本地监听轮询间隔:', width=18, anchor='e').pack(side='left')
        local_poll_var = tk.StringVar(value=str(config.get('listener_interval_seconds', 60)))
        ttk.Entry(local_poll_row, textvariable=local_poll_var, width=8).pack(side='left', padx=(6, 4))
        ttk.Label(local_poll_row, text='秒，仅本地监听',
                  foreground='gray', font=('', 8)).pack(side='left', padx=(4, 0))

        local_msg_lbl = ttk.Label(local_poll_row, text='', foreground='green')

        def save_local_listener():
            try:
                c = load_config()
                try:
                    c['listener_interval_seconds'] = max(5, int(local_poll_var.get().strip()))
                except ValueError:
                    c['listener_interval_seconds'] = 30
                CFG_PATH.write_text(
                    json.dumps(c, ensure_ascii=False, indent=4), encoding='utf-8')
                local_msg_lbl.config(text='已保存', foreground='green')
                return True
            except Exception as e:
                local_msg_lbl.config(text=f'保存失败: {e}', foreground='red')
                return False

        ttk.Button(local_poll_row, text='保存', command=save_local_listener, width=6).pack(side='left', padx=(8, 0))
        local_msg_lbl.pack(side='left', padx=(8, 0))

        # -- Publish settings --
        pub_section = ttk.LabelFrame(win, text='发布配置', padding=(12, 8))
        pub_section.pack(fill='x', padx=15, pady=(0, 10))

        interval_row = ttk.Frame(pub_section)
        interval_row.pack(fill='x', pady=3)
        ttk.Label(interval_row, text='定时发布间隔:', width=18, anchor='e').pack(side='left')
        interval_var = tk.StringVar(value=str(config.get('publish_interval_minutes', 120)))
        ttk.Entry(interval_row, textvariable=interval_var, width=8).pack(side='left', padx=(6, 4))
        ttk.Label(interval_row, text='分钟，多条链接时相邻视频的定时发布间隔',
                  foreground='gray', font=('', 8)).pack(side='left', padx=(4, 0))

        desc_row = ttk.Frame(pub_section)
        desc_row.pack(fill='x', pady=3)
        ttk.Label(desc_row, text='发布话题:', width=18, anchor='e').pack(side='left')
        pub_desc_var = tk.StringVar(value=config.get('pub_desc', ''))
        ttk.Entry(desc_row, textvariable=pub_desc_var, width=35).pack(side='left', padx=(6, 0))

        template_row = ttk.Frame(pub_section)
        template_row.pack(fill='x', pady=3)
        ttk.Label(template_row, text='AI改写模板:', width=18, anchor='e').pack(side='left')
        ttk.Button(template_row, text='打开模板文件', command=self._open_rewrite_template).pack(side='left', padx=(6, 0))
        ttk.Label(template_row, text='修改后保存 txt，再点下面按钮同步',
                  foreground='gray', font=('', 8)).pack(side='left', padx=(8, 0))

        ttk.Label(
            pub_section,
            text='本地配置是主配置。保存并同步后，云端会按同样设置运行。',
            foreground='gray',
            font=('', 8)
        ).pack(anchor='w', padx=(18, 0), pady=(6, 0))

        # -- Save button --
        btn_row = ttk.Frame(pub_section)
        btn_row.pack(fill='x', padx=(18, 0), pady=(8, 0))

        def save(close=True):
            try:
                c = load_config()
                c['auto_publish_douyin'] = True
                c['pub_desc'] = pub_desc_var.get().strip()
                try:
                    c['publish_interval_minutes'] = int(interval_var.get().strip())
                except ValueError:
                    c['publish_interval_minutes'] = 120
                c['transition_enabled'] = True
                CFG_PATH.write_text(
                    json.dumps(c, ensure_ascii=False, indent=4), encoding='utf-8')
                msg_lbl.config(text='已保存', foreground='green')
                if close:
                    win.after(1500, win.destroy)
                return True
            except Exception as e:
                msg_lbl.config(text=f'保存失败: {e}', foreground='red')
                return False

        def save_and_sync():
            if save(close=False):
                self._sync_default_cloud_settings()

        self.cloud_sync_btn = ttk.Button(btn_row, text='保存并同步云端', command=save_and_sync)
        self.cloud_sync_btn.pack(side='left')
        msg_lbl = ttk.Label(btn_row, text='', foreground='green')
        msg_lbl.pack(side='left', padx=(12, 0))

        account_section = ttk.LabelFrame(win, text='抖音账号', padding=(12, 8))
        account_section.pack(fill='x', padx=15, pady=(0, 10))
        ttk.Button(
            account_section,
            text='抖音 Cookie 同步云端',
            command=self._refresh_cookie_and_sync_cloud,
        ).pack(side='left')

    # ----------------------------------------------------------------
    # Workspace switching
    # ----------------------------------------------------------------
    def _center_debug_sash(self):
        """Center the debug area PanedWindow sash"""
        try:
            h = self._debug_paned.winfo_height()
            if h > 100:
                self._debug_paned.sashpos(0, h * 57 // 100)
        except Exception:
            pass

    def _switch_workspace(self, tab):
        """Switch between one-click(0) / step-by-step debug(1)"""
        self._ws_current_tab = tab
        if tab == 0:
            self._ws_debug_frame.pack_forget()
            self._ws_oneclick_frame.pack(fill='both', expand=True)
        else:
            self._ws_oneclick_frame.pack_forget()
            self._ws_debug_frame.pack(fill='both', expand=True)
            # Re-center sash after switching
            self.root.after(200, self._center_debug_sash)
        self._update_workspace_buttons()
        self._update_button_text()

    def _get_workspace_tab(self):
        """Get current workspace: 0=one-click, 1=step-by-step debug"""
        return self._ws_current_tab

    def _update_workspace_buttons(self):
        """Update workspace tab button highlighting"""
        tab = self._ws_current_tab
        if tab == 0:
            self.ws_oneclick_btn.state(['pressed'])
            self.ws_debug_btn.state(['!pressed'])
        else:
            self.ws_oneclick_btn.state(['!pressed'])
            self.ws_debug_btn.state(['pressed'])
        if tab == 1:
            try:
                idx = self.notebook.index(self.notebook.select())
                self.current_step = idx + 1
            except Exception:
                pass

    # ----------------------------------------------------------------
    # Right-click menu
    # ----------------------------------------------------------------
    def _bind_right_click(self, widget):
        def show_menu(e):
            m = tk.Menu(self.root, tearoff=0)
            m.add_command(label='剪切', accelerator='Ctrl+X',
                          command=lambda: widget.event_generate('<<Cut>>'))
            m.add_command(label='复制', accelerator='Ctrl+C',
                          command=lambda: widget.event_generate('<<Copy>>'))
            m.add_command(label='粘贴', accelerator='Ctrl+V',
                          command=lambda: widget.event_generate('<<Paste>>'))
            m.add_separator()
            m.add_command(label='全选', accelerator='Ctrl+A',
                          command=lambda: self._select_all(widget))
            try:
                m.tk_popup(e.x_root, e.y_root)
            finally:
                m.grab_release()
            return 'break'
        widget.bind('<Button-3>', show_menu)

    def _select_all(self, widget):
        try:
            widget.select_range(0, 'end')
            widget.icursor('end')
        except Exception:
            widget.tag_add('sel', '1.0', 'end')
        return 'break'

    # ----------------------------------------------------------------
    # Utility methods
    # ----------------------------------------------------------------
    def log(self, msg):
        self.log_q.put(msg)

    def set_step(self, num, status):
        self.log_q.put(('step', (num, status)))

    def set_status(self, text):
        self.log_q.put(('status', text))

    def _append_log(self, msg):
        tag = 'info'
        if any(k in msg for k in ['错误', '失败', 'x ']):
            tag = 'error'
        elif any(k in msg for k in ['完成', '#', '成功']):
            tag = 'success'
        log_widget = self.log_text
        try:
            if self._get_workspace_tab() == 1:
                log_widget = self._debug_log_widget
        except Exception:
            pass
        log_widget.config(state='normal')
        log_widget.insert('end', msg + '\n', tag)
        log_widget.see('end')
        log_widget.config(state='disabled')

    def _update_step(self, num, status):
        """Update tab title to show step status"""
        if not (1 <= num <= 7):
            return
        name = self.step_names[num - 1]
        if status == 'active':
            text = f' {num}.{name}... '
        elif status == 'done':
            text = f' {num}.{name} \u2713 '
        elif status == 'error':
            text = f' {num}.{name} \u2717 '
        else:
            text = f' {num}.{name} '
        try:
            self.notebook.tab(num - 1, text=text)
        except Exception:
            pass

    def _switch_tab(self, idx):
        if 0 <= idx < 7:
            self.notebook.select(idx)

    def _update_button_text(self):
        """Update button text based on current page and workspace tab"""
        if self.running:
            self.main_btn.config(text='停止', state='normal')
            return
        tab = self._get_workspace_tab()
        if tab == 1:  # step-by-step debug
            step = max(1, min(self.current_step, 7))
            self.main_btn.config(text=f'运行步骤{step}', state='normal')
        else:  # one-click generate
            self.main_btn.config(text='开始生成', state='normal')

    def _auto_save_edits(self):
        """Auto-save edit content from current tab to instance variables"""
        try:
            idx = self.notebook.index(self.notebook.select())
        except Exception:
            idx = 1

        try:
            if idx == 0:
                txt = self.t1_text.get('1.0', 'end').strip()
                if txt:
                    self.raw_narration = txt

            elif idx == 1:
                txt = self.t2_text.get('1.0', 'end').strip()
                if not txt:
                    return
                tm = re.search(r'【标题】\s*\n?(.+)', txt)
                bm = re.search(r'【优化口播文案】\s*\n?([\s\S]+)', txt)
                t = tm.group(1).strip() if tm else ''
                b = bm.group(1).strip() if bm else txt
                if t and b:
                    self.title = t
                    self.narration = b
                    safe = re.sub(r'[\\/:*?"<>|]', '_', t)
                    new_dir = Path(self.output_dir) / safe
                    new_dir.mkdir(parents=True, exist_ok=True)
                    self.run_dir = new_dir
                    self.proc_dir = new_dir / '过程'
                    self.proc_dir.mkdir(exist_ok=True)
                    (self.proc_dir / '01_rewritten_narration.txt').write_text(
                        f'【标题】\n{t}\n\n【优化口播文案】\n{b}', encoding='utf-8')

            elif idx == 2:
                txt = self.t3_text.get('1.0', 'end').strip()
                if not txt or not self.segments:
                    return
                new_segs = []
                for m in re.finditer(r'镜\s*(\d+)\s*\(([0-9.]+)s\):\s*\n\s*([\s\S]+?)(?=\n镜\s*\d|\Z)', txt):
                    sid, dur, seg_text = int(m.group(1)), float(m.group(2)), m.group(3).strip()
                    if sid <= len(self.segments):
                        seg = self.segments[sid - 1].copy()
                        seg['text'] = seg_text
                        seg['duration'] = dur
                    else:
                        seg = {'id': sid, 'text': seg_text, 'duration': dur}
                    new_segs.append(seg)
                if new_segs:
                    self.segments = new_segs
                    if self.proc_dir:
                        (self.proc_dir / '02_storyboard.json').write_text(
                            json.dumps(new_segs, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass

    def _save_rewrite(self):
        """Manually save current tab's edit content"""
        try:
            idx = self.notebook.index(self.notebook.select())
        except Exception:
            idx = 1
        tab_names = self.step_names
        self._auto_save_edits()

        # Detect step 2 user edits, record learning correction
        if idx == 1 and self._last_ai_rewrite_text:
            current = self.t2_text.get('1.0', 'end').strip()
            if current and current != self._last_ai_rewrite_text.strip():
                _add_rewrite_correction(self._last_ai_rewrite_text, current)
                self.log('  已记录改写偏好 (下次AI将参考)')
                self._last_ai_rewrite_text = current

        name = tab_names[idx] if idx < len(tab_names) else f'Tab{idx+1}'
        if idx == 0:
            msg = f'原始文案已保存 ({len(self.raw_narration)}字)'
        elif idx == 1:
            msg = f'文案已保存 (标题: {self.title}, {len(self.narration)}字)'
        elif idx == 2:
            msg = f'分镜已保存 (共{len(self.segments)}个分镜)'
        else:
            msg = f'{name} 已保存'
        self.log(msg)
        self.save_lbl.config(text='已保存')
        self.set_status(msg)

    def _poll(self):
        while True:
            try:
                item = self.log_q.get_nowait()
                if isinstance(item, tuple):
                    cmd, args = item
                    if cmd == 'step':
                        self._update_step(args[0], args[1])
                    elif cmd == 'status':
                        self.status_label.config(text=args)
                    elif cmd == 'done':
                        self.running = False
                        self._update_button_text()
                        self._on_task_completed()
                    elif cmd == 'tab':
                        self._switch_tab(args)
                    elif cmd == 'debug_step':
                        self._on_step_done(args)
                    elif cmd == 'img_refresh':
                        self._show_image_grid()
                    elif cmd == 'tts_status':
                        self.t5_status.config(text=args)
                    elif cmd == 't6_result':
                        self.t6_result.config(text=args)
                    elif cmd == 'listener_link':
                        self._on_listener_link(args)
                    elif cmd == 'douyin_status':
                        self._update_douyin_status_ui(args)
                    elif cmd == 'step7_prefill':
                        self._step7_prefill_from_pipeline()
                else:
                    self._append_log(item)
            except queue.Empty:
                break
        self.root.after(100, self._poll)

    # ----------------------------------------------------------------
    # Douyin status
    # ----------------------------------------------------------------
    def _refresh_douyin_status(self):
        """Check Douyin login status in background thread"""
        self.douyin_status_label.config(text='检查中...', foreground='gray')

        def check():
            try:
                from publisher import check_douyin_login
                result = check_douyin_login()
                if result:
                    self.log_q.put(('douyin_status', ('已登录', 'green')))
                else:
                    self.log_q.put(('douyin_status', ('未登录', 'red')))
            except Exception:
                self.log_q.put(('douyin_status', ('检查失败', 'red')))

        threading.Thread(target=check, daemon=True).start()

    def _update_douyin_status_ui(self, args):
        """Update Douyin status label on main thread (status bar + step 7)"""
        text, color = args
        self.douyin_status_label.config(text=text, foreground=color)
        try:
            self.step7_status_label.config(text=text, foreground=color)
        except Exception:
            pass

    # ----------------------------------------------------------------
    # Step 7: Publish operation
    # ----------------------------------------------------------------
    def _step7_prefill_from_pipeline(self):
        """Step 7 uses the generated title and settings, so no form prefill is needed."""
        pass

    def _sync_default_cloud_settings(self):
        """Sync local primary settings to GitHub Secrets for cloud runs."""
        cmd = [
            sys.executable,
            str(SRC_DIR / 'tools' / 'sync_cloud_settings.py'),
            '--all',
        ]
        self._run_cloud_sync_command(cmd, '保存并同步云端')

    def _open_rewrite_template(self):
        """Open the AI rewrite template text file with the system editor."""
        template = CFG_DIR / 'ai生故事模板.txt'
        if not template.exists():
            messagebox.showerror('错误', f'未找到模板文件: {template}')
            return
        try:
            if sys.platform == 'win32':
                os.startfile(str(template))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(template)])
            else:
                subprocess.Popen(['xdg-open', str(template)])
        except Exception as e:
            messagebox.showerror('错误', f'打开模板失败: {e}')

    def _refresh_cookie_and_sync_cloud(self):
        """Launch cookie refresh + cloud sync in a visible console window."""
        script = SRC_DIR / 'tools' / 'refresh_cookies.py'
        if not script.exists():
            messagebox.showerror('错误', f'未找到脚本: {script}')
            return
        try:
            if sys.platform == 'win32':
                python_exe = Path(sys.executable)
                if python_exe.name.lower() == 'pythonw.exe':
                    console_python = python_exe.with_name('python.exe')
                    if console_python.exists():
                        python_exe = console_python
                subprocess.Popen(
                    [
                        str(python_exe),
                        '-c',
                        (
                            'import runpy, sys, traceback\n'
                            "print('速影 - 抖音 Cookie 同步云端')\n"
                            "print('=' * 50)\n"
                            "code = 0\n"
                            "try:\n"
                            f"    sys.argv = [{str(script)!r}]\n"
                            f"    runpy.run_path({str(script)!r}, run_name='__main__')\n"
                            "except SystemExit as e:\n"
                            "    code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)\n"
                            "except Exception:\n"
                            "    traceback.print_exc()\n"
                            "    code = 1\n"
                            "input('\\n请按回车键关闭窗口...')\n"
                            "raise SystemExit(code)\n"
                        ),
                    ],
                    cwd=str(BASE),
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            else:
                subprocess.Popen([sys.executable, str(script)], cwd=str(BASE))
            self.log('已打开抖音 Cookie 同步云端脚本。')
        except Exception as e:
            messagebox.showerror('错误', f'启动失败: {e}')

    def _run_cloud_sync_command(self, cmd, title):
        """Run a cloud sync command in background and write output to the main log."""
        btn = getattr(self, 'cloud_sync_btn', None)
        if btn:
            btn.config(state='disabled')

        self.log('=' * 50)
        self.log(f'[云端同步] {title}...')

        def worker():
            code = 1
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(BASE),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    **NW,
                )
                for line in proc.stdout:
                    self.log(line.rstrip())
                code = proc.wait()
                if code == 0:
                    self.log('[云端同步] 同步完成')
                else:
                    self.log(f'[云端同步] 同步失败, 退出码: {code}')
            except Exception as e:
                self.log(f'[云端同步] 同步失败: {e}')
            finally:
                if btn:
                    self.root.after(0, lambda: btn.config(state='normal'))

        threading.Thread(target=worker, daemon=True).start()

    def _step7_do_publish(self):
        """Step 7: Publish selected video to Douyin"""
        # Auto-calculate video path
        video_path = ''
        if self.run_dir and self.title:
            vp = self.run_dir / f'{self.title}--成品.mp4'
            if vp.exists():
                video_path = str(vp)
        config = load_config()
        title = self.title
        desc = config.get('pub_desc', '').strip()

        # If title is empty, use step 2 rewrite title
        if not title:
            title = self.title
        if not title:
            messagebox.showinfo('提示', '请填写标题, 或先完成步骤2获取改写标题')
            return
        if len(title) > 30:
            messagebox.showinfo('提示', '标题不能超过30个字')
            return

        if not video_path or not Path(video_path).exists():
            messagebox.showinfo('提示', '请先完成视频渲染 (步骤6)')
            return

        def do_publish():
            try:
                from publisher import publish_to_douyin, check_douyin_login, split_douyin_tags

                self.log('=' * 50)
                self.log('[发布] 准备发布到抖音...')

                if not check_douyin_login():
                    self.log('抖音未登录或 cookie 已失效, 请先扫码登录')
                    return

                self.log(f'  标题: {title}')
                self.log(f'  简介: {desc if desc else "(无)"}')
                self.log('  方式: 立即发布')
                self.set_status('发布到抖音中...')

                kwargs = dict(
                    video_path=video_path,
                    title=title,
                    tags=split_douyin_tags(desc),
                    description='',
                    headless=False,
                    debug=True
                )
                result = publish_to_douyin(**kwargs)

                if result['success']:
                    self.log('✓ 发布成功!')
                    self.set_status('发布成功')
                    self.last_publish_time = datetime.now()
                    self.root.after(0, lambda: messagebox.showinfo('发布结果', '视频已成功发布到抖音!'))
                else:
                    self.log(f'✗ 发布失败: {result["message"]}')
                    self.set_status(f'发布失败: {result["message"]}')
            except Exception as e:
                self.log(f'发布异常: {e}')
                self.root.after(0, lambda: messagebox.showerror('发布异常', str(e)))
            finally:
                self.log_q.put(('done', None))

        self.running = True
        self._update_button_text()
        threading.Thread(target=do_publish, daemon=True).start()

    # ----------------------------------------------------------------
    # Remote listener
    # ----------------------------------------------------------------
    def _toggle_listener(self):
        """Enable/disable remote listener"""
        enabled = self.listener_var.get()
        config = load_config()

        config['listener_enabled'] = enabled
        CFG_PATH.write_text(
            json.dumps(config, ensure_ascii=False, indent=4), encoding='utf-8')

        if enabled:
            url = config.get('listener_worker_url', '').strip()
            if not url:
                self.listener_var.set(False)
                config['listener_enabled'] = False
                CFG_PATH.write_text(
                    json.dumps(config, ensure_ascii=False, indent=4), encoding='utf-8')
                messagebox.showwarning('提示', '请先在配置文件里填写 listener_worker_url')
                return
            self.listener_stop.clear()
            self.listener_active = True
            self.listener_status.config(text='监听中...', foreground='green')
            self.log('本地远程监听已开启')
            self.listener_thread = threading.Thread(
                target=self._listener_loop, daemon=True)
            self.listener_thread.start()
        else:
            self.listener_stop.set()
            self.listener_active = False
            self.listener_status.config(text='', foreground='gray')
            self.log('本地远程监听已关闭')

    def _listener_loop(self):
        """Background polling thread: periodically check cloud for new links"""
        import requests as req
        config = load_config()
        url = config.get('listener_worker_url', '').rstrip('/')
        secret = config.get('listener_secret', '')
        interval = config.get('listener_interval_seconds', 30)

        while not self.listener_stop.is_set():
            try:
                resp = req.get(
                    f'{url}/api/poll',
                    params={'secret': secret},
                    timeout=15
                )
                if resp.status_code == 200:
                    data = resp.json()
                    links = data.get('links', [])
                    for item in links:
                        link = item.get('link', '')
                        if link:
                            self.log(f'收到远程链接: {link}')
                            self.log_q.put(('listener_link', link))
            except Exception as e:
                self.log(f'监听轮询失败: {e}')

            for _ in range(interval):
                if self.listener_stop.is_set():
                    break
                time.sleep(1)

    def _on_listener_link(self, link):
        """Handle received remote link (main thread)"""
        if self.running:
            self.task_queue.append(link)
            self.log(f'当前有任务在运行, 链接已加入队列 (队列中: {len(self.task_queue)} 条)')
            return
        self._start_task(link)

    def _start_task(self, link):
        """Start executing a task"""
        self.url_var.set(link)
        self.log(f'自动填入链接: {link}')
        self._run_oneclick()

    def _on_task_completed(self):
        """Check queue after task completion, continue if next task exists"""
        if not self.task_queue:
            return
        # Check publish interval
        config = load_config()
        interval_min = config.get('publish_interval_minutes', 120)
        if self.last_publish_time and interval_min > 0:
            from datetime import datetime, timedelta
            elapsed = datetime.now() - self.last_publish_time
            wait_sec = interval_min * 60 - elapsed.total_seconds()
            if wait_sec > 0:
                self.log(f'发布间隔中, {int(wait_sec // 60)} 分钟后执行下一条...')
                self.root.after(int(wait_sec * 1000), self._process_next_task)
                return
        self._process_next_task()

    def _process_next_task(self):
        """Process next task in queue"""
        if not self.task_queue:
            return
        if self.running:
            return
        link = self.task_queue.pop(0)
        self.log(f'')
        self.log(f'========== 开始执行队列任务 (剩余: {len(self.task_queue)} 条) ==========')
        self._start_task(link)

    def _notify_pushplus(self, title, content):
        """Send WeChat notification via PushPlus"""
        try:
            import requests as req
            config = load_config()
            token = config.get('pushplus_token', '').strip()
            if not token:
                return
            req.post('http://www.pushplus.plus/send',
                json={'token': token, 'title': title, 'content': content},
                timeout=10)
            self.log('已发送微信通知')
        except Exception:
            pass

    # ----------------------------------------------------------------
    # Tab change event
    # ----------------------------------------------------------------
    def _on_tab_changed(self, event=None):
        """Handle Tab switch in debug mode"""
        self._auto_save_edits()
        try:
            idx = self.notebook.index(self.notebook.select())
            self.current_step = idx + 1
            self._update_button_text()
            self._update_workspace_buttons()
        except Exception:
            pass

    # ----------------------------------------------------------------
    # Main button (start/stop toggle)
    # ----------------------------------------------------------------
    def _on_main_btn(self):
        if self.running:
            self.stop_flag.set()
            self._append_log('用户取消...')
        else:
            tab = self._get_workspace_tab()
            if tab == 0:
                self._run_oneclick()
            else:
                self._run_debug_step()

    def _run_oneclick(self):
        url = self.url_var.get().strip()
        if not url:
            return
        self.running = True
        self.stop_flag.clear()
        self._update_button_text()
        for widget in [self.log_text, self._debug_log_widget]:
            widget.config(state='normal')
            widget.delete('1.0', 'end')
            widget.config(state='disabled')
        for i in range(1, 8):
            self._update_step(i, 'reset')
        threading.Thread(target=self._run_pipeline, args=(url,), daemon=True).start()

    def _run_debug_step(self):
        """Debug mode: run current step"""
        step = max(1, min(self.current_step, 7))
        self._auto_save_edits()

        self.running = True
        self.stop_flag.clear()
        self._update_button_text()
        threading.Thread(
            target=self._run_single_step, args=(step,), daemon=True).start()

    # ----------------------------------------------------------------
    # Step completion callbacks (main thread)
    # ----------------------------------------------------------------
    def _on_step_done(self, step):
        self.completed_up_to = max(self.completed_up_to, step)
        next_s = min(step + 1, 7)
        self.current_step = next_s
        self._update_button_text()

    # ----------------------------------------------------------------
    # Complete pipeline (one-click mode)
    # ----------------------------------------------------------------
    def _run_pipeline(self, douyin_input):
        success = False
        try:
            self._step1_extract(douyin_input)
            if self.stop_flag.is_set(): raise InterruptedError()
            self._step2_rewrite()
            if self.stop_flag.is_set(): raise InterruptedError()
            self._step3_split()
            if self.stop_flag.is_set(): raise InterruptedError()
            self._step4_search()
            if self.stop_flag.is_set(): raise InterruptedError()
            self._step5_tts()
            if self.stop_flag.is_set(): raise InterruptedError()
            self._step6_render()
            if self.stop_flag.is_set(): raise InterruptedError()
            # Step 7: Auto publish (if configured)
            self._auto_publish_after_render()
            success = True
        except InterruptedError:
            self.log('用户取消了流水线')
            self.set_status('已取消')
        except Exception as e:
            self.log(f'错误: {e}')
            import traceback; self.log(traceback.format_exc())
            self.set_status(f'失败: {e}')
        finally:
            self.log_q.put(('done', None))
            # Pre-fill step 7 form
            self.log_q.put(('step7_prefill', None))
            if success:
                self._notify_pushplus(
                    '速影 - 视频生成完成',
                    f'视频《{self.title}》已生成完毕')
            elif not self.stop_flag.is_set():
                self._notify_pushplus(
                    '速影 - 视频生成失败',
                    f'视频生成过程中出现错误, 请查看电脑日志')

    def _run_single_step(self, step):
        """Debug mode: execute single step (supports step skipping, auto-checks prerequisites)"""
        try:
            if self.stop_flag.is_set():
                raise InterruptedError()
            # Skip-step prerequisite data check
            if step == 2 and not self.raw_narration:
                self.log('步骤2需要原始文案, 请先运行步骤1提取, 或在Tab2直接粘贴文案后跳到步骤3')
                return
            if step >= 3 and not self.narration:
                self.log('缺少文案数据, 请先在Tab2输入文案并点击"保存修改"')
                return
            if step >= 3 and not self.proc_dir:
                self.log('缺少输出目录, 请先在Tab2输入文案并点击"保存修改"')
                return
            if step >= 4 and not self.segments:
                self.log('缺少分镜数据, 请先运行步骤3(分镜切分)')
                return
            if step == 6 and not self.images:
                self.log('缺少配图数据, 请先运行步骤4(搜索配图)')
                return
            if step == 7:
                # Step 7 publish: execute directly
                self._step7_do_publish()
                return

            if self.stop_flag.is_set():
                raise InterruptedError()
            if step == 1:
                url = self.url_var.get().strip()
                if not url:
                    self.log('请输入抖音链接')
                    return
                self._step1_extract(url)
            elif step == 2:
                self._step2_rewrite()
            elif step == 3:
                self._step3_split()
            elif step == 4:
                self._step4_search()
            elif step == 5:
                self._step5_tts()
            elif step == 6:
                self._step6_render()
            self.log_q.put(('debug_step', step))
        except InterruptedError:
            self.log('用户取消了流水线')
            self.set_status('已取消')
        except Exception as e:
            self.log(f'错误: {e}')
            import traceback; self.log(traceback.format_exc())
            self.set_status(f'步骤{step}失败')
        finally:
            self.log_q.put(('done', None))

    # ----------------------------------------------------------------
    # Step implementations
    # ----------------------------------------------------------------
    def _step1_extract(self, douyin_input):
        from extract_narration import (extract_share_url, resolve_video_id,
            get_video_info, download_and_extract_audio, transcribe_audio)

        self.set_step(1, 'active')
        self.set_status('提取文案中...')
        self.log('=' * 50)
        self.log('[步骤1/7] 提取抖音文案...')

        is_url = douyin_input.startswith('http') or 'douyin' in douyin_input
        if is_url:
            share_url = extract_share_url(douyin_input)
            self.log(f'  链接: {share_url}')
            vid = resolve_video_id(share_url)
            if not vid:
                raise RuntimeError('无法解析视频ID')
            info = get_video_info(vid)
            self.log(f'  作者: {info["author"]}')
            video_urls = info.get('video_urls') or [info.get('video_url')]
            if not any(video_urls):
                raise RuntimeError('无法获取视频地址')
            wav = CACHE / f'_audio_{vid}.wav'
            try:
                t0 = time.perf_counter()
                self.log('  开始下载视频并提取音频...')
                download_and_extract_audio(video_urls, wav)
                self.log(f'  音频提取完成, 用时 {time.perf_counter() - t0:.1f} 秒')
                self.log('  开始语音识别...')
                t0 = time.perf_counter()
                raw = transcribe_audio(wav)
                self.log(f'  语音识别完成, 用时 {time.perf_counter() - t0:.1f} 秒')
            finally:
                if wav.exists():
                    os.remove(wav)
        else:
            raw = Path(douyin_input).read_text(encoding='utf-8').strip()
            self.log(f'  已加载文案: {len(raw)}字')

        self.log(f'  文案长度: {len(raw)} 字')
        self.raw_narration = raw

        self.t1_text.delete('1.0', 'end')
        self.t1_text.insert('1.0', raw)

        self.set_step(1, 'done')
        self.log_q.put(('tab', 0))

    def _step2_rewrite(self):
        config = load_config()

        self.set_step(2, 'active')
        self.set_status('AI改写中...')
        self.log('=' * 50)
        self.log('[步骤2/7] AI改写文案...')

        raw = self.raw_narration
        tm = re.search(r'【标题】\s*\n?(.+)', raw)
        bm = re.search(r'【优化口播文案】\s*\n?([\s\S]+)', raw)
        if tm and bm:
            title, narration = tm.group(1).strip(), bm.group(1).strip()
            self.log('  已有格式标记, 跳过改写')
        else:
            tpl = (CFG_DIR / 'ai生故事模板.txt').read_text(encoding='utf-8-sig')
            learn_ctx = _get_learning_prompt()
            prompt = tpl.rstrip()
            if learn_ctx:
                prompt += '\n\n' + learn_ctx
                self.log('  已注入学习偏好')  # Learning preference injected
            prompt += '\n\n' + raw
            self.log(f'  模型: {config["openrouter_model"]}')
            d = call_openrouter_chat(
                config,
                prompt,
                max_tokens=config.get('openrouter_max_tokens', 4000),
                timeout=180,
                log_func=self.log,
            )
            txt = d['choices'][0]['message']['content'].strip()
            usage = d.get('usage', {})
            self.log(f'  tokens: {usage.get("total_tokens", 0)}, cost: ${usage.get("cost", 0)}')
            t2 = re.search(r'【标题】\s*\n?(.+)', txt)
            b2 = re.search(r'【优化口播文案】\s*\n?([\s\S]+)', txt)
            title = t2.group(1).strip() if t2 else raw[:6]
            narration = b2.group(1).strip() if b2 else txt
            if len(narration) > 1400:
                self.log(f'  改写文案偏长({len(narration)}字), 二次压缩并规范化...')
                compress_prompt = (
                    '请对下面已经改写过的口播文案做二次压缩和规范化。\n'
                    '要求：\n'
                    '1. 标题保持不变，不要换标题。\n'
                    '2. 正文压缩到1100-1300个中文字符，最多不能超过1400个中文字符。\n'
                    '3. 保留主线冲突、关键转折和结尾余味，不要写成提纲。\n'
                    '4. 删除重复解释、重复对话、重复哭诉和不影响主线的旁枝。\n'
                    '5. 自动替换可替换的人名、地名、村名、店名、公司名等，避免和原故事完全同名。\n'
                    '6. 仍然使用现代情感故事口播风格，短句、口语化、适合TTS。\n'
                    '7. 必须严格只输出下面两段，不要添加任何解释。\n\n'
                    '【标题】\n'
                    f'{title}\n\n'
                    '【优化口播文案】\n'
                    f'{narration}'
                )
                d2 = call_openrouter_chat(
                    config,
                    compress_prompt,
                    max_tokens=2500,
                    timeout=180,
                    log_func=self.log,
                )
                txt2 = d2['choices'][0]['message']['content'].strip()
                usage2 = d2.get('usage', {})
                self.log(f'  二次压缩 tokens: {usage2.get("total_tokens", 0)}, cost: ${usage2.get("cost", 0)}')
                b3 = re.search(r'【优化口播文案】\s*\n?([\s\S]+)', txt2)
                narration = b3.group(1).strip() if b3 else txt2
                self.log(f'  二次压缩后文案: {len(narration)} 字')

        self.title = title
        self.narration = narration
        self.log(f'  标题: {title}')
        self.log(f'  改写文案: {len(narration)} 字')

        safe = re.sub(r'[\\/:*?"<>|]', '_', title)
        self.run_dir = Path(self.output_dir) / safe
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.proc_dir = self.run_dir / '过程'
        self.proc_dir.mkdir(exist_ok=True)
        (self.proc_dir / '01_rewritten_narration.txt').write_text(
            f'【标题】\n{title}\n\n【优化口播文案】\n{narration}', encoding='utf-8')

        self.t2_text.delete('1.0', 'end')
        ai_output = f'【标题】\n{title}\n\n【优化口播文案】\n{narration}'
        self.t2_text.insert('1.0', ai_output)
        self._last_ai_rewrite_text = ai_output
        self.save_lbl.config(text='')

        self.set_step(2, 'done')
        self.log_q.put(('tab', 1))

    def _step3_split(self):
        from video_pipeline import ai_split_narration
        config = load_config()

        self._auto_save_edits()

        self.set_step(3, 'active')
        self.set_status('分镜切分中...')
        self.log('=' * 50)
        self.log('[步骤3/7] 分镜切分...')

        num_shots = config.get('num_shots', 5)
        self.log(f'  尝试AI按故事情节分镜, 最多 {num_shots} 段...')
        segs = ai_split_narration(self.narration, config, num_shots, log_func=self.log)
        self.log('  AI分镜成功')
        self.segments = segs
        self.log(f'  共 {len(segs)} 个分镜, 预估 {sum(s["duration"] for s in segs):.0f}秒')
        for s in segs:
            self.log(f'  镜{s["id"]:2d} ({s["duration"]:5.1f}s): {s["text"][:30]}...')

        if self.proc_dir:
            (self.proc_dir / '02_storyboard.json').write_text(
                json.dumps(segs, ensure_ascii=False, indent=2), encoding='utf-8')

        self.t3_text.delete('1.0', 'end')
        lines = [f'共 {len(segs)} 个分镜\n\n']
        for s in segs:
            lines.append(f'镜{s["id"]:2d} ({s["duration"]:.1f}s):\n  {s["text"]}\n\n')
        self.t3_text.insert('1.0', ''.join(lines))

        self.set_step(3, 'done')
        self.log_q.put(('tab', 2))

    def _step4_search(self):
        from video_pipeline import search_and_download_images
        config = load_config()

        self.set_step(4, 'active')
        self.set_status('搜索配图中...')
        self.log('=' * 50)
        self.log('[步骤4/7] 搜索实拍配图...')

        # Build keyword learning context
        learn_data = _load_learning()
        kw_corrections = learn_data.get('keyword_corrections', [])
        kw_ctx = ''
        if kw_corrections:
            kw_ctx = '【用户偏好的配图关键词修正, 请分析用户的选词风格并应用到本次提取中】'
            for c in kw_corrections[-15:]:
                kw_ctx += f'\n"{c["original"]}" → "{c["corrected"]}"'
            self.log('  已注入关键词偏好')  # Keyword preference injected

        imgs = search_and_download_images(self.segments, config, self.proc_dir,
                                          learning_context=kw_ctx)
        self.images = imgs
        self.log(f'  成功: {len(imgs)}/{len(self.segments)} 张')

        self.root.after(0, self._show_image_grid)

        self.set_step(4, 'done')
        self.log_q.put(('tab', 3))

    def _step5_tts(self):
        from video_pipeline import generate_tts
        config = load_config()

        self._auto_save_edits()

        self.set_step(5, 'active')
        self.set_status('TTS合成中...')
        self.log('=' * 50)
        self.log(f'[步骤5/7] TTS语音合成 ({config["tts_voice"]})...')

        audio = self.proc_dir / 'narration.mp3'
        tts_rate = self.tts_rate_var.get().strip() or config.get('tts_rate', '-5%')
        audio_path, word_boundaries = generate_tts(self.narration, audio, config['tts_voice'], tts_rate)
        self.audio_path = audio_path
        self.word_boundaries = word_boundaries
        self.log(f'  音频: {audio.name}')
        self.log(f'  词边界: {len(word_boundaries)} 个')
        self.log_q.put(('tts_status', f'音频已生成: {audio.name}'))

        self.set_step(5, 'done')
        self.log_q.put(('tab', 4))

    def _step6_render(self):
        from video_pipeline import create_kenburns_clip
        config = load_config()
        ffmpeg = config['ffmpeg_path']

        self._auto_save_edits()

        self.set_step(6, 'active')
        self.set_status('视频渲染中...')
        self.log('=' * 50)
        self.log('[步骤6/7] 视频渲染...')

        W, H, fps = config['video_width'], config['video_height'], config['fps']
        dirs = ['zoom_in', 'zoom_out', 'pan_left', 'pan_right']
        tc = sum(len(s['text']) for s in self.segments)
        estimated_duration = sum(x['duration'] for x in self.segments)
        audio_duration = probe_media_duration(ffmpeg, self.audio_path, estimated_duration)
        target_duration = max(audio_duration, 1.0)
        cover_duration = 1.0
        output_duration = target_duration + cover_duration
        audio_delay_ms = int(round(cover_duration * 1000))
        self.log(f'  音频基准时长: {target_duration:.1f}s, 封面静音: {cover_duration:.3f}s')

        fonts_dir = CACHE / 'ffmpeg_fonts'
        fonts_conf = fonts_dir / 'fonts.conf'
        if not fonts_conf.exists():
            fonts_dir.mkdir(parents=True, exist_ok=True)
            fonts_conf.write_text(
                '<?xml version="1.0"?>\n<fontconfig>\n'
                '  <dir>C:\\Windows\\Fonts</dir>\n'
                '  <cachedir>C:\\Windows\\Temp\\fontconfig_cache</cachedir>\n'
                '</fontconfig>', encoding='utf-8')
        ff_env = os.environ.copy()
        ff_env['FONTCONFIG_PATH'] = str(fonts_dir)
        ff_env['FONTCONFIG_FILE'] = 'fonts.conf'

        render_dir = CACHE / 'render'
        render_dir.mkdir(parents=True, exist_ok=True)

        # 6a: Ken Burns animation clips
        self.log('  [6a] Ken Burns动画片段...')
        cdir = self.proc_dir / 'clips'
        cdir.mkdir(exist_ok=True)
        cfs = []
        trans_dur = 0.5 if config.get('transition_enabled', True) else 0
        if trans_dur > 0:
            self.log(f'    转场: 淡入淡出 {trans_dur}s')
        render_items = []
        for s in self.segments:
            im = next((r for r in self.images if r['id'] == s['id']), None)
            if not im:
                continue
            sd = max((len(s['text']) / tc) * target_duration, 3.0)
            if render_items and render_items[-1]['image_path'] == im['image_path']:
                render_items[-1]['duration'] += sd
                render_items[-1]['ids'].append(s['id'])
            else:
                render_items.append({
                    'ids': [s['id']],
                    'image_path': im['image_path'],
                    'duration': sd,
                })
        for idx, item in enumerate(render_items):
            first_id = item['ids'][0]
            cp = cdir / f"clip_{first_id:02d}.mp4"
            dr = dirs[idx % 4]
            fade_in = 0 if not cfs else trans_dur
            fade_out = 0 if idx == len(render_items) - 1 else trans_dur
            if create_kenburns_clip(
                item['image_path'], cp, item['duration'], W, H, fps, ffmpeg, dr,
                transition_duration=trans_dur,
                fade_in_duration=fade_in,
                fade_out_duration=fade_out,
            ):
                cfs.append(str(cp))
                id_text = f'{item["ids"][0]}-{item["ids"][-1]}' if len(item['ids']) > 1 else str(first_id)
                self.log(f'    + 片段{id_text}: {dr} {item["duration"]:.1f}s')
            else:
                self.log(f'    x 片段{first_id}失败')
        if not cfs:
            raise RuntimeError('没有成功创建任何视频片段!')

        # 6b: Concatenate clips
        self.log('  [6b] 拼接片段...')
        cl = self.proc_dir / 'concat_list.txt'
        cl.write_text(''.join(f"file '{c}'\n" for c in cfs), encoding='utf-8')
        cat = self.proc_dir / 'concat_video.mp4'
        concat_result = subprocess.run([ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', str(cl),
            '-c', 'copy', str(cat)],
            capture_output=True, text=True, timeout=300, **NW)
        if concat_result.returncode != 0 or not cat.exists() or cat.stat().st_size <= 0:
            raise RuntimeError('视频片段快速拼接失败: 中间片段参数不一致或输出无效')

        # 6c: Overlay TTS audio
        self.log('  [6c] 叠加TTS音频...')
        wa = self.proc_dir / 'video_with_audio.mp4'
        audio_result = subprocess.run([ffmpeg, '-y', '-i', str(cat), '-i', str(self.audio_path),
            '-t', f'{output_duration:.3f}', '-filter:a', f'adelay={audio_delay_ms}:all=1',
            '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '128k',
            '-map', '0:v:0', '-map', '1:a:0', str(wa)],
            capture_output=True, text=True, timeout=300, **NW)
        if audio_result.returncode != 0 or not wa.exists() or wa.stat().st_size <= 0:
            raise RuntimeError('视频叠加音频失败: 输出无效')

        tdur = probe_media_duration(ffmpeg, wa, output_duration)
        if abs(tdur - output_duration) > 1.0:
            raise RuntimeError(f'视频叠加音频后时长异常: {tdur:.1f}s, 预期 {output_duration:.1f}s')

        # 6d: Render title cover
        self.log('  [6d] 渲染标题封面...')
        ss = render_dir / 'source.mp4'
        shutil.copy2(str(wa), str(ss))
        to = render_dir / 'title.mp4'
        te = self.title.replace("'", "'\\''").replace(":", "\\:")
        cover_expr = f"lt(t\\,{cover_duration:.4f})"
        content_expr = f"gte(t\\,{cover_duration:.4f})"
        vf1 = (f"drawbox=x=0:y=870:w=1080:h=220:color=white@0.6:t=fill:enable='{cover_expr}',"
               f"drawtext=fontfile='C\\:/Windows/Fonts/msyhbd.ttc':text='{te}'"
               f":fontsize=128:fontcolor=0xFFE600:borderw=9:bordercolor=black"
               f":x=(w-text_w)/2:y=918:enable='{cover_expr}',"
               f"drawtext=fontfile='C\\:/Windows/Fonts/msyhbd.ttc':text='全'"
               f":fontsize=82:fontcolor=0xFFE600:borderw=7:bordercolor=black"
               f":x=(w-text_w)/2:y=1210:enable='{cover_expr}',"
               f"drawtext=fontfile='C\\:/Windows/Fonts/msyhbd.ttc':text='{te}'"
               f":fontsize=64:fontcolor=0xFFE600:borderw=5:bordercolor=black"
               f":x=(w-text_w)/2:y=110:enable='{content_expr}'")
        r1 = subprocess.run([ffmpeg, '-y', '-i', str(ss), '-vf', vf1,
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23', '-c:a', 'copy', str(to)],
            capture_output=True, text=True, env=ff_env, timeout=600, **NW)
        if r1.returncode != 0:
            self.log('    标题渲染失败, 跳过')
            to = ss

        # 6e: Generate smart subtitles
        self.log('  [6e] 生成智能字幕...')
        af = render_dir / 'final_sub.ass'

        def ft(t):
            return f"{int(t//3600)}:{int((t%3600)//60):02d}:{int(t%60):02d}.{int((t%1)*100):02d}"

        def ssp(text, ml=18):
            ps = re.split(r'(?<=[，,。！？；、])', text)
            sg = [s.strip() for s in ps if s.strip()]
            rs = []
            for g in sg:
                if len(g) <= ml:
                    rs.append(g)
                else:
                    sb = re.split(r'(?<=[，,、；：""\'\'（）—])', g)
                    sb = [s for s in sb if s.strip()]
                    b = ''
                    for s in sb:
                        if b and len(b+s) > ml:
                            rs.append(b); b = s
                        else:
                            b += s
                    if b:
                        if len(b) > ml:
                            for j in range(0, len(b), ml):
                                rs.append(b[j:j+ml])
                        else:
                            rs.append(b)
            return rs

        def subtitle_text(text):
            text = re.sub(r'[\r\n]+', ' ', text).strip()
            return re.sub(r'[，,。.!！？?；;、：:]+$', '', text).strip()

        def ass_text(text):
            return text.replace('\\', '\\\\').replace('{', '').replace('}', '')

        def add_evt(start, end, text):
            text = subtitle_text(text)
            start += cover_duration
            end += cover_duration
            if text and end > start:
                evts.append(f"Dialogue: 1,{ft(start)},{ft(end)},Default,,0,0,0,,{ass_text(text)}")
                evt_times.append((start, end))

        def build_ratio_events(reason):
            nonlocal evts, evt_times
            self.log(f'    {reason}, 改用稳定时间轴')
            evts, evt_times = [], []
            tsc = sum(len(l) for l in alls) if alls else 1
            cur = 0.0
            subtitle_duration = max(tdur - cover_duration, 1.0)
            for l in alls:
                d = (len(l) / tsc) * subtitle_duration if tsc else subtitle_duration / len(alls)
                e = min(cur + d, subtitle_duration)
                add_evt(cur, e, l)
                cur = e

        evts, evt_times, alls = [], [], []
        for s in self.segments:
            alls.extend(ssp(s['text']))

        # Prefer Edge TTS word boundaries for precise subtitle alignment
        wb = getattr(self, 'word_boundaries', [])
        if wb and alls:
            self.log('    使用词边界精准对齐字幕')
            sub_idx = 0
            acc_text = ''
            cur_start = wb[0]['start'] if wb else 0.0
            for b in wb:
                acc_text += b['text']
                # Try to match current subtitle line
                if sub_idx < len(alls):
                    target = alls[sub_idx]
                    # Normalize by removing punctuation for comparison
                    norm_acc = re.sub(r'[_\W]+', '', acc_text)
                    norm_tgt = re.sub(r'[_\W]+', '', target)
                    if norm_acc and norm_tgt and (norm_acc == norm_tgt or norm_acc.startswith(norm_tgt)):
                        add_evt(cur_start, b['end'], target)
                        sub_idx += 1
                        acc_text = ''
                        cur_start = b['end']
            if sub_idx < len(alls):
                build_ratio_events('词边界未匹配完整字幕')
            elif evt_times:
                gaps = [evt_times[i][0] - evt_times[i - 1][1] for i in range(1, len(evt_times))]
                max_gap = max(gaps) if gaps else 0
                gap_limit = max(2.5, min(tdur * 0.08, 5.0))
                last_start, last_end = evt_times[-1]
                if max_gap > gap_limit:
                    build_ratio_events(f'词边界字幕中间空档过大({max_gap:.1f}s)')
                elif last_end > tdur + 0.05 or last_start >= max(tdur - 0.5, tdur * 0.98):
                    build_ratio_events('词边界字幕结束时间异常')
        else:
            build_ratio_events('无词边界数据')
        ae = str(af).replace('\\', '/').replace(':', '\\:')
        af.write_text(r"""[Script Info]
Title: Final
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Default,Microsoft YaHei,86,&H0000E6FF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,8,0,2,120,120,500

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + '\n'.join(evts), encoding='utf-8-sig')
        self.log(f'    字幕: {len(evts)}条')

        # 6f: Render subtitles
        self.log('  [6f] 渲染字幕...')
        fo = render_dir / 'final.mp4'
        r2 = subprocess.run([ffmpeg, '-y', '-i', str(to), '-vf', f"ass='{ae}'",
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23', '-c:a', 'copy', str(fo)],
            capture_output=True, text=True, env=ff_env, timeout=900, **NW)
        if r2.returncode != 0 or not fo.exists():
            self.log('    字幕渲染失败, 使用标题版本')
            fo = to

        fd = self.run_dir / f'{self.title}--成品.mp4'
        if fd.exists():
            os.remove(fd)
        shutil.copy2(str(fo), str(fd))
        for t in [ss, to, fo, af]:
            if t.exists():
                try: os.remove(t)
                except: pass

        mb = os.path.getsize(str(fd)) / 1024 / 1024
        self.log('')
        self.log('#' * 50)
        self.log(f' 完成! {fd}')
        self.log(f' 大小: {mb:.1f} MB')
        self.log('#' * 50)

        self.log_q.put(('t6_result', f'视频已生成: {fd.name} ({mb:.1f} MB)'))
        self.set_step(6, 'done')
        self.set_status(f'完成! {mb:.1f} MB')
        self.log_q.put(('tab', 5))

        # Extract first frame and crop as portrait cover (3:4) and landscape cover (4:3)
        self.cover_portrait_path = None
        self.cover_landscape_path = None
        try:
            # Portrait cover 3:4 (1080x1440, center-cropped from 1080x1920)
            cover_p = self.run_dir / 'cover_portrait.jpg'
            subprocess.run([ffmpeg, '-y', '-i', str(fd),
                '-vf', 'select=eq(n\\,0),crop=iw:ih*3/4:0:(oh-ih*3/4)/2',
                '-frames:v', '1', '-q:v', '2', str(cover_p)],
                capture_output=True, timeout=30, **NW)
            if cover_p.exists():
                self.cover_portrait_path = str(cover_p)
                self.log(f'  竖封面(3:4): {cover_p.name}')

            # Landscape cover 4:3 (center-crop 1080x810, then scale to 1440x1080)
            cover_l = self.run_dir / 'cover_landscape.jpg'
            subprocess.run([ffmpeg, '-y', '-i', str(fd),
                '-vf', 'select=eq(n\\,0),crop=iw*3/4:ih:(ow-iw*3/4)/2:0,scale=1440:1080',
                '-frames:v', '1', '-q:v', '2', str(cover_l)],
                capture_output=True, timeout=30, **NW)
            if cover_l.exists():
                self.cover_landscape_path = str(cover_l)
                self.log(f'  横封面(4:3): {cover_l.name}')
        except Exception as e:
            self.log(f'  封面截取失败(不影响发布): {e}')  # Cover extraction failed (does not affect publishing)

    # ----------------------------------------------------------------
    # One-click: Auto publish (called within pipeline)
    # ----------------------------------------------------------------
    def _auto_publish_after_render(self):
        """Auto-publish after rendering using generated title and saved settings."""
        try:
            config = load_config()

            self.log('')
            self.log('=' * 50)
            self.log('[自动发布] 准备发布到抖音...')

            from publisher import publish_to_douyin, check_douyin_login, split_douyin_tags

            if not check_douyin_login():
                self.log('抖音未登录或 cookie 已失效, 跳过自动发布')
                return

            video_path = self.run_dir / f'{self.title}--成品.mp4'
            if not video_path.exists():
                self.log(f'视频文件不存在: {video_path}')
                return

            title = self.title
            desc = config.get('pub_desc', '').strip()

            kwargs = dict(
                video_path=str(video_path),
                title=title,
                tags=split_douyin_tags(desc),
                description='',
                headless=True,
                debug=False,
                thumbnail_portrait_path=self.cover_portrait_path,
                thumbnail_landscape_path=self.cover_landscape_path,
            )

            self.log(f'  标题: {title}')
            self.log(f'  话题: {desc if desc else "(无)"}')
            self.log('  方式: 立即发布')
            self.set_status('自动发布到抖音中...')

            result = publish_to_douyin(**kwargs)

            if result['success']:
                self.log('✓ 自动发布成功!')
                self.set_status('自动发布成功')
                self.last_publish_time = datetime.now()
            else:
                self.log(f'✗ 自动发布失败: {result["message"]}')
                self.set_status(f'自动发布失败: {result["message"]}')
        except Exception as e:
            self.log(f'自动发布异常: {e}')

    # ----------------------------------------------------------------
    # Debug mode: Step 4 image management
    # ----------------------------------------------------------------
    def _show_image_grid(self):
        """Step 4: Display search image results in list form"""
        for w in self.t4_container.winfo_children():
            w.destroy()
        if not self.segments:
            ttk.Label(self.t4_container, text='暂无分镜数据').pack(pady=20)
            return

        canvas = tk.Canvas(self.t4_container, highlightthickness=0)
        sb = ttk.Scrollbar(self.t4_container, orient='vertical', command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        try:
            self.root.unbind_all('<MouseWheel>')
        except Exception:
            pass
        canvas.bind_all('<MouseWheel>', on_mousewheel)

        for idx, seg in enumerate(self.segments):
            sid = seg['id']
            keyword = seg.get('text', '')
            img = next((r for r in self.images if r['id'] == sid), None)
            # Show actual search keyword first, fallback to storyboard text
            keyword = img.get('search_query', '') if img else seg.get('text', '')

            row = ttk.Frame(inner)
            row.pack(fill='x', padx=4, pady=2)

            # Shot number
            ttk.Label(row, text=f'镜{sid}', width=5).pack(side='left')

            # Keyword (clickable to re-search)
            kw_btn = ttk.Button(row, text=keyword[:20] if len(keyword) > 20 else keyword,
                                width=24,
                                command=lambda i={'id': sid, 'image_path': img['image_path'] if img else '', 'keyword': keyword}: self._re_search_image(i))
            kw_btn.pack(side='left', padx=(4, 0))

            # Status
            if img:
                ttk.Label(row, text='✓', foreground='green').pack(side='left', padx=(6, 0))
                # Open image folder
                img_dir = str(Path(img['image_path']).parent)
                ttk.Button(row, text='图片', width=5,
                           command=lambda d=img_dir: subprocess.Popen(
                               f'explorer "{d}"' if sys.platform == 'win32' else ['xdg-open', d])
                           ).pack(side='left', padx=(6, 0))
            else:
                ttk.Label(row, text='✗ 未搜索', foreground='red').pack(side='left', padx=(6, 0))

    def _pick_local_image(self, img_info):
        fp = filedialog.askopenfilename(
            title=f'选择图片 - 镜头{img_info["id"]}',
            filetypes=[('图片', '*.jpg *.jpeg *.png *.bmp *.webp'), ('所有', '*.*')])
        if fp:
            dest = Path(img_info['image_path'])
            shutil.copy2(fp, str(dest))
            self.log(f'  镜头{img_info["id"]} 已替换为本地图片')
            self._show_image_grid()

    def _re_search_image(self, img_info):
        """Click keyword button to re-search images"""
        sid = img_info['id']
        old_kw = img_info.get('keyword', '')
        keyword = simpledialog.askstring('重新搜索',
            f'镜头{sid} 搜索关键词:', initialvalue=old_kw, parent=self.root)
        if not keyword or keyword == old_kw:
            return
        # Record keyword correction preference
        _add_keyword_correction(old_kw, keyword)
        # Update keyword in segments
        for s in self.segments:
            if s['id'] == sid:
                s['text'] = keyword
                break
        from video_pipeline import search_and_download_images
        config = load_config()

        def do_search():
            try:
                seg = {'id': img_info['id'], 'text': keyword, 'duration': 5}
                results = search_and_download_images([seg], config, self.proc_dir)
                if results:
                    for i, img in enumerate(self.images):
                        if img['id'] == img_info['id']:
                            self.images[i] = results[0]
                            break
                    self.log(f'  镜头{img_info["id"]} 已重新搜索: "{keyword}"')
                    self.root.after(0, self._show_image_grid)
                else:
                    self.log(f'  镜头{img_info["id"]} 搜索无结果')
            except Exception as e:
                self.log(f'  搜索失败: {e}')
            finally:
                self.log_q.put(('done', None))

        self.running = True
        self._update_button_text()
        threading.Thread(target=do_search, daemon=True).start()

    # ----------------------------------------------------------------
    # Debug mode: Step 5 TTS
    # ----------------------------------------------------------------
    def _preview_tts(self):
        if not self.audio_path or not Path(self.audio_path).exists():
            messagebox.showinfo('提示', '请先生成配音')
            return
        try:
            if sys.platform == 'win32':
                os.startfile(str(self.audio_path))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(self.audio_path)])
            else:
                subprocess.Popen(['xdg-open', str(self.audio_path)])
        except Exception as e:
            self.log(f'播放失败: {e}')

    # ----------------------------------------------------------------
    # Douyin login & status check
    # ----------------------------------------------------------------
    def _login_douyin_dialog(self):
        """Show Douyin login dialog"""
        def do_login():
            try:
                from publisher import login_douyin

                self.log('=' * 50)
                self.log('[抖音登录] 正在准备登录...')
                self.log('浏览器即将打开, 请用抖音 APP 扫码登录')
                self.set_status('抖音登录中...')

                def qrcode_callback(qr_info):
                    # Browser already shows QR code, no need to open saved image
                    pass

                result = login_douyin(qrcode_callback=qrcode_callback)

                if result['success']:
                    self.log('✓ 登录成功!')
                    self.set_status('抖音登录成功')
                    self.root.after(0, lambda: self._update_douyin_status_ui(('已登录', 'green')))
                    self.root.after(0, lambda: messagebox.showinfo('登录结果', '抖音登录成功!'))
                else:
                    self.log(f'✗ 登录失败: {result["message"]}')
                    self.set_status(f'登录失败: {result["message"]}')
                    self.root.after(0, lambda: self._update_douyin_status_ui(('未登录', 'red')))
                    self.root.after(0, lambda: messagebox.showerror('登录结果', f'登录失败: {result["message"]}'))
            except Exception as e:
                self.log(f'登录异常: {e}')
                self.root.after(0, lambda: messagebox.showerror('登录异常', str(e)))
            finally:
                self.log_q.put(('done', None))

        self.running = True
        self._update_button_text()
        threading.Thread(target=do_login, daemon=True).start()

    def _check_douyin_status(self):
        """Check Douyin login status"""
        try:
            from publisher import check_douyin_login
            self.log('检查抖音登录状态...')
            self.douyin_status_label.config(text='检查中...', foreground='gray')
            if check_douyin_login():
                self.log('✓ 抖音已登录')
                self._update_douyin_status_ui(('已登录', 'green'))
                messagebox.showinfo('抖音状态', '抖音已登录, 可以发布视频')
            else:
                self.log('✗ 抖音未登录或 cookie 已失效')
                self._update_douyin_status_ui(('未登录', 'red'))
                messagebox.showinfo('抖音状态', '抖音未登录或 cookie 已失效, 请重新登录')
        except Exception as e:
            self.log(f'检查状态失败: {e}')
            self._update_douyin_status_ui(('检查失败', 'red'))

    # ----------------------------------------------------------------
    # Main loop
    # ----------------------------------------------------------------
    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    App().run()
