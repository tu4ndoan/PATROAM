"""The tools Gemini can call, and what they do.

Division of labour: Gemini Live decides WHICH tool and with WHAT arguments (it
is the one that heard you). The work itself runs here against PATROAM's existing
skills — the same calendar, task, project and graph code the typed interface
uses — with your chosen model writing anything that needs composing.

Each handler returns a SHORT string. Gemini reads it aloud, and every second of
audio it produces is billed, so brevity is a cost decision as much as a style
one.
"""

from .. import config


def _write(prompt, system, timeout=20):
    """Compose a spoken answer with whichever worker model you picked."""
    from .llm import worker
    try:
        w = worker()
        return w.complete(prompt, system=system, timeout=timeout, max_tokens=150) or ""
    except Exception:
        return ""

# Declarations sent to Gemini at session setup.
DECLARATIONS = [
    {"name": "get_calendar",
     "description": "Xem lịch / sự kiện sắp tới của người dùng.",
     "parameters": {"type": "object", "properties": {
         "when": {"type": "string",
                  "description": "Khoảng thời gian theo lời người dùng: hôm nay, ngày mai, thứ 6, tuần này."}},
         "required": ["when"]}},
    {"name": "add_calendar_event",
     "description": "Thêm một sự kiện vào lịch.",
     "parameters": {"type": "object", "properties": {
         "title": {"type": "string", "description": "Tên sự kiện"},
         "when": {"type": "string", "description": "Ngày giờ theo lời người dùng"}},
         "required": ["title", "when"]}},
    {"name": "get_tasks",
     "description": "Liệt kê việc cần làm, theo thứ tự ưu tiên và hạn chót.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "add_task",
     "description": "Thêm một việc vào danh sách cần làm.",
     "parameters": {"type": "object", "properties": {
         "title": {"type": "string"},
         "due": {"type": "string", "description": "Hạn chót theo lời người dùng (nếu có)"}},
         "required": ["title"]}},
    {"name": "complete_task",
     "description": "Đánh dấu một việc đã hoàn thành.",
     "parameters": {"type": "object", "properties": {
         "title": {"type": "string"}}, "required": ["title"]}},
    {"name": "project_status",
     "description": "Tóm tắt tiến độ một dự án, hoặc tất cả dự án nếu không nêu tên.",
     "parameters": {"type": "object", "properties": {
         "project": {"type": "string"}}}},
    {"name": "briefing",
     "description": "Bản tóm tắt đầu ngày: lịch, việc, dự án, tin tức.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "recall",
     "description": "Tra cứu điều PATROAM đã ghi nhớ về một chủ đề hoặc về người dùng.",
     "parameters": {"type": "object", "properties": {
         "topic": {"type": "string"}}, "required": ["topic"]}},
    {"name": "create_project",
     "description": ("Tạo một DỰ ÁN mới đầy đủ: thư mục + plan.md + README + git, "
                     "đẩy roadmap lên ClickUp và tạo kênh Slack riêng. Dùng khi "
                     "người dùng muốn bắt đầu / khởi tạo / lập kế hoạch một dự án."),
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Tên dự án"},
         "kind": {"type": "string", "description": "Loại: web, game, app, tool…"},
         "description": {"type": "string", "description": "Mô tả dự án làm gì"},
         "prototype": {"type": "boolean",
                       "description": "true = bản thử nhanh, false = làm chuẩn production"}},
         "required": ["name"]}},
    {"name": "resume_project",
     "description": "Mở lại một dự án đang làm dở: git, task, việc tiếp theo.",
     "parameters": {"type": "object", "properties": {
         "project": {"type": "string"}}, "required": ["project"]}},
]


def _short(text, limit=320):
    """Trim to something speakable. Long text costs money and patience."""
    t = " ".join((text or "").split())
    return t[:limit]


# Language of the conversation right now, set by the session from what it heard.
# Without this the summaries came back in Vietnamese regardless, and Gemini kept
# drifting back to Vietnamese to match them.
_LANG = {"code": "en"}   # English until he actually speaks Vietnamese

_VN_CHARS = set("ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệ"
                "ìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")


def set_language(code):
    _LANG["code"] = "vi" if (code or "").lower().startswith("vi") else "en"


def detect_language(text):
    """Vietnamese if it carries Vietnamese diacritics — cheap and reliable enough
    to pick which language the answer should be written in."""
    t = (text or "").lower()
    return "vi" if any(c in _VN_CHARS for c in t) else "en"


def _summarise(raw, ask):
    """Turn a data dump into one spoken sentence, in the current language."""
    if not raw:
        return ""
    vi = _LANG["code"] == "vi"
    system = ("Bạn viết câu trả lời NÓI cho một trợ lý giọng nói tiếng Việt. "
              "Tối đa 2 câu ngắn, tự nhiên, không markdown, không liệt kê dài."
              if vi else
              "You write the SPOKEN reply for a voice assistant, in English. "
              "At most 2 short natural sentences, no markdown, no long lists.")
    out = _write(
        (f"Dữ liệu:\n{raw[:2500]}\n\nYêu cầu: {ask}" if vi
         else f"Data:\n{raw[:2500]}\n\nTask: {ask}\n(Answer in English.)"),
        system=system, timeout=15)
    return _short(out or raw)


# ── handlers ──────────────────────────────────────────────────────────────────
def _get_calendar(args):
    from .. import skills
    r = skills._calendar_read(args.get("when") or "hôm nay")
    raw = r.get("show") or r.get("say") or ""
    return ({"speak": _summarise(raw, "Nói ngắn có những gì trong lịch."),
             "detail": raw}, "calendar")


def _add_calendar_event(args):
    from .. import skills
    r = skills._calendar_add(args.get("title", ""), args.get("when", ""))
    return _summarise(r.get("show") or r.get("say") or "",
                      "Xác nhận ngắn gọn đã thêm gì vào lịch."), "calendar"


def _get_tasks(args):
    from .. import gcal
    if not gcal.available():
        return "Google Tasks chưa kết nối.", "todo"
    snap = gcal.tasks_snapshot()
    open_t, c = snap["open"], snap["counts"]
    if not open_t:
        return "Anh không còn việc nào.", "todo"
    lines = "\n".join(f"- {t['title']}" + (f" (hạn {t['when']})" if t["when"] else "")
                      + (" [QUÁ HẠN]" if t["overdue"] else "") for t in open_t[:10])
    raw = f"{c.get('open',0)} việc mở, {c.get('overdue',0)} quá hạn:\n{lines}"
    return ({"speak": _summarise(raw, "Nói ngắn còn bao nhiêu việc và 2 việc ưu tiên nhất."),
             "detail": raw}, "todo")


def _add_task(args):
    from .. import skills
    r = skills._todo_add(args.get("title", ""), args.get("due", ""))
    return _summarise(r.get("show") or r.get("say") or "",
                      "Xác nhận ngắn gọn đã thêm việc gì."), "todo"


def _complete_task(args):
    from .. import skills
    r = skills._todo_done(args.get("title", ""))
    return _summarise(r.get("show") or r.get("say") or "",
                      "Xác nhận đã xong việc gì và còn lại bao nhiêu."), "todo"


def _project_status(args):
    name = (args.get("project") or "").strip()
    from .. import manage
    if name:
        v = manage.project_view(name)
        if not v.get("found"):
            return f"Không tìm thấy dự án {name}.", "project"
        g, tk = v.get("git") or {}, (v.get("tasks") or {}).get("counts") or {}
        raw = (f"Dự án {v['name']}: nhánh {g.get('branch','?')}, "
               f"commit cuối {g.get('last_commit','?')}, "
               f"{tk.get('done',0)}/{tk.get('total',0)} task xong.")
        return _summarise(raw, "Tóm tắt tiến độ dự án trong 1-2 câu."), ("project:" + v["name"])
    rows = []
    for rec in manage.discover_projects():
        pr = manage.project_progress(rec)
        rows.append(f"{rec['name']}: {pr.get('done',0)}/{pr.get('total',0)}")
    return _summarise("\n".join(rows), "Nói ngắn tình hình chung các dự án."), "graph"


def _briefing(args):
    from .. import briefing
    rep = briefing.gather()
    if not rep:
        return "Chưa có gì đáng báo cáo.", None
    return (_summarise(rep.get("show") or rep.get("say") or "",
                       "Tóm tắt đầu ngày trong 2 câu: việc quan trọng nhất hôm nay."),
            "chat")


def _recall(args):
    from .. import graph
    topic = (args.get("topic") or "").strip()
    facts = graph.render_for(topic, limit=12) or graph.user_summary()
    return _summarise(facts, f"Trả lời ngắn điều đã biết về: {topic}"), ("graph:" + topic)


def _create_project(args):
    """Full project creation — the same path the typed 'create a project' uses."""
    from .. import planner
    name = (args.get("name") or "").strip()
    if not name:
        return "Dự án tên gì ạ?", None
    r = planner.create_project(
        name, kind=args.get("kind", ""), description=args.get("description", ""),
        prototype=args.get("prototype"))
    # Speak the short line, but put the FULL result in the chat: paths, ClickUp
    # link and Slack channel are things you need to see, not hear.
    return {"speak": _short(r.get("say") or ""), "detail": r.get("show") or ""}, "graph"


def _resume_project(args):
    from .. import manage
    name = (args.get("project") or "").strip()
    if not name:
        return "Dự án nào ạ?", None
    r = manage.resume(name)
    if isinstance(r, str):
        return r, "graph"
    return ({"speak": _short(r.get("say") or ""), "detail": r.get("show") or ""},
            "project:" + name)


HANDLERS = {
    "get_calendar": _get_calendar,
    "add_calendar_event": _add_calendar_event,
    "get_tasks": _get_tasks,
    "add_task": _add_task,
    "complete_task": _complete_task,
    "project_status": _project_status,
    "briefing": _briefing,
    "recall": _recall,
    "create_project": _create_project,
    "resume_project": _resume_project,
}


def run(name, args):
    """Execute a tool call → {"text": spoken reply, "ui": panel to open}.

    The `ui` hint is what makes the screen follow the conversation: asking about
    tasks slides the TODO panel out, asking about a project opens it in the
    graph. Returning only text left PATROAM talking about things the interface
    never showed."""
    fn = HANDLERS.get(name)
    if not fn:
        return {"text": f"Chưa hỗ trợ {name}.", "detail": "", "ui": None}
    try:
        out = fn(args or {})
        payload, ui = out if isinstance(out, tuple) else (out, None)
        # Handlers may return either a plain string, or {"speak", "detail"} when
        # the chat should show more than the voice says.
        if isinstance(payload, dict):
            return {"text": payload.get("speak") or "Xong.",
                    "detail": payload.get("detail") or "", "ui": ui}
        return {"text": payload or "Xong.", "detail": "", "ui": ui}
    except Exception as e:
        return {"text": f"Lỗi khi {name}: {type(e).__name__}", "detail": "", "ui": None}
