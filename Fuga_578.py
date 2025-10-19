import streamlit as st
import json
import re
from datetime import datetime, timedelta
from collections import Counter
import pandas as pd
import random
import sqlite3
from pathlib import Path



# 简化版分词和词性标注（不依赖spaCy）
def simple_tokenize(text):
    """简单分词：提取所有德语单词，保留大小写"""
    tokens = re.findall(r'\b[a-zA-ZäöüßÄÖÜ]+\b', text)
    return tokens

def get_concordance(word, text, window=5):
    """提取词汇的上下文"""
    tokens = simple_tokenize(text)
    concordances = []
    for i, token in enumerate(tokens):
        if token == word:  # 精确匹配，保留大小写
            start = max(0, i - window)
            end = min(len(tokens), i + window + 1)
            context = ' '.join(tokens[start:end])
            concordances.append(context)
    return concordances[:3]  # 最多返回3个例句

def extract_readable_text(text, target_tokens, max_length=None):
    """
    提取包含目标token的最大连续文本片段
    
    Args:
        text: 原始文本
        target_tokens: 目标词汇集合（生词+熟词），保留大小写
        max_length: 最大返回长度（字符数），None表示不限制
    
    Returns:
        包含最多目标词汇的文本片段
    """
    sentences = re.split(r'[.!?]+', text)
    
    best_segment = ""
    max_target_count = 0
    
    # 尝试不同长度的句子组合
    for start_idx in range(len(sentences)):
        current_segment = ""
        current_target_count = 0
        
        for end_idx in range(start_idx, len(sentences)):
            current_segment += sentences[end_idx] + ". "
            
            # 如果超过最大长度限制，停止
            if max_length and len(current_segment) > max_length:
                break
            
            # 统计当前片段中的目标词汇数量（保留大小写）
            tokens = simple_tokenize(current_segment)
            target_count = sum(1 for token in tokens if token in target_tokens)
            
            if target_count > current_target_count:
                current_target_count = target_count
            
            # 更新最佳片段
            if current_target_count > max_target_count:
                max_target_count = current_target_count
                best_segment = current_segment
    
    return best_segment.strip(), max_target_count

# ============= 添加缓存管理类 (在 VocabManager 类之前) =============
class TextCacheManager:
    """管理文本缓存"""
    
    def __init__(self, cache_file='text_cache.txt'):
        self.cache_file = cache_file
    
    def save_text(self, text):
        """保存文本到缓存"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                f.write(text)
            return True
        except Exception as e:
            st.error(f"保存缓存失败: {e}")
            return False
    
    def load_text(self):
        """从缓存加载文本"""
        try:
            if Path(self.cache_file).exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return f.read()
            return None
        except Exception as e:
            st.error(f"加载缓存失败: {e}")
            return None
    
    def clear_cache(self):
        """清除缓存"""
        try:
            if Path(self.cache_file).exists():
                Path(self.cache_file).unlink()
            return True
        except Exception as e:
            st.error(f"清除缓存失败: {e}")
            return False
    
    def has_cache(self):
        """检查是否有缓存"""
        return Path(self.cache_file).exists()



# 数据管理类
class VocabManager:
    def __init__(self, vocab_file='vocab.json'):
        self.vocab_file = vocab_file
        self.vocab = self.load_vocab()
    
    def load_vocab(self):
        """加载词汇表"""
        try:
            with open(self.vocab_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def save_vocab(self):
        """保存词汇表"""
        with open(self.vocab_file, 'w', encoding='utf-8') as f:
            json.dump(self.vocab, f, ensure_ascii=False, indent=2)
    
    def add_word(self, word, text=""):
        """添加生词（修复版：避免覆盖已有数据）"""
        if word not in self.vocab:
            self.vocab[word] = {
                "word": word,
                "added_date": datetime.now().isoformat(),
                "last_review": datetime.now().isoformat(),
                "status": "learning",
                "encounter_count": 0,
                "concordance": get_concordance(word, text) if text else []
            }
        else:
            # 词汇已存在（可能是从熟词降级）
            # 只更新concordance和status，不覆盖其他字段
            if text and not self.vocab[word].get("concordance"):
                self.vocab[word]["concordance"] = get_concordance(word, text)
            if self.vocab[word]["status"] == "familiar":
                # 如果是从熟词降级，重置为学习状态
                self.vocab[word]["status"] = "learning"
                self.vocab[word]["added_date"] = datetime.now().isoformat()
        
        return self.vocab[word]
    
    def mark_as_familiar(self, word):
        """标记为熟词"""
        if word in self.vocab:
            self.vocab[word]["status"] = "familiar"
            self.vocab[word]["last_review"] = datetime.now().isoformat()
            self.save_vocab()
    
    def mark_as_learning(self, word):
        """降级到生词（修复版：保留历史信息）"""
        if word in self.vocab:
            # 保留原有信息，只修改状态和时间戳
            self.vocab[word]["status"] = "learning"
            self.vocab[word]["last_review"] = datetime.now().isoformat()
            # 重新设置添加日期为今天（开启新的3天倒计时）
            self.vocab[word]["added_date"] = datetime.now().isoformat()
            self.save_vocab()
    
    def increment_encounter(self, word):
        """增加遇到次数"""
        if word in self.vocab:
            self.vocab[word]["encounter_count"] += 1
            self.save_vocab()
    
    def clean_expired_words(self, days=3):
        """删除超过3天未复习的生词"""
        cutoff = datetime.now() - timedelta(days=days)
        expired_words = []
        
        for word, meta in list(self.vocab.items()):
            if meta["status"] == "learning":
                added_date = datetime.fromisoformat(meta["added_date"])
                if added_date < cutoff:
                    expired_words.append(word)
                    del self.vocab[word]
        
        if expired_words:
            self.save_vocab()
        return expired_words
    
    def get_learning_words(self):
        """获取生词表"""
        return {k: v for k, v in self.vocab.items() if v["status"] == "learning"}
    
    def get_familiar_words(self):
        """获取熟词表"""
        return {k: v for k, v in self.vocab.items() if v["status"] == "familiar"}
# ============= 新增数据管理类 =============
class ReadingManager:
    """管理书籍和阅读会话数据"""
    
    def __init__(self, db_file='reading_stats.db'):
        self.db_file = db_file
        self.init_database()
    
    def init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 书籍表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT UNIQUE NOT NULL,
                author TEXT,
                added_date TEXT NOT NULL,
                reading_status TEXT DEFAULT 'reading'
            )
        ''')
        
        # 阅读会话表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reading_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                session_date TEXT NOT NULL,
                total_tokens INTEGER,
                new_words INTEGER,
                familiar_words_count INTEGER,
                duration_minutes INTEGER,
                notes TEXT,
                FOREIGN KEY(book_id) REFERENCES books(id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_book(self, title, author=""):
        """添加新书籍"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO books (title, author, added_date) VALUES (?, ?, ?)',
                (title, author, datetime.now().isoformat())
            )
            conn.commit()
            book_id = cursor.lastrowid
            conn.close()
            return book_id
        except sqlite3.IntegrityError:
            return None  # 书籍已存在
    
    def get_all_books(self):
        """获取所有书籍"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT id, title, author, added_date, reading_status FROM books ORDER BY added_date DESC')
        books = cursor.fetchall()
        conn.close()
        return books
    
    def add_session(self, book_id, total_tokens, new_words, familiar_words_count, duration_minutes=0, notes=""):
        """添加阅读会话"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO reading_sessions 
               (book_id, session_date, total_tokens, new_words, familiar_words_count, duration_minutes, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (book_id, datetime.now().isoformat(), total_tokens, new_words, familiar_words_count, duration_minutes, notes)
        )
        conn.commit()
        conn.close()
    
    def get_book_stats(self, book_id):
        """获取单本书的统计数据"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 获取书籍信息
        cursor.execute('SELECT title, author, added_date FROM books WHERE id = ?', (book_id,))
        book_info = cursor.fetchone()
        
        # 获取所有会话
        cursor.execute(
            'SELECT session_date, total_tokens, new_words, familiar_words_count, duration_minutes, notes FROM reading_sessions WHERE book_id = ? ORDER BY session_date',
            (book_id,)
        )
        sessions = cursor.fetchall()
        conn.close()
        
        if not book_info:
            return None
        
        # 计算统计数据
        total_tokens = sum(s[1] for s in sessions)
        total_new_words = sum(s[2] for s in sessions)
        total_familiar_count = sum(s[3] for s in sessions)
        session_count = len(sessions)
        
        return {
            'title': book_info[0],
            'author': book_info[1],
            'added_date': book_info[2],
            'sessions': sessions,
            'total_tokens': total_tokens,
            'total_new_words': total_new_words,
            'total_familiar_count': total_familiar_count,
            'session_count': session_count,
            'avg_new_words_per_session': total_new_words / session_count if session_count > 0 else 0,
            'new_words_ratio': (total_new_words / total_tokens * 100) if total_tokens > 0 else 0
        }
    
    def get_all_stats(self):
        """获取所有书籍的统计摘要"""
        books = self.get_all_books()
        stats_list = []
        
        for book_id, title, author, added_date, status in books:
            stats = self.get_book_stats(book_id)
            if stats:
                stats['book_id'] = book_id
                stats['status'] = status
                stats_list.append(stats)
        
        return stats_list
    
    def update_book_status(self, book_id, status):
        """更新书籍阅读状态"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('UPDATE books SET reading_status = ? WHERE id = ?', (status, book_id))
        conn.commit()
        conn.close()

# 初始化
if 'vocab_manager' not in st.session_state:
    st.session_state.vocab_manager = VocabManager()
if 'current_text' not in st.session_state:
    st.session_state.current_text = ""
if 'readable_text' not in st.session_state:
    st.session_state.readable_text = ""
if 'new_tokens' not in st.session_state:
    st.session_state.new_tokens = []

vm = st.session_state.vocab_manager

if 'reading_manager' not in st.session_state:
    st.session_state.reading_manager = ReadingManager()
if 'current_book_id' not in st.session_state:
    st.session_state.current_book_id = None

rm = st.session_state.reading_manager

if 'text_cache_manager' not in st.session_state:
    st.session_state.text_cache_manager = TextCacheManager()

tcm = st.session_state.text_cache_manager

# Streamlit 界面
st.title("- Little Fuga 578-zehn ")
st.set_page_config(
    page_title="Fuga Vocabs",
    page_icon="💿",
    layout="wide"
)

# 侧边栏：统计信息
with st.sidebar:
    st.header("📊 词汇统计")
    learning_words = vm.get_learning_words()
    familiar_words = vm.get_familiar_words()
    
    st.metric("生词数量", len(learning_words))
    st.metric("熟词数量", len(familiar_words))
    st.metric("总词汇量", len(vm.vocab))
    
    st.divider()
    
    # 自动清理过期生词
    if st.button("🗑️ 清理过期生词（>3天）"):
        expired = vm.clean_expired_words(days=3)
        if expired:
            st.success(f"已删除 {len(expired)} 个过期生词")
            st.write(expired)
        else:
            st.info("没有过期生词")

# 主页面 - 标签页
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📥 文本载入", 
    "📖 阅读模式", 
    "📚 生词学习", 
    "🎴 Anki复习", 
    "🧪 每周抽检",
    "📊 阅读统计"
])

# Tab 1: 文本载入
with tab1:
    st.header("📥 文本载入")
    
    # ============= 添加书籍选择 =============
    # 【新增】书籍管理区域
    st.subheader("📖 选择或添加书籍")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # 获取所有书籍
        books = rm.get_all_books()
        book_options = {f"{b[1]} - {b[2]}" if b[2] else b[1]: b[0] for b in books}
        book_options["+ 新增书籍"] = None
        
        selected_book = st.selectbox(
            "选择书籍：",
            options=list(book_options.keys()),
            key="book_selector"
        )
        
        if selected_book == "+ 新增书籍":
            st.session_state.current_book_id = None
        else:
            st.session_state.current_book_id = book_options[selected_book]
    
    with col2:
        if st.button("➕ 新增书籍", key="add_book_btn"):
            st.session_state.show_add_book_form = True
    
    # 新增书籍表单
    if st.session_state.get('show_add_book_form'):
        st.divider()
        with st.form("add_book_form"):
            new_title = st.text_input("书籍标题 *", placeholder="例如：Der Steppenwolf")
            new_author = st.text_input("作者（可选）", placeholder="例如：Hermann Hesse")
            
            if st.form_submit_button("✅ 添加书籍"):
                if new_title.strip():
                    book_id = rm.add_book(new_title.strip(), new_author.strip())
                    if book_id:
                        st.session_state.current_book_id = book_id
                        st.session_state.show_add_book_form = False
                        st.success(f"✅ 书籍 '{new_title}' 添加成功！")
                        st.rerun()
                    else:
                        st.error("❌ 该书籍已存在")
                else:
                    st.error("请输入书籍标题")
    
    st.divider()
    
    # 显示当前选中的书籍
    if st.session_state.current_book_id:
        books_dict = {b[0]: b[1] for b in rm.get_all_books()}
        current_title = books_dict.get(st.session_state.current_book_id)
        st.info(f"📖 当前选中：**{current_title}**")
    
    
        
    st.subheader("📝 文本输入区域")

    # 缓存状态和操作按钮
    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        if tcm.has_cache():
            st.success("✅ 已保存上一次的文本缓存")
        else:
            st.info("💡 还未保存文本缓存")

    with col2:
        if tcm.has_cache():
            if st.button("⏮️ 恢复上次文本", key="restore_cache"):
                cached_text = tcm.load_text()
                if cached_text:
                    st.session_state.text_input_value = cached_text
                    st.rerun()

    with col3:
        if tcm.has_cache():
            if st.button("🗑️ 清除缓存", key="clear_cache"):
                if tcm.clear_cache():
                    st.success("✅ 缓存已清除")
                    st.rerun()

    st.divider()

    # 文本输入框
    text_input = st.text_area(
        "粘贴或输入德语文本：",
        value=st.session_state.get('text_input_value', ''),
        height=300,
        placeholder="例如：Ich gehe heute in die Schule. Das Wetter ist sehr schön..."
    )


    buffer_size = st.number_input("每次加载生词数量：", min_value=10, max_value=200, value=50)
   
    # 在开头添加保存缓存的逻辑：
    if st.button("🔄 处理文本", type="primary"):
        if text_input:
            # 保存文本到缓存
            tcm.save_text(text_input)
            st.session_state.text_input_value = text_input
            expired = vm.clean_expired_words(days=3)

            # 检查是否选了书籍
            if not st.session_state.current_book_id:
                st.error("❌ 请先选择或创建一本书籍")
            else:
                # 【原有逻辑】
                expired = vm.clean_expired_words(days=3)
                if expired:
                    st.warning(f"⚠️ 已自动删除 {len(expired)} 个超过3天未复习的生词")
                
                tokens = simple_tokenize(text_input)
                st.session_state.current_text = text_input
                
                token_counts = Counter(tokens)
                familiar_set = set(vm.get_familiar_words().keys())
                learning_set = set(vm.get_learning_words().keys())
                
                new_words = []
                for word, count in token_counts.most_common():
                    if word not in familiar_set and word not in learning_set:
                        new_words.append(word)
                        if len(new_words) >= buffer_size:
                            break
                
                for word in familiar_set:
                    if word in token_counts:
                        vm.increment_encounter(word)
                
                for word in new_words:
                    vm.add_word(word, text_input)
                
                st.session_state.new_tokens = new_words
                vm.save_vocab()
                
                target_tokens = set(vm.get_learning_words().keys()) | set(vm.get_familiar_words().keys())
                readable_text, target_count = extract_readable_text(text_input, target_tokens)
                st.session_state.readable_text = readable_text
                
                # 【新增】记录到数据库
                rm.add_session(
                    book_id=st.session_state.current_book_id,
                    total_tokens=len(tokens),
                    new_words=len(new_words),
                    familiar_words_count=target_count
                )
                
                st.success(f"✅ 处理完成！发现 {len(new_words)} 个新词")
                st.info(f"📖 生成阅读文本，包含 {target_count} 个已学词汇")
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("总词数", len(tokens))
                with col2:
                    st.metric("唯一词数", len(set(tokens)))
                with col3:
                    st.metric("新词数", len(new_words))
                with col4:
                    st.metric("可读词数", target_count)
        else:
            st.error("请先输入文本！")

# Tab 2: 阅读模式
with tab2:
    st.header("📖 阅读模式")
    
    if not st.session_state.readable_text:
        st.info("💡 请先在「文本载入」页面处理文本")
    else:
        # 获取目标词汇
        learning_set = set(vm.get_learning_words().keys())
        familiar_set = set(vm.get_familiar_words().keys())
        all_known = learning_set | familiar_set
        
        st.markdown("### 📄 优化后的阅读文本")
        st.markdown("---")
        
        # 显示统计
        tokens_in_text = simple_tokenize(st.session_state.readable_text)
        known_count = sum(1 for t in tokens_in_text if t in all_known)
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("文本词数", len(tokens_in_text))
        with col2:
            st.metric("已学词数", known_count)
        with col3:
            coverage = (known_count / len(tokens_in_text) * 100) if tokens_in_text else 0
            st.metric("覆盖率", f"{coverage:.1f}%")
        
        st.divider()
        
        # 高亮显示文本
        display_text = st.session_state.readable_text
        words_in_text = re.findall(r'\b[a-zA-ZäöüßÄÖÜ]+\b', display_text)
        
        for word in set(words_in_text):
            if word in learning_set:
                # 生词用红色高亮（保留大小写）
                display_text = re.sub(
                    rf'\b{re.escape(word)}\b',
                    f'<mark style="background-color: #ffcccb;">{word}</mark>',
                    display_text
                )
            elif word in familiar_set:
                # 熟词用绿色高亮（保留大小写）
                display_text = re.sub(
                    rf'\b{re.escape(word)}\b',
                    f'<mark style="background-color: #90EE90;">{word}</mark>',
                    display_text
                )
        
        st.markdown(display_text, unsafe_allow_html=True)
        
        st.divider()
        
        # 图例
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("🟥 <mark style='background-color: #ffcccb;'>生词（需要学习）</mark>", unsafe_allow_html=True)
        with col2:
            st.markdown("🟩 <mark style='background-color: #90EE90;'>熟词（已掌握）</mark>", unsafe_allow_html=True)
        
        # 导出选项
        st.divider()
        st.download_button(
            label="💾 下载阅读文本",
            data=st.session_state.readable_text,
            file_name=f"reading_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            mime="text/plain"
        )

# Tab 3: 生词学习
with tab3:
    st.header("📚 生词学习")
    
    learning_words = vm.get_learning_words()
    
    if not learning_words:
        st.info("🎉 当前没有生词！去「文本载入」页面加载新文本吧。")
    else:
        st.write(f"**当前生词数量：{len(learning_words)}**")
        
        # 导出生词表（用于获取翻译）
        st.divider()
        st.subheader("📤 步骤1：导出生词表")
        
        # 🆕 自动生成带提示词的内容
        llm_prompt = "请给出下列德文单词的翻译，格式为 Deutsch, 中文翻译 \n 每個單詞一行\n"
        word_list_text = "\n".join(learning_words.keys())
        full_text_with_prompt = llm_prompt + word_list_text
        
        col1, col2 = st.columns([3, 1])
        with col1:
            # 显示带提示词的完整文本
            st.text_area(
                "单词列表（复制后粘贴给AI翻译）：", 
                full_text_with_prompt, 
                height=200,
                disabled=False  # 不要設置只讀，因為這樣用戶連復製都不行
            )
        with col2:
            # 下载选项：同时下载提示词和单词列表
            st.download_button(
                label="💾 下载 TXT（含提示词）",
                data=full_text_with_prompt,
                file_name=f"words_prompt_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain"
            )
            
            st.download_button(
                label="💾 下载单词表",
                data=word_list_text,
                file_name=f"words_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain"
            )
        
        st.info("💡 点击「复制」按钮（在文本框右上角），或下载后粘贴给 Claude/ChatGPT")
        
        # 导入CSV释义
        st.divider()
        st.subheader("📥 步骤2：导入参考释义")
        
        st.markdown("""
        **粘贴AI生成的CSV表格**（格式：`德语单词,中文释义`）
        ```
        beispiel,例子
        schön,美丽的
        lernen,学习
        ```
        """)
        
        csv_input = st.text_area(
            "粘贴CSV内容：",
            height=200,
            placeholder="beispiel,例子\nschön,美丽的\nlernen,学习",
            key="csv_import"
        )
        
        col1, col2, col3 = st.columns([1, 1, 2])
        
        with col1:
            if st.button("📝 导入释义", type="primary"):
                if csv_input.strip():
                    imported_count = 0
                    skipped_count = 0
                    
                    for line in csv_input.strip().split('\n'):
                        line = line.strip()
                        if not line or ',' not in line:
                            continue
                        
                        parts = line.split(',', 1)
                        if len(parts) != 2:
                            continue
                        
                        word = parts[0].strip()  # 保留大小写
                        translation = parts[1].strip()
                        
                        if word in vm.vocab:
                            vm.vocab[word]["translation"] = translation
                            imported_count += 1
                        else:
                            skipped_count += 1
                    
                    vm.save_vocab()
                    st.success(f"✅ 成功导入 {imported_count} 个释义")
                    if skipped_count > 0:
                        st.warning(f"⚠️ 跳过 {skipped_count} 个不在词汇表中的单词")
                    st.rerun()
                else:
                    st.error("请先粘贴CSV内容")
        
        with col2:
            if st.button("🗑️ 清除所有释义"):
                for word in vm.vocab:
                    if "translation" in vm.vocab[word]:
                        del vm.vocab[word]["translation"]
                vm.save_vocab()
                st.success("已清除所有释义")
                st.rerun()
        
        # 显示生词列表（带释义）
        st.divider()
        st.subheader("📋 步骤3：生词一览表")
        
        df_data = []
        for word, meta in learning_words.items():
            added_date = datetime.fromisoformat(meta["added_date"])
            days_left = 3 - (datetime.now() - added_date).days
            
            df_data.append({
                "单词": word,
                "参考释义": meta.get("translation", "❌ 未导入"),
                "添加日期": added_date.strftime("%Y-%m-%d"),
                "剩余天数": f"{days_left} 天",
                "例句数量": len(meta.get("concordance", []))
            })
        
        df = pd.DataFrame(df_data)
        st.dataframe(df, use_container_width=True)
        
        # 统计释义覆盖率
        with_translation = sum(1 for meta in learning_words.values() if meta.get("translation"))
        coverage = (with_translation / len(learning_words) * 100) if learning_words else 0
        st.metric("释义覆盖率", f"{coverage:.1f}% ({with_translation}/{len(learning_words)})")

# Tab 4: Anki 复习
with tab4:
    st.header("🎴 简易 Anki 复习")
    
    learning_words = vm.get_learning_words()
    
    if not learning_words:
        st.info("当前没有需要复习的生词。")
    else:
        st.write(f"**待复习生词：{len(learning_words)} 个**")
        
        # 初始化复习索引
        if 'review_index' not in st.session_state:
            st.session_state.review_index = 0
            st.session_state.review_words = list(learning_words.keys())
            random.shuffle(st.session_state.review_words)
        
        if st.session_state.review_index < len(st.session_state.review_words):
            current_word = st.session_state.review_words[st.session_state.review_index]
            word_meta = learning_words[current_word]
            
            # 显示当前单词
            st.markdown(f"### 单词 {st.session_state.review_index + 1}/{len(st.session_state.review_words)}")
            st.markdown(f"# **{current_word}**")
            
            # 显示参考释义（可折叠）
            if word_meta.get("translation"):
                with st.expander("💭 查看参考释义", expanded=False):
                    st.markdown(f"### {word_meta['translation']}")
            else:
                st.caption("💡 提示：可在「生词学习」页面导入释义")
            
            # 显示例句
            if word_meta.get("concordance"):
                with st.expander("📖 查看例句", expanded=False):
                    for i, context in enumerate(word_meta["concordance"], 1):
                        st.write(f"{i}. ...{context}...")
            
            st.divider()
            
            # 操作按钮
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("✅ 认识 → 熟词表", use_container_width=True, type="primary"):
                    vm.mark_as_familiar(current_word)
                    st.session_state.review_index += 1
                    st.rerun()
            
            with col2:
                if st.button("❌ 不认识 → 跳过", use_container_width=True):
                    st.session_state.review_index += 1
                    st.rerun()
            

            
            # 进度条
            progress = st.session_state.review_index / len(st.session_state.review_words)
            st.progress(progress)
        else:
            st.success("🎉 本轮复习完成！")
            if st.button("🔄 重新开始复习"):
                st.session_state.review_index = 0
                st.session_state.review_words = list(learning_words.keys())
                random.shuffle(st.session_state.review_words)
                st.rerun()

# Tab 5: 每周抽检
with tab5:
    st.header("🧪 熟词抽检")
    
    familiar_words = vm.get_familiar_words()
    
    if not familiar_words:
        st.info("当前没有熟词可以抽检。")
    else:
        st.write(f"**熟词总数：{len(familiar_words)} 个**")
        
        test_ratio = st.slider("抽检比例（%）：", 10, 100, 30)
        test_count = max(1, int(len(familiar_words) * test_ratio / 100))
        
        if st.button("🎲 开始随机抽检", type="primary"):
            st.session_state.test_words = random.sample(list(familiar_words.keys()), test_count)
            st.session_state.test_index = 0
        
        st.divider()
        
        # 抽检流程
        if 'test_words' in st.session_state and st.session_state.test_words:
            if st.session_state.test_index < len(st.session_state.test_words):
                current_word = st.session_state.test_words[st.session_state.test_index]
                word_meta = familiar_words[current_word]
                
                st.markdown(f"### 单词 {st.session_state.test_index + 1}/{len(st.session_state.test_words)}")
                st.markdown(f"# **{current_word}**")
                
                # 显示统计信息
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("遇到次数", word_meta.get("encounter_count", 0))
                with col2:
                    added = datetime.fromisoformat(word_meta["added_date"])
                    days_ago = (datetime.now() - added).days
                    st.metric("学习天数", f"{days_ago} 天")
                
                st.divider()
                
                col1, col2 = st.columns(2)
                
                with col1:
                    if st.button("✅ 认识", use_container_width=True, type="primary"):
                        st.session_state.test_index += 1
                        st.rerun()
                
                with col2:
                    if st.button("❌ 不认识 → 降级到生词", use_container_width=True):
                        vm.mark_as_learning(current_word)
                        st.session_state.test_index += 1
                        st.rerun()
                
                progress = st.session_state.test_index / len(st.session_state.test_words)
                st.progress(progress)
            else:
                st.success("🎉 抽检完成！")
                if st.button("🔄 重新抽检"):
                    del st.session_state.test_words
                    st.rerun()

# ============= 新增 Tab 6（阅读统计） =============
with tab6:
    st.header("📊 阅读统计")
    
    all_stats = rm.get_all_stats()
    
    if not all_stats:
        st.info("📚 还没有阅读记录。请先在「文本载入」页面加载文本。")
    else:
        # 总体统计卡片
        st.subheader("📈 总体统计")
        
        total_books = len(all_stats)
        total_tokens_all = sum(s['total_tokens'] for s in all_stats)
        total_new_words_all = sum(s['total_new_words'] for s in all_stats)
        total_sessions = sum(s['session_count'] for s in all_stats)
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("书籍总数", total_books)
        with col2:
            st.metric("总阅读词数", total_tokens_all)
        with col3:
            st.metric("总生词数", total_new_words_all)
        with col4:
            st.metric("总会话数", total_sessions)
        
        st.divider()
        
        # 按书籍详细统计
        st.subheader("📚 按书籍统计")
        
        # 准备表格数据
        df_stats = []
        for stat in all_stats:
            df_stats.append({
                "书籍": stat['title'],
                "作者": stat['author'] or "-",
                "阅读词数": stat['total_tokens'],
                "生词数": stat['total_new_words'],
                "熟词数": stat['total_familiar_count'],
                "会话数": stat['session_count'],
                "新词率": f"{stat['new_words_ratio']:.1f}%",
                "平均新词/会话": f"{stat['avg_new_words_per_session']:.1f}"
            })
        
        df = pd.DataFrame(df_stats)
        st.dataframe(df, use_container_width=True)
        
        st.divider()
        
        # 单本书详细信息
        st.subheader("🔍 单本书详情")
        
        book_titles = [s['title'] for s in all_stats]
        selected_stat = st.selectbox("选择书籍：", book_titles)
        
        selected_book_stat = next(s for s in all_stats if s['title'] == selected_stat)
        
        # 显示该书的详细数据
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("总阅读词数", selected_book_stat['total_tokens'])
        with col2:
            st.metric("总生词数", selected_book_stat['total_new_words'])
        with col3:
            st.metric("新词率", f"{selected_book_stat['new_words_ratio']:.1f}%")
        with col4:
            st.metric("阅读会话数", selected_book_stat['session_count'])
        
        st.divider()
        
        # 该书的所有会话记录
        st.markdown("### 📝 阅读会话记录")
        
        session_data = []
        for i, session in enumerate(selected_book_stat['sessions'], 1):
            session_date = datetime.fromisoformat(session[0])
            session_data.append({
                "序号": i,
                "日期": session_date.strftime("%Y-%m-%d %H:%M"),
                "词数": session[1],
                "新词": session[2],
                "熟词": session[3],
                "时长(分)": session[4] or "-",
                "笔记": session[5] or "-"
            })
        
        df_sessions = pd.DataFrame(session_data)
        st.dataframe(df_sessions, use_container_width=True)
        
        # 导出功能
        st.divider()
        st.markdown("### 💾 导出数据")
        
        export_df = pd.DataFrame(df_stats)
        csv_data = export_df.to_csv(index=False, encoding='utf-8-sig')
        
        st.download_button(
            label="📥 下载阅读统计 (CSV)",
            data=csv_data,
            file_name=f"reading_stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )


# 底部信息
st.divider()
st.caption("💡 提示：生词会在3天后自动删除，请及时复习！熟词可通过抽检降级回生词表。")