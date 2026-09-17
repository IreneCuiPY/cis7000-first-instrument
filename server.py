"""your-first-instrument — a sense of time for a model that has none.

Why time? Ask your Claude "how long have we been talking?" WITHOUT this
connected. It can only guess: no clock lives in a context window. This
server is the smallest honest fix — and the pattern generalizes to any
instrument you can imagine. See docs/adr/ for every choice made here.
"""
import functools
import os
import random
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "your-first-instrument",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Point it at your Postgres connection "
        "string before starting this server — e.g. a .env.local for local "
        "development (see `neon link`), or Render's environment settings "
        "in production."
    )

SEED_QUESTIONS = [
    ("sde", "Two Sum: 给一个整数数组和一个目标值,找出数组中两个数之和等于目标值的下标。", "用哈希表存已遍历过的数字,一次遍历完成。"),
    ("sde", "反转链表:就地反转一个单链表。", "用三个指针(prev, curr, next)迭代反转。"),
    ("sde", "有效括号:判断字符串中的括号是否正确配对闭合。", "用栈,遇到左括号入栈,遇到右括号检查栈顶是否匹配。"),
    ("sde", "最大子数组和:找一个整数数组中连续子数组的最大和。", "Kadane's Algorithm,维护当前子数组和与全局最大值。"),
    ("sde", "二叉树层序遍历:用BFS按层遍历一棵二叉树。", "用队列,每次处理完当前层所有节点再进入下一层。"),
    ("sde", "合并两个有序数组:把两个已排序数组合并成一个有序数组。", "双指针从头或从尾比较合并。"),
    ("sde", "判断链表是否有环:检测一个链表中是否存在环。", "快慢指针,若相遇则有环。"),
    ("quant", "抛硬币直到出现正面,期望要抛几次?", "设期望为E,E = 1 + 0.5*E,解得E=2。"),
    ("quant", "三门问题(Monty Hall):换门是否能提高中奖概率?", "换门中奖概率2/3,不换是1/3,主持人排除信息改变了条件概率。"),
    ("quant", "生日问题:一个房间最少多少人,让至少两人生日相同的概率超过50%?", "约23人,用补集计算所有人生日都不同的概率。"),
    ("quant", "掷一个六面骰子,期望点数是多少?", "E[X] = (1+2+3+4+5+6)/6 = 3.5。"),
    ("quant", "掷两个骰子,点数之和等于7的概率是多少?", "共36种组合,和为7有6种(1+6,2+5,3+4,4+3,5+2,6+1),概率1/6。"),
    ("quant", "已知E[X]=3, E[Y]=5,求E[X+Y]。", "期望的线性性,E[X+Y]=E[X]+E[Y]=8,与X、Y是否独立无关。"),
    ("quant", "12个球中有一个坏球(重量不同),用天平最少称几次能找出坏球并确定它更重还是更轻?", "3次,每次把球分成三组比较。"),
    ("quant", "某疾病发病率1%,检测准确率99%(真阳性率和真阴性率都是99%),某人检测阳性,他真正患病的概率是多少?", "用贝叶斯定理,答案约为50%,远低于直觉上的99%。"),
]

@contextmanager
def get_connection():
    """A single connection per call, always closed on the way out."""
    try:
        conn = psycopg.connect(DATABASE_URL)
    except psycopg.Error as e:
        raise ConnectionError(f"无法连接数据库: {e}") from e
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def db_errors(fn):
    """Turn connection/query failures into a returned error string
    instead of letting them crash the server."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ConnectionError as e:
            return str(e)
        except psycopg.Error as e:
            return f"数据库错误: {e}"
    return wrapper

def init_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS questions (
                    id SERIAL PRIMARY KEY,
                    category TEXT NOT NULL,
                    question TEXT NOT NULL,
                    hint TEXT,
                    answer TEXT,
                    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT now()
                )
            """)
            cur.execute("SELECT count(*) FROM questions")
            (count,) = cur.fetchone()
            if count == 0:
                cur.executemany(
                    "INSERT INTO questions (category, question, hint) VALUES (%s, %s, %s)",
                    SEED_QUESTIONS,
                )

init_db()

@mcp.tool()
def current_time() -> str:
    """The current date and time (UTC and local)."""
    now = datetime.now(timezone.utc)
    return f"UTC: {now.isoformat()} · local: {datetime.now().isoformat()}"

@mcp.tool()
def seconds_since(iso_timestamp: str) -> str:
    """Seconds elapsed since an ISO timestamp (e.g. '2026-09-10T17:15:00')."""
    then = datetime.fromisoformat(iso_timestamp)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - then
    return f"{delta.total_seconds():.0f} seconds ({delta})"

@mcp.tool()
def flip_coin() -> str:
    """Flip a coin. Returns '正面' (heads) or '反面' (tails)."""
    # weighted 70/30 toward heads — intentional, not a fair coin
    return "正面" if random.random() < 0.7 else "反面"

@mcp.tool()
@db_errors
def get_random_question(category: str = None) -> dict:
    """Get a random interview question. Optionally filter by category
    ('sde' or 'quant'); an unrecognized or omitted category draws from
    the full question bank. Returns id, category, and question text."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if category in ("sde", "quant"):
                cur.execute(
                    "SELECT id, category, question FROM questions "
                    "WHERE category = %s ORDER BY random() LIMIT 1",
                    (category,),
                )
            else:
                cur.execute(
                    "SELECT id, category, question FROM questions "
                    "ORDER BY random() LIMIT 1"
                )
            row = cur.fetchone()
    if row is None:
        return {"error": "题库为空。"}
    return {"id": row[0], "category": row[1], "question": row[2]}

@mcp.tool()
@db_errors
def get_hint(question_id: int) -> str:
    """Get the hint for a question by its id."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT hint FROM questions WHERE id = %s", (question_id,))
            row = cur.fetchone()
    if row is None:
        return f"未找到 id={question_id} 的题目。"
    return row[0] or "此题暂无提示。"

@mcp.tool()
@db_errors
def get_answer(question_id: int) -> str:
    """Get the full answer for a question by its id, if one exists."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT answer FROM questions WHERE id = %s", (question_id,))
            row = cur.fetchone()
    if row is None:
        return f"未找到 id={question_id} 的题目。"
    return row[0] or "此题暂无参考答案。"

@mcp.tool()
@db_errors
def mark_reviewed(question_id: int) -> str:
    """Mark a question as reviewed."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE questions SET reviewed = TRUE WHERE id = %s RETURNING id",
                (question_id,),
            )
            if cur.fetchone() is None:
                return f"未找到 id={question_id} 的题目。"
            cur.execute("SELECT count(*) FROM questions WHERE reviewed = TRUE")
            (reviewed_count,) = cur.fetchone()
    return f"题目{question_id}已标记为已复习,累计已复习{reviewed_count}题。"

@mcp.tool()
@db_errors
def add_question(category: str, question: str, hint: str, answer: str = None) -> str:
    """Add a new question to the question bank. category must be 'sde' or 'quant'."""
    if category not in ("sde", "quant"):
        return "category 必须是 'sde' 或 'quant'。"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO questions (category, question, hint, answer) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (category, question, hint, answer),
            )
            (new_id,) = cur.fetchone()
    return f"已添加题目,id={new_id}。"

@mcp.tool()
@db_errors
def get_quiz(n: int = 3) -> list:
    """Get up to n randomly selected questions that have already been
    reviewed, for a daily review quiz. Returns fewer than n if not enough
    reviewed questions exist yet."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, category, question FROM questions "
                "WHERE reviewed = TRUE ORDER BY random() LIMIT %s",
                (n,),
            )
            rows = cur.fetchall()
    return [{"id": r[0], "category": r[1], "question": r[2]} for r in rows]

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
