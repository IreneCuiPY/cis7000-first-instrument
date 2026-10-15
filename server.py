"""your-first-instrument — a sense of time for a model that has none.

Why time? Ask your Claude "how long have we been talking?" WITHOUT this
connected. It can only guess: no clock lives in a context window. This
server is the smallest honest fix — and the pattern generalizes to any
instrument you can imagine. See docs/adr/ for every choice made here.

Identity (retrofit, following your-protected-notes' guide, adapted for
Neon Auth's Better Auth provider instead of Stack):
  guest — no (or missing) Authorization header: you share the commons
          (questions with user_id IS NULL) — the original 15 seed
          questions, plus anything anyone adds as a guest.
  user  — a verified Neon Auth identity: you see the commons PLUS your
          own private questions; add_question is the only thing that
          tells the two apart (your new questions are yours alone).
"review progress" (question_reviews) is tracked per-identity, never on
the question row itself — otherwise one user reviewing a shared commons
question would mark it "reviewed" for every guest and every other user.
"""
import functools
import os
import random
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

import jwt as pyjwt
import psycopg
from jwt import PyJWKClient
from mcp.server.fastmcp import Context, FastMCP

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

# Neon Auth / Better Auth: unlike the Stack provider, there is no separate
# project id — issuer AND audience are both the origin (scheme + host) of
# the JWKS URL itself (confirmed against Neon's JWT-plugin docs). Signing
# algorithm is fixed to EdDSA (Ed25519) and cannot be changed on the
# managed service.
#
# NEON_AUTH_BASE_URL (the Auth -> Configuration URL) is the primary config:
# the JWKS URL is derived from it. NEON_AUTH_JWKS_URL is kept only as an
# optional fallback for when BASE_URL isn't set (e.g. a legacy Stack
# project that only has a JWKS URL to give).
NEON_AUTH_BASE_URL = os.environ.get("NEON_AUTH_BASE_URL", "").rstrip("/")
JWKS_URL = (
    f"{NEON_AUTH_BASE_URL}/.well-known/jwks.json" if NEON_AUTH_BASE_URL
    else os.environ.get("NEON_AUTH_JWKS_URL")
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
    instead of letting them crash the server. Auth errors (bad/garbled
    tokens, raised from current_identity as plain jwt exceptions) are
    NOT psycopg/ConnectionError, so they pass through uncaught — an
    invalid token must surface as a loud error, never a silent guest."""
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
                    created_at TIMESTAMP NOT NULL DEFAULT now(),
                    user_id TEXT   -- NULL = shared commons (migration 2026-10-15)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS question_reviews (
                    question_id  INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    user_id      TEXT,
                    reviewed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS question_reviews_unique
                    ON question_reviews (question_id, COALESCE(user_id, ''))
            """)
            cur.execute("SELECT count(*) FROM questions")
            (count,) = cur.fetchone()
            if count == 0:
                cur.executemany(
                    "INSERT INTO questions (category, question, hint) VALUES (%s, %s, %s)",
                    SEED_QUESTIONS,
                )

init_db()

# ---------------------------------------------------------------- identity
def current_identity(ctx: Context):
    """Resolve the caller: (user_id, email) or (None, None) for a guest.

    ABSENT token  -> guest (a mode, not an error).
    INVALID token -> raises. An invalid credential must never silently
                     degrade to guest — that would hide broken auth
                     (expired token, wrong JWKS URL, clock skew) as
                     working anonymity, misfiling writes into the
                     commons while the caller believes they're private.
    """
    request = getattr(ctx.request_context, "request", None)  # starlette Request under streamable-http
    auth = request.headers.get("authorization", "") if request is not None else ""
    if not auth.lower().startswith("bearer "):
        return None, None
    token = auth.split(None, 1)[1]
    if token == "guest-no-shelf":      # the declared-guest credential (auth.py GUEST_TOKEN)
        return None, None              # chosen commons — a mode, never an error

    if not JWKS_URL:
        raise RuntimeError(
            "No auth variables set — NEON_AUTH_BASE_URL (Managed Better Auth; "
            "see COURSE-STEPS step 2) or NEON_AUTH_JWKS_URL (fallback)."
        )

    # Better Auth / Neon Auth: no project id. issuer == audience == the
    # origin (scheme + host) of the JWKS URL itself.
    origin = urlparse(JWKS_URL)
    expected = f"{origin.scheme}://{origin.netloc}"

    signing_key = PyJWKClient(JWKS_URL).get_signing_key_from_jwt(token)
    claims = pyjwt.decode(
        token, signing_key.key, algorithms=["EdDSA"],
        issuer=expected, audience=expected,
    )
    user_id = claims["sub"]                       # the identity, keyed on sub — never email
    email = None
    with get_connection() as conn:                # display only, never the key
        with conn.cursor() as cur:
            cur.execute('SELECT email FROM neon_auth."user" WHERE id::text = %s', (user_id,))
            row = cur.fetchone()
            email = row[0] if row else None
    return user_id, email

@mcp.tool()
def user_status(ctx: Context) -> str:
    """Who does this server think you are? Always available, in every mode."""
    user_id, email = current_identity(ctx)
    if user_id is None:
        return "guest — 你能看到并添加公共题库(commons);复习进度算在 guest commons 名下,人人共享。登录后可拥有自己的私有题。"
    return f"user — 已登录为 {email or user_id}。你能看到公共题库 + 自己的私有题;复习进度只属于你自己。"

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
def get_random_question(category: str = None, ctx: Context = None) -> dict:
    """Get a random interview question from the commons plus your own
    private questions (guests get the commons only). Optionally filter
    by category ('sde' or 'quant'); an unrecognized or omitted category
    draws from your whole visible bank. Returns id, category, question."""
    user_id, _ = current_identity(ctx)
    with get_connection() as conn:
        with conn.cursor() as cur:
            if category in ("sde", "quant"):
                cur.execute(
                    "SELECT id, category, question FROM questions "
                    "WHERE (user_id IS NULL OR user_id = %s) AND category = %s "
                    "ORDER BY random() LIMIT 1",
                    (user_id, category),
                )
            else:
                cur.execute(
                    "SELECT id, category, question FROM questions "
                    "WHERE user_id IS NULL OR user_id = %s "
                    "ORDER BY random() LIMIT 1",
                    (user_id,),
                )
            row = cur.fetchone()
    if row is None:
        return {"error": "题库为空。"}
    return {"id": row[0], "category": row[1], "question": row[2]}

@mcp.tool()
@db_errors
def get_hint(question_id: int, ctx: Context) -> str:
    """Get the hint for a question by its id (must be visible to you:
    commons, or your own private question)."""
    user_id, _ = current_identity(ctx)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT hint FROM questions WHERE id = %s AND (user_id IS NULL OR user_id = %s)",
                (question_id, user_id),
            )
            row = cur.fetchone()
    if row is None:
        return f"未找到 id={question_id} 的题目。"
    return row[0] or "此题暂无提示。"

@mcp.tool()
@db_errors
def get_answer(question_id: int, ctx: Context) -> str:
    """Get the full answer for a question by its id, if one exists
    (must be visible to you: commons, or your own private question)."""
    user_id, _ = current_identity(ctx)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT answer FROM questions WHERE id = %s AND (user_id IS NULL OR user_id = %s)",
                (question_id, user_id),
            )
            row = cur.fetchone()
    if row is None:
        return f"未找到 id={question_id} 的题目。"
    return row[0] or "此题暂无参考答案。"

@mcp.tool()
@db_errors
def mark_reviewed(question_id: int, ctx: Context) -> str:
    """Mark a question as reviewed BY YOU. Review progress is tracked
    per-identity (question_reviews), never on the question row itself —
    a commons question you review does not become "reviewed" for anyone
    else, guest or signed-in."""
    user_id, _ = current_identity(ctx)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM questions WHERE id = %s AND (user_id IS NULL OR user_id = %s)",
                (question_id, user_id),
            )
            if cur.fetchone() is None:
                return f"未找到 id={question_id} 的题目。"
            cur.execute(
                "INSERT INTO question_reviews (question_id, user_id) VALUES (%s, %s) "
                "ON CONFLICT (question_id, COALESCE(user_id, '')) DO UPDATE SET reviewed_at = now()",
                (question_id, user_id),
            )
            cur.execute(
                "SELECT count(*) FROM question_reviews WHERE user_id IS NOT DISTINCT FROM %s",
                (user_id,),
            )
            (reviewed_count,) = cur.fetchone()
    return f"题目{question_id}已标记为已复习,累计已复习{reviewed_count}题。"

@mcp.tool()
@db_errors
def add_question(category: str, question: str, hint: str, answer: str = None, ctx: Context = None) -> str:
    """Add a new question to the question bank. category must be 'sde'
    or 'quant'. Added as a guest -> goes into the shared commons; added
    while signed in -> private to you alone."""
    if category not in ("sde", "quant"):
        return "category 必须是 'sde' 或 'quant'。"
    user_id, _ = current_identity(ctx)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO questions (category, question, hint, answer, user_id) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (category, question, hint, answer, user_id),
            )
            (new_id,) = cur.fetchone()
    shelf = "你的私有题库" if user_id else "公共题库(commons)"
    return f"已添加题目,id={new_id}(加入{shelf})。"

@mcp.tool()
@db_errors
def get_quiz(n: int = 3, ctx: Context = None) -> list:
    """Get up to n randomly selected questions, visible to you (commons
    + your own), that YOU have already reviewed, for a daily review
    quiz. Returns fewer than n if not enough reviewed questions exist."""
    user_id, _ = current_identity(ctx)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT q.id, q.category, q.question FROM questions q "
                "JOIN question_reviews r ON r.question_id = q.id "
                "  AND r.user_id IS NOT DISTINCT FROM %s "
                "WHERE q.user_id IS NULL OR q.user_id = %s "
                "ORDER BY random() LIMIT %s",
                (user_id, user_id, n),
            )
            rows = cur.fetchall()
    return [{"id": r[0], "category": r[1], "question": r[2]} for r in rows]

from auth import register_oauth_routes  # OAuth AS endpoints — without this, claude.ai cannot sign in
register_oauth_routes(mcp)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
