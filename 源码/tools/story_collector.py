"""
Story material collector for Suying.

This is a standalone local tool. It searches Douyin for modern emotional story
videos, lets the user review candidates, then submits selected links to the
existing Suying Pages endpoint.
"""

import json
import queue
import re
import os
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import quote

import requests


try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = PROJECT_ROOT / '缓存'
COLLECTOR_DIR = CACHE_DIR / 'story_collector'
PROFILE_DIR = COLLECTOR_DIR / 'douyin_profile'
CANDIDATES_PATH = CACHE_DIR / 'story_collector_candidates.json'
HISTORY_PATH = CACHE_DIR / 'story_collector_history.json'
PREFERENCES_PATH = CACHE_DIR / 'story_collector_preferences.json'
CONFIG_PATH = PROJECT_ROOT / '配置' / 'story_collector_config.json'

SUBMIT_API = 'https://suying-link.pages.dev/api/submit'
SUBMIT_SECRET = 'wang5201314@'

DEFAULT_CONFIG = {
    'target_count': 10,
    'min_duration_seconds': 60,
    'max_duration_seconds': 300,
    'min_likes': 0,
    'min_score': 10,
    'search_keywords': [
    '婆媳故事',
    '家庭伦理故事',
    '情感故事',
    '老人赡养',
    '儿媳婆婆',
    '父爱故事',
    '母亲故事',
    '后妈故事',
    '房子养老',
    '彩礼婚姻',
    '社会现象故事',
    ],
    'prefer_words': [
    '儿媳', '婆婆', '母亲', '父亲', '妻子', '丈夫', '媳妇', '老人',
    '后妈', '继子', '房子', '彩礼', '养老', '报恩', '复仇', '真相',
    '冤枉', '离家', '车票', '一碗', '十三年', '家庭', '亲情',
    ],
    'block_words': [
    '直播', '带货', '商品', '同款', '教程', '剪辑', '影视', '电影',
    '电视剧', '短剧', '音乐', '舞蹈', '游戏',
    ],
}

DEFAULT_PREFERENCES = {
    'liked_words': {},
    'skipped_words': {},
    'keyword_stats': {},
}


@dataclass
class Candidate:
    title: str
    url: str
    keyword: str
    score: int
    likes: int = 0
    duration_seconds: int = 0
    reason: str = ''


def ensure_dirs():
    CACHE_DIR.mkdir(exist_ok=True)
    COLLECTOR_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path, data):
    ensure_dirs()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def load_config():
    config = DEFAULT_CONFIG.copy()
    file_config = load_json(CONFIG_PATH, {})
    if isinstance(file_config, dict):
        for key, value in file_config.items():
            if key in config:
                config[key] = value
    return config


def load_preferences():
    prefs = load_json(PREFERENCES_PATH, DEFAULT_PREFERENCES.copy())
    if not isinstance(prefs, dict):
        return DEFAULT_PREFERENCES.copy()
    for key, default in DEFAULT_PREFERENCES.items():
        if key not in prefs or not isinstance(prefs[key], dict):
            prefs[key] = default.copy()
    return prefs


def save_preferences(prefs):
    save_json(PREFERENCES_PATH, prefs)


def normalize_url(url):
    if not url:
        return ''
    if url.startswith('//'):
        url = 'https:' + url
    if url.startswith('/video/'):
        url = 'https://www.douyin.com' + url
    url = url.split('?')[0]
    return url


def parse_count(text):
    if not text:
        return 0
    text = str(text).strip()
    m = re.search(r'([\d.]+)\s*万', text)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r'([\d.]+)', text)
    if m:
        return int(float(m.group(1)))
    return 0


def parse_duration(text):
    if not text:
        return 0
    text = str(text)
    m = re.search(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', text)
    if not m:
        return 0
    parts = [int(x) if x else 0 for x in m.groups()]
    if m.group(3):
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return parts[0] * 60 + parts[1]


def score_candidate(title, likes=0, duration_seconds=0, config=None, prefs=None, keyword=''):
    config = config or DEFAULT_CONFIG
    prefs = prefs or DEFAULT_PREFERENCES
    score = 0
    reasons = []

    for word in config.get('block_words', []):
        if word and word in title:
            return -999, '屏蔽词:' + word

    min_likes = int(config.get('min_likes', 0) or 0)
    if min_likes and likes and likes < min_likes:
        return -999, f'点赞低于{min_likes}'

    for word in config.get('prefer_words', []):
        if word in title:
            score += 8
            reasons.append(word)

    min_duration = int(config.get('min_duration_seconds', 60) or 0)
    max_duration = int(config.get('max_duration_seconds', 300) or 0)
    if duration_seconds and min_duration <= duration_seconds <= max_duration:
        score += 25
        reasons.append('时长合适')
    elif duration_seconds:
        score -= 15
        reasons.append('时长不合适')

    if likes >= 10000:
        score += 30
        reasons.append('点赞1万+')
    elif likes >= 3000:
        score += 20
        reasons.append('点赞3000+')
    elif likes >= 1000:
        score += 12
        reasons.append('点赞1000+')
    elif likes > 0:
        score += 5
        reasons.append('有点赞')

    if len(title) >= 6:
        score += 5

    liked_words = prefs.get('liked_words', {})
    skipped_words = prefs.get('skipped_words', {})
    for word in extract_learning_words(title, config):
        like_count = int(liked_words.get(word, 0) or 0)
        skip_count = int(skipped_words.get(word, 0) or 0)
        delta = min(like_count * 3, 18) - min(skip_count, 8)
        if delta:
            score += delta
            reasons.append(f'偏好{word}:{delta:+d}')

    keyword_stats = prefs.get('keyword_stats', {}).get(keyword, {})
    selected = int(keyword_stats.get('selected', 0) or 0)
    shown = int(keyword_stats.get('shown', 0) or 0)
    if shown >= 3:
        rate_bonus = round((selected / shown) * 12)
        if rate_bonus:
            score += rate_bonus
            reasons.append(f'关键词偏好+{rate_bonus}')

    return score, '、'.join(reasons)


def load_history_urls():
    data = load_json(HISTORY_PATH, {'submitted': [], 'skipped': []})
    urls = set()
    for key in ('submitted', 'skipped'):
        for item in data.get(key, []):
            if isinstance(item, dict):
                urls.add(normalize_url(item.get('url', '')))
            elif isinstance(item, str):
                urls.add(normalize_url(item))
    return urls


def extract_learning_words(title, config):
    words = []
    for word in config.get('prefer_words', []):
        if word and word in title:
            words.append(word)
    if words:
        return words

    for word in re.findall(r'[\u4e00-\u9fff]{2,4}', title):
        if word not in config.get('block_words', []):
            words.append(word)
    return words[:8]


class DouyinCollector:
    def __init__(self, log):
        self.log = log
        self.playwright = None
        self.context = None
        self.page = None
        self.config = load_config()
        self.preferences = load_preferences()

    def start(self, headless=False):
        if sync_playwright is None:
            raise RuntimeError('未安装 playwright, 请先执行: pip install -r 配置/requirements.txt')
        ensure_dirs()
        if self.playwright is None:
            self.playwright = sync_playwright().start()
        if self.context is None:
            try:
                self.context = self.playwright.chromium.launch_persistent_context(
                    str(PROFILE_DIR),
                    headless=headless,
                    viewport={'width': 1280, 'height': 900},
                    args=['--disable-blink-features=AutomationControlled'],
                )
            except Exception as e:
                msg = str(e)
                if 'Executable doesn' in msg or 'playwright install' in msg:
                    raise RuntimeError(
                        'Playwright 浏览器内核未安装。请先在项目目录运行: '
                        'python -m playwright install chromium'
                    ) from e
                raise
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self.page

    def open_login(self):
        try:
            page = self.start(headless=False)
            page.goto('https://www.douyin.com/', wait_until='domcontentloaded', timeout=30000)
            self.log('已打开抖音, 请扫码或完成登录。登录状态会保存在本机缓存。')
        except Exception as e:
            self.log(f'打开抖音失败: {e}')

    def collect(self, target_count=10):
        self.config = load_config()
        self.preferences = load_preferences()
        page = self.start(headless=False)
        history_urls = load_history_urls()
        seen = set()
        results = []

        for keyword in self.config.get('search_keywords', []):
            if len(results) >= target_count:
                break
            self.log(f'搜索: {keyword}')
            url = 'https://www.douyin.com/search/' + quote(keyword) + '?type=video'
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(2500)

            for _ in range(3):
                self._extract_from_page(page, keyword, results, seen, history_urls)
                if len(results) >= target_count:
                    break
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(1200)

        results.sort(key=lambda c: c.score, reverse=True)
        results = results[:target_count]
        save_json(CANDIDATES_PATH, [asdict(c) for c in results])
        self.log(f'采集完成: {len(results)} 条候选')
        return results

    def _extract_from_page(self, page, keyword, results, seen, history_urls):
        items = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href*="/video/"]'))
                .map(a => {
                    const text = (a.innerText || a.getAttribute('aria-label') || a.title || '').trim();
                    const img = a.querySelector('img');
                    const alt = img ? (img.alt || '') : '';
                    return { href: a.href, text, alt };
                })
                .filter(x => x.href && (x.text || x.alt))
                .slice(0, 60)
            """
        )

        for item in items:
            url = normalize_url(item.get('href', ''))
            if not url or url in seen or url in history_urls:
                continue
            raw_title = item.get('text') or item.get('alt') or ''
            title = clean_title(raw_title)
            if not title or len(title) < 4:
                continue

            likes = parse_count(raw_title)
            duration = parse_duration(raw_title)
            score, reason = score_candidate(
                title, likes, duration, self.config, self.preferences, keyword
            )
            if score < int(self.config.get('min_score', 10) or 10):
                continue

            seen.add(url)
            results.append(Candidate(
                title=title,
                url=url,
                keyword=keyword,
                score=score,
                likes=likes,
                duration_seconds=duration,
                reason=reason,
            ))

    def close(self):
        try:
            if self.context:
                self.context.close()
        finally:
            self.context = None
            self.page = None
        try:
            if self.playwright:
                self.playwright.stop()
        finally:
            self.playwright = None


def clean_title(text):
    text = re.sub(r'\s+', ' ', text or '').strip()
    text = re.sub(r'小牛说故事[:：]?', '', text).strip()
    text = re.sub(r'#\S+', '', text).strip()
    text = re.sub(r'\d{1,2}:\d{2}(:\d{2})?', '', text).strip()
    text = re.sub(r'\b\d+(\.\d+)?万?\b', '', text).strip()
    text = re.sub(r'[\s:：]+$', '', text).strip()
    return text[:80]


def submit_link(link):
    resp = requests.post(
        SUBMIT_API,
        json={'link': link, 'secret': SUBMIT_SECRET},
        timeout=30,
    )
    try:
        data = resp.json()
    except Exception:
        data = {'ok': False, 'error': resp.text[:120]}
    if resp.status_code != 200 or not data.get('ok'):
        raise RuntimeError(data.get('error') or f'HTTP {resp.status_code}')
    return data


def learn_from_review(candidates, selected_indexes, config):
    prefs = load_preferences()
    liked_words = prefs.setdefault('liked_words', {})
    skipped_words = prefs.setdefault('skipped_words', {})
    keyword_stats = prefs.setdefault('keyword_stats', {})
    selected_indexes = set(selected_indexes)

    for idx, candidate in enumerate(candidates):
        selected = idx in selected_indexes
        stats = keyword_stats.setdefault(candidate.keyword, {'shown': 0, 'selected': 0})
        stats['shown'] = int(stats.get('shown', 0) or 0) + 1
        if selected:
            stats['selected'] = int(stats.get('selected', 0) or 0) + 1

        words = extract_learning_words(candidate.title, config)
        target = liked_words if selected else skipped_words
        for word in words:
            target[word] = int(target.get(word, 0) or 0) + (2 if selected else 1)

    save_preferences(prefs)


class StoryCollectorApp:
    def __init__(self, root):
        self.root = root
        self.root.title('速影选题采集器')
        self.root.geometry('1080x720')

        self.ui_queue = queue.Queue()
        self.browser_queue = queue.Queue()
        self.collector = DouyinCollector(self.log)
        self.config = load_config()
        self.candidates = []
        self.check_vars = {}
        self.browser_thread = threading.Thread(target=self.browser_worker, daemon=True)
        self.browser_thread.start()

        self.build_ui()
        self.load_saved_candidates()
        self.root.after(200, self.process_queue)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)

    def build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill='x')

        ttk.Button(top, text='打开抖音登录', command=self.open_login).pack(side='left')
        ttk.Label(top, text='采集数量').pack(side='left', padx=(16, 4))
        self.count_var = tk.IntVar(value=int(self.config.get('target_count', 10) or 10))
        ttk.Spinbox(top, from_=1, to=30, textvariable=self.count_var, width=6).pack(side='left')
        ttk.Button(top, text='开始采集', command=self.collect).pack(side='left', padx=8)
        ttk.Button(top, text='打开规则配置', command=self.open_config).pack(side='left')
        ttk.Button(top, text='重置学习偏好', command=self.reset_preferences).pack(side='left', padx=4)
        ttk.Button(top, text='全选', command=lambda: self.set_all(True)).pack(side='left', padx=(16, 4))
        ttk.Button(top, text='取消全选', command=lambda: self.set_all(False)).pack(side='left')
        ttk.Button(top, text='提交选中链接', command=self.submit_selected).pack(side='right')

        columns = ('选中', '分数', '点赞', '时长', '来源词', '标题', '原因', '链接')
        self.tree = ttk.Treeview(self.root, columns=columns, show='headings', height=22)
        widths = [50, 60, 80, 70, 100, 260, 180, 300]
        for col, width in zip(columns, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor='w')
        self.tree.pack(fill='both', expand=True, padx=10, pady=(0, 8))
        self.tree.bind('<Double-1>', self.toggle_selected)

        log_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        log_frame.pack(fill='x')
        self.log_text = tk.Text(log_frame, height=8, wrap='word')
        self.log_text.pack(fill='x')

    def log(self, text):
        self.ui_queue.put(('log', str(text)))

    def browser_worker(self):
        while True:
            task = self.browser_queue.get()
            if task is None:
                break
            name, payload = task
            try:
                if name == 'open_login':
                    self.collector.open_login()
                    self.log('采集期间请保持弹出的抖音浏览器打开。')
                elif name == 'collect':
                    candidates = self.collector.collect(payload)
                    self.ui_queue.put(('candidates', candidates))
            except Exception as e:
                if name == 'collect':
                    self.log(f'采集失败: {e}')
                else:
                    self.log(f'打开抖音失败: {e}')

    def process_queue(self):
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == 'log':
                self.log_text.insert('end', payload + '\n')
                self.log_text.see('end')
            elif kind == 'candidates':
                self.candidates = payload
                self.refresh_tree()
        self.root.after(200, self.process_queue)

    def load_saved_candidates(self):
        data = load_json(CANDIDATES_PATH, [])
        self.candidates = [Candidate(**item) for item in data if isinstance(item, dict)]
        self.refresh_tree()

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.check_vars = {}
        for idx, c in enumerate(self.candidates):
            checked = idx < 5
            self.check_vars[idx] = checked
            self.tree.insert('', 'end', iid=str(idx), values=(
                '√' if checked else '',
                c.score,
                c.likes or '',
                format_duration(c.duration_seconds),
                c.keyword,
                c.title,
                c.reason,
                c.url,
            ))

    def toggle_selected(self, event=None):
        item = self.tree.focus()
        if not item:
            return
        idx = int(item)
        self.check_vars[idx] = not self.check_vars.get(idx, False)
        values = list(self.tree.item(item, 'values'))
        values[0] = '√' if self.check_vars[idx] else ''
        self.tree.item(item, values=values)

    def set_all(self, checked):
        for idx in range(len(self.candidates)):
            self.check_vars[idx] = checked
            values = list(self.tree.item(str(idx), 'values'))
            values[0] = '√' if checked else ''
            self.tree.item(str(idx), values=values)

    def open_login(self):
        self.browser_queue.put(('open_login', None))

    def collect(self):
        self.config = load_config()
        target_count = int(self.count_var.get() or 10)
        self.browser_queue.put(('collect', target_count))

    def open_config(self):
        ensure_dirs()
        if not CONFIG_PATH.exists():
            save_json(CONFIG_PATH, DEFAULT_CONFIG)
        os.startfile(str(CONFIG_PATH))

    def reset_preferences(self):
        if not messagebox.askyesno('确认重置', '确定清空本地学习偏好吗?'):
            return
        save_preferences(DEFAULT_PREFERENCES.copy())
        self.log('已重置学习偏好。')

    def submit_selected(self):
        selected = [c for i, c in enumerate(self.candidates) if self.check_vars.get(i, False)]
        if not selected:
            messagebox.showwarning('没有选择', '请先勾选要提交的视频。')
            return
        if not messagebox.askyesno('确认提交', f'将提交 {len(selected)} 条链接到云端并触发生成/发布, 是否继续?'):
            return
        selected_indexes = [i for i, c in enumerate(self.candidates) if self.check_vars.get(i, False)]
        learn_from_review(self.candidates, selected_indexes, load_config())
        self.log('已学习本次筛选偏好, 下次采集会加权排序。')

        def worker():
            history = load_json(HISTORY_PATH, {'submitted': [], 'skipped': []})
            submitted = history.setdefault('submitted', [])
            for c in selected:
                try:
                    self.log(f'提交: {c.title}')
                    submit_link(c.url)
                    submitted.append({**asdict(c), 'submitted_at': int(time.time())})
                    save_json(HISTORY_PATH, history)
                    self.log('  成功')
                    time.sleep(3)
                except Exception as e:
                    self.log(f'  失败: {e}')
            self.log('提交完成。')

        threading.Thread(target=worker, daemon=True).start()

    def on_close(self):
        try:
            self.browser_queue.put(None)
        except Exception:
            pass
        try:
            self.collector.close()
        except Exception:
            pass
        self.root.destroy()


def format_duration(seconds):
    if not seconds:
        return ''
    return f'{seconds // 60}:{seconds % 60:02d}'


def main():
    ensure_dirs()
    root = tk.Tk()
    app = StoryCollectorApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
