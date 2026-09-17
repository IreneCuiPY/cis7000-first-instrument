"""your-first-instrument — a sense of time for a model that has none.

Why time? Ask your Claude "how long have we been talking?" WITHOUT this
connected. It can only guess: no clock lives in a context window. This
server is the smallest honest fix — and the pattern generalizes to any
instrument you can imagine. See docs/adr/ for every choice made here.
"""
import os
import random
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "your-first-instrument",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)

QUESTION_BANK = [
    {"id": 1, "category": "sde", "question": "Two Sum: 给一个整数数组和一个目标值,找出数组中两个数之和等于目标值的下标。", "hint": "用哈希表存已遍历过的数字,一次遍历完成。"},
    {"id": 2, "category": "sde", "question": "反转链表:就地反转一个单链表。", "hint": "用三个指针(prev, curr, next)迭代反转。"},
    {"id": 3, "category": "sde", "question": "有效括号:判断字符串中的括号是否正确配对闭合。", "hint": "用栈,遇到左括号入栈,遇到右括号检查栈顶是否匹配。"},
    {"id": 4, "category": "sde", "question": "最大子数组和:找一个整数数组中连续子数组的最大和。", "hint": "Kadane's Algorithm,维护当前子数组和与全局最大值。"},
    {"id": 5, "category": "sde", "question": "二叉树层序遍历:用BFS按层遍历一棵二叉树。", "hint": "用队列,每次处理完当前层所有节点再进入下一层。"},
    {"id": 6, "category": "sde", "question": "合并两个有序数组:把两个已排序数组合并成一个有序数组。", "hint": "双指针从头或从尾比较合并。"},
    {"id": 7, "category": "sde", "question": "判断链表是否有环:检测一个链表中是否存在环。", "hint": "快慢指针,若相遇则有环。"},
    {"id": 8, "category": "quant", "question": "抛硬币直到出现正面,期望要抛几次?", "hint": "设期望为E,E = 1 + 0.5*E,解得E=2。"},
    {"id": 9, "category": "quant", "question": "三门问题(Monty Hall):换门是否能提高中奖概率?", "hint": "换门中奖概率2/3,不换是1/3,主持人排除信息改变了条件概率。"},
    {"id": 10, "category": "quant", "question": "生日问题:一个房间最少多少人,让至少两人生日相同的概率超过50%?", "hint": "约23人,用补集计算所有人生日都不同的概率。"},
    {"id": 11, "category": "quant", "question": "掷一个六面骰子,期望点数是多少?", "hint": "E[X] = (1+2+3+4+5+6)/6 = 3.5。"},
    {"id": 12, "category": "quant", "question": "掷两个骰子,点数之和等于7的概率是多少?", "hint": "共36种组合,和为7有6种(1+6,2+5,3+4,4+3,5+2,6+1),概率1/6。"},
    {"id": 13, "category": "quant", "question": "已知E[X]=3, E[Y]=5,求E[X+Y]。", "hint": "期望的线性性,E[X+Y]=E[X]+E[Y]=8,与X、Y是否独立无关。"},
    {"id": 14, "category": "quant", "question": "12个球中有一个坏球(重量不同),用天平最少称几次能找出坏球并确定它更重还是更轻?", "hint": "3次,每次把球分成三组比较。"},
    {"id": 15, "category": "quant", "question": "某疾病发病率1%,检测准确率99%(真阳性率和真阴性率都是99%),某人检测阳性,他真正患病的概率是多少?", "hint": "用贝叶斯定理,答案约为50%,远低于直觉上的99%。"},
]
QUESTIONS_BY_ID = {q["id"]: q for q in QUESTION_BANK}
REVIEWED_TODAY = set()

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
def get_random_question(category: str = None) -> dict:
    """Get a random interview question. Optionally filter by category
    ('sde' or 'quant'); an unrecognized or omitted category draws from
    the full question bank. Returns id, category, and question text."""
    pool = [q for q in QUESTION_BANK if q["category"] == category]
    if not pool:
        pool = QUESTION_BANK
    q = random.choice(pool)
    return {"id": q["id"], "category": q["category"], "question": q["question"]}

@mcp.tool()
def get_hint(question_id: int) -> str:
    """Get the hint for a question by its id."""
    q = QUESTIONS_BY_ID.get(question_id)
    if q is None:
        return f"未找到 id={question_id} 的题目。"
    return q["hint"]

@mcp.tool()
def mark_reviewed(question_id: int) -> str:
    """Mark a question as reviewed."""
    if question_id not in QUESTIONS_BY_ID:
        return f"未找到 id={question_id} 的题目。"
    REVIEWED_TODAY.add(question_id)
    return f"题目{question_id}已标记为已复习,今日已复习{len(REVIEWED_TODAY)}题。"

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
