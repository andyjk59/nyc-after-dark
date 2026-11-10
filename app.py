import asyncio
import json
import re
import time
import urllib.request
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import duckdb
import uvicorn
from agents import Agent, ModelSettings, RunContextWrapper, RunHooks, Runner, function_tool, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from context_panel import AgentContexts, PanelHooks, add_context_panel

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

# Traces upload to OpenAI by default, which needs an OpenAI key. We're on Gemini.
set_tracing_disabled(True)

# LiteLLM routes the call to Gemini. api_key is unused: Vertex AI uses your gcloud credentials.
MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash", api_key="unused")
# A stalled model call would hang the page: give up after 90 s, and retry twice.
SETTINGS = ModelSettings(extra_args={"vertex_location": "global", "timeout": 90, "num_retries": 2})

# The Lead gets this many researchers in all. Prompts ask for restraint; code enforces it.
MAX_RESEARCHERS = 8
MAX_ROWS = 40  # rows of a query result the model sees
# Lights-out scenario: share of overnight lighting assumed to stay on for egress, security and
# night shifts. An assumption, not a measurement: the agent must say so.
LIGHTS_OUT_RETAINED = 0.2

# Graders wait 5 minutes. Research stops after RESEARCH_SECONDS and the Writer works with whatever
# findings are in; if the Writer runs past WRITER_SECONDS, the findings are shown as they are.
RESEARCH_SECONDS = 180
WRITER_SECONDS = 75

# --- Data: one read-only DuckDB file, with no access to files or the network from SQL ---

DB = duckdb.connect(str(HERE / "data" / "nyc_overnight.duckdb"), read_only=True,
                    config={"enable_external_access": False, "lock_configuration": True})
TABLE_NAMES = [r[0] for r in DB.execute("SELECT table_name FROM information_schema.tables").fetchall()]
SCHEMA = (HERE / "data" / "schema.md").read_text()
NIGHTLIGHT_PERIODS = [r[0] for r in DB.execute("SELECT DISTINCT period FROM nightlights ORDER BY period").fetchall()]
LATEST_HOUR = DB.execute("SELECT max(ts_local) FROM nyiso_load").fetchone()[0]


def schema_for(tables: list[str]) -> str:
    """The schema.md sections for these tables."""
    sections = re.split(r"(?m)^## ", SCHEMA)[1:]
    return "\n".join("## " + s for s in sections if s.split(" ", 1)[0] in tables)


@dataclass
class Research:
    """One run's state, shared by every agent and tool in it. The model never sees it."""

    hooks: RunHooks
    researchers: int = 0
    findings: list[str] = field(default_factory=list)
    queries: dict[str, dict] = field(default_factory=dict)  # "Q3" -> {agent, sql, tables, rows}
    tool_calls: list[dict] = field(default_factory=list)  # the /chat payload: {name, args, result, agent}
    map: dict | None = None


# --- Tools ---


def fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:,.4g}" if abs(value) < 1000 else f"{value:,.0f}"
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


@function_tool
def run_sql(ctx: RunContextWrapper[Research], sql: str) -> str:
    """Run one read-only DuckDB SQL query on the NYC overnight energy database. Returns an id like
    Q3 to cite, and up to 40 rows. Aggregate in SQL: don't select raw rows you'd have to add up.

    Args:
        sql: A single SELECT (or WITH ... SELECT) statement.
    """
    statement = sql.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with|describe|summarize)\b", statement) or ";" in statement:
        return "Error: only a single SELECT, WITH, DESCRIBE or SUMMARIZE statement is allowed."
    try:
        cursor = DB.cursor()
        cursor.execute(statement)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(MAX_ROWS + 1)
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)[:600]}"
    qid = f"Q{len(ctx.context.queries) + 1}"
    agent = getattr(getattr(ctx, "agent", None), "name", "?")
    tables = sorted({t for t in TABLE_NAMES + ["nyiso_hourly", "overnight_daily", "hex_nightlights"]
                     if re.search(rf"\b{t}\b", statement)})
    ctx.context.queries[qid] = {"agent": agent, "sql": statement, "tables": tables, "rows": min(len(rows), MAX_ROWS)}
    lines = [" | ".join(columns)] + [" | ".join(fmt(v) for v in row) for row in rows[:MAX_ROWS]]
    more = f"\n(more than {MAX_ROWS} rows: aggregate further)" if len(rows) > MAX_ROWS else ""
    return f"{qid} ({min(len(rows), MAX_ROWS)} rows, tables: {', '.join(tables) or 'none'})\n" + "\n".join(lines) + more


@function_tool
def fetch_latest_nyiso(ctx: RunContextWrapper[Research], day: str) -> str:
    """Fetch one day of NYC (Zone J) hourly load live from NYISO, for days newer than the database.
    Returns an id like Q4 to cite.

    Args:
        day: The date, YYYY-MM-DD. NYISO posts yesterday's file early each morning.
    """
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
        url = f"http://mis.nyiso.com/public/csv/palIntegrated/{d:%Y%m%d}palIntegrated.csv"
        with urllib.request.urlopen(url, timeout=20) as r:
            text = r.read().decode()
    except Exception as e:
        return f"Error: could not fetch NYISO data for {day}: {type(e).__name__}: {e}"
    rows = [line.split(",") for line in text.splitlines()[1:] if '"N.Y.C."' in line]
    hours = [(r[0].strip('"')[11:16], float(r[4])) for r in rows if len(r) >= 5 and r[4]]
    if not hours:
        return f"Error: no N.Y.C. rows in NYISO's file for {day}."
    overnight = [mw for hhmm, mw in hours if hhmm[:2] in ("01", "02", "03", "04")]
    qid = f"Q{len(ctx.context.queries) + 1}"
    agent = getattr(getattr(ctx, "agent", None), "name", "?")
    ctx.context.queries[qid] = {"agent": agent, "sql": f"live NYISO file: {url}", "tables": ["NYISO live"], "rows": len(hours)}
    table = "\n".join(f"{hhmm} | {mw:,.0f}" for hhmm, mw in hours)
    floor = f"{sum(overnight) / len(overnight):,.0f} MW" if overnight else "n/a"
    return f"{qid} (live, {day})\n1-5 a.m. mean: {floor}\nhour | load_mw\n{table}"


MAP_LAYERS = ["est_overnight_lighting", "night_lights", "night_lights_change", "commercial_share"]


@function_tool
def show_on_map(ctx: RunContextWrapper[Research], layer: Literal["est_overnight_lighting", "night_lights",
                "night_lights_change", "commercial_share"], title: str, period: str | None = None,
                compare_to: str | None = None) -> str:
    """Draw one layer on the map of NYC next to the chat.

    Args:
        layer: est_overnight_lighting (modeled 1-5 a.m. commercial lighting), night_lights (satellite
            brightness for one period), night_lights_change (percent change from compare_to to period),
            commercial_share (share of floor space that is commercial).
        title: A short caption for the map, e.g. 'Satellite brightness, spring 2020 vs 2019'.
        period: For night_lights layers: '2013'..'2025' (yearly) or a month like '2020-04'.
        compare_to: For night_lights_change only: the earlier period, of the same kind as period.
    """
    if layer.startswith("night_lights"):
        if period not in NIGHTLIGHT_PERIODS:
            return f"Error: period must be one of {NIGHTLIGHT_PERIODS}"
        if layer == "night_lights_change" and (compare_to not in NIGHTLIGHT_PERIODS or len(compare_to) != len(period)):
            return "Error: compare_to must be an earlier period of the same kind (both years or both months)."
    else:
        period = compare_to = None
    ctx.context.map = {"layer": layer, "title": title, "period": period, "compare_to": compare_to}
    return "The map now shows: " + title


# --- Researchers: one per slice of the data ---

DOMAIN_NOTES = """\
Facts about the data:
- "Overnight" means 1-5 a.m. local time: hour_local / hour BETWEEN 1 AND 4 (hour-starting).
- Grid load (nyiso_*) and LL84 are MEASURED. ComStock (comstock_*) and hex_cells.est_* are MODELED.
  Black Marble (nightlights) is MEASURED BRIGHTNESS, not energy. Say which each number is.
- ComStock describes one typical year of today's building stock: it cannot show change across years.
- Citywide modeled overnight interior lighting, MW: SELECT upgrade, sum(interior_lighting_1_5am_mwh)/(365*4)
  FROM comstock_annual GROUP BY upgrade. upgrade 0 = baseline, 44 = Lighting Controls retrofit.
  hex_cells.est_* sums to less (about 94%): it only covers lots PLUTO can place. Use it for where, not how much.
- A lights-out scenario is not in the data. Its standard assumption here: {retained:.0%} of overnight lighting
  stays on for egress, security and night shifts. Call it an assumption.
- MW averaged over the 4 overnight hours x 4 x 365 / 1000 = GWh per year for those hours.
- The newest grid hour in the database is {latest}. For anything newer, use fetch_latest_nyiso.
- Compare Black Marble periods like with like: a year with a year, a month with the same month.
- Identify places by borough, ZIP code or community district from the data (hex_cells has them), or by
  the places table's landmarks and neighborhoods (JOIN places USING (hex)).
""".format(retained=LIGHTS_OUT_RETAINED, latest=LATEST_HOUR)

RESEARCHERS = {
    "grid": ("measured NYC electricity load and prices by hour, 2016 to yesterday; the 2020 shutdown",
             ["nyiso_load", "nyiso_price", "calendar", "nyiso_hourly", "overnight_daily", "constants"],
             [run_sql, fetch_latest_nyiso]),
    "lighting": ("modeled lighting and electricity by building type and borough, baseline vs the Lighting "
                 "Controls retrofit (NREL ComStock)",
                 ["comstock_hourly", "comstock_annual", "type_crosswalk", "constants"], [run_sql]),
    "buildings": ("tax-lot floor area by use (PLUTO), metered annual electricity per building (LL84), and "
                  "the map's hexagons with estimated lighting",
                  ["pluto", "ll84", "hex_cells", "places", "constants"], [run_sql]),
    "nightlights": ("satellite nighttime brightness per hexagon, yearly 2013-2025 and monthly for spring "
                    "2019, spring 2020 and Oct 2025-Sep 2026 (NASA Black Marble)",
                    ["nightlights", "hex_nightlights", "hex_cells", "places"], [run_sql]),
}


def make_researcher(kind: str, number: int) -> Agent:
    about, tables, tools = RESEARCHERS[kind]
    return Agent(
        name=f"{kind.title()} Researcher {number}",
        instructions=f"""\
You research one task using SQL on a DuckDB database about NYC's overnight electricity. Your slice:
{about}.

{DOMAIN_NOTES}
Your tables:
{schema_for(tables)}

1. Write focused, aggregated queries with run_sql. Use at most 6 queries. If one fails, read the error
   and fix it.
2. Reply with at most 8 bullets that answer your task, nothing else. Each bullet states a number with
   its unit, says MEASURED or MODELED, and ends with the query id it came from, e.g. [Q3].
   Round to 3 significant figures. Give percent changes when comparing.
3. Stick to your task: don't report numbers it didn't ask for.
4. If the data can't answer part of your task, say so in a last bullet, and why.""",
        model=MODEL,
        model_settings=SETTINGS,
        tools=tools,
    )


@function_tool
async def research(ctx: RunContextWrapper[Research], researcher: Literal["grid", "lighting", "buildings", "nightlights"],
                   task: str) -> str:
    """Send a researcher to answer one task with SQL on its slice of the data. Returns its findings,
    each citing a query id.

    Args:
        researcher: grid (measured load and prices), lighting (modeled lighting by building type),
            buildings (floor area, metered building electricity, map hexagons) or nightlights
            (satellite brightness over time).
        task: One specific thing to find out, with the numbers you need.
    """
    if ctx.context.researchers >= MAX_RESEARCHERS:
        return "No researchers left. Reply DONE."
    ctx.context.researchers += 1
    agent = make_researcher(researcher, ctx.context.researchers)
    result = await Runner.run(agent, task, context=ctx.context, hooks=ctx.context.hooks, max_turns=10)
    ctx.context.findings.append(f"{agent.name}: {task}\n\n{result.final_output}")
    return str(result.final_output)


# --- Lead and Writer ---

lead = Agent(
    name="Lead",
    instructions=f"""\
You lead a research team answering questions about energy NYC uses, and wastes, overnight.

"Waste" can mean three things. Level 1: all overnight load (measured; includes homes, hospitals and
subways, so it's a ceiling, not waste). Level 2: commercial lighting on at night (modeled). Level 3:
avoidable lighting, i.e. level 2 minus what a Lighting Controls retrofit or a lights-out rule would
leave on (a modeled range).

Your researchers: {"; ".join(f"{k}: {v[0]}" for k, v in RESEARCHERS.items())}.

1. If the question is too vague to research, for example it asks how much is "wasted" without saying
   which kind, reply with CLARIFY: and one short question offering 2 to 4 options (one can be "all of
   them"). Do this at most once: if the input includes a clarification, research it. Concrete
   questions never need clarifying.
2. If the question has nothing to do with NYC energy, lighting or buildings, reply OUT_OF_SCOPE: and
   one sentence on what you can answer instead.
3. Otherwise split the question into 2 to 4 tasks and call research once per task, all in the same
   turn, so the researchers work in parallel. Say exactly which numbers each task needs.
4. Read the findings. If part of the question is still unanswered, or two findings disagree, call
   research again for just that part. Do this at most twice.
5. When the question is covered, reply DONE and nothing else: a writer writes the answer.""",
    model=MODEL,
    model_settings=SETTINGS,
    tools=[research],
)

writer = Agent(
    name="Writer",
    instructions=f"""\
Write the answer in markdown, using only the findings you're given.

- Start with a short paragraph that answers the question directly, with the key numbers.
- Then a few short sections or bullets with the supporting numbers.
- End every sentence that states a number with the query id(s) from its finding, e.g. [Q3] or [Q2, Q5].
  Never invent a query id or a number.
- Label numbers as measured or modeled. Call the lights-out share an assumption.
- End with a section "What this data can't tell you" (1 to 3 bullets), e.g. which building leaves lights
  on (no building-level night data), or change over time in modeled numbers.
- Answer the question asked. Leave out findings that don't bear on it.
- Keep it under about 350 words. Round to 3 significant figures.
- Plain, specific sentences. No "it is worth noting", "pivotal" or "crucial".

Before writing, call show_on_map once with the layer that best shows the answer:
est_overnight_lighting for where modeled lighting is, night_lights or night_lights_change for satellite
brightness (periods: {", ".join(NIGHTLIGHT_PERIODS)}), commercial_share for building mix.""",
    model=MODEL,
    model_settings=SETTINGS,
    tools=[show_on_map],
)


class Hooks(PanelHooks):
    """The context panel's hooks, plus a record of every tool call for the /chat payload."""

    def __init__(self, contexts, emit, run: Research | None = None):
        super().__init__(contexts, emit)
        self.run = run

    async def on_tool_end(self, context, agent, tool, result):
        args = getattr(context, "tool_arguments", None)
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except ValueError:
            pass
        call = {"name": tool.name, "args": args, "result": str(result)[:4000], "agent": agent.name}
        self.run.tool_calls.append(call)
        self.emit({"type": "tool", "agent": agent.name, "name": tool.name, "args": args})
        if tool.name == "show_on_map" and self.run.map:
            self.emit({"type": "map", "map": self.run.map})


def sources(report: str, queries: dict[str, dict]) -> str:
    """A Sources section for the query ids the report cites, built from the record, not the model."""
    cited = sorted(set(re.findall(r"Q\d+", " ".join(re.findall(r"\[(Q\d+(?:\s*,\s*Q\d+)*)\]", report)))),
                   key=lambda q: int(q[1:]))
    if not cited:
        return ""
    lines = ["", "---", "**Sources** (every query the answer cites)", ""]
    for q in cited:
        info = queries.get(q)
        if info is None:
            lines.append(f"- **{q}**: ⚠️ no such query was run. Treat the sentence citing it as unsupported.")
        else:
            lines.append(f"- **{q}** · {info['agent']} · {', '.join(info['tables'])}\n  `{' '.join(info['sql'].split())}`")
    return "\n".join(lines)


async def answer(question: str, session: dict, hooks: Hooks) -> dict:
    run = hooks.run
    pending = session.get("pending")
    if pending:
        prompt = f"Question: {pending}\nClarification from the user: {question}"
        full_question, session["pending"] = f"{pending} ({question})", None
    else:
        prompt, full_question = f"Question: {question}", question
    if session["history"]:
        earlier = "\n".join(f"- {q}\n  {a[:400]}" for q, a in session["history"][-2:])
        prompt = f"Earlier in this conversation:\n{earlier}\n\n{prompt}"

    try:
        result = await asyncio.wait_for(Runner.run(lead, prompt, context=run, hooks=hooks, max_turns=12), RESEARCH_SECONDS)
        lead_says, timed_out = str(result.final_output).strip(), False
    except asyncio.TimeoutError:  # the builtin TimeoutError only from Python 3.11
        # Researchers still running are cancelled; finished ones have already filed their findings.
        lead_says, timed_out = "DONE", True
        n = len(run.findings)
        hooks.emit({"type": "notice", "text": f"Time limit ({RESEARCH_SECONDS} s) reached: writing up what "
                    f"{n} finished researcher{'' if n == 1 else 's'} found"})
    if lead_says.startswith("CLARIFY:") and not pending:
        session["pending"] = question
        return {"response": lead_says.removeprefix("CLARIFY:").strip(), "kind": "clarify"}
    if lead_says.startswith("OUT_OF_SCOPE:"):
        return {"response": lead_says.removeprefix("OUT_OF_SCOPE:").strip(), "kind": "out_of_scope"}
    if not run.findings:
        if timed_out:
            return {"response": f"No researcher finished within the {RESEARCH_SECONDS}-second time limit. "
                    "Try a narrower question, e.g. one borough, one year or one kind of data.", "kind": "timeout"}
        return {"response": "The researchers came back with nothing to report. Try a more specific question.", "kind": "empty"}

    findings = "\n\n---\n\n".join(run.findings)
    note = ("\n\nNote: research hit its time limit before every task finished. Say so in one sentence near the "
            "top, and name what may be missing.") if timed_out else ""
    try:
        report = await asyncio.wait_for(Runner.run(writer, f"Question: {full_question}{note}\n\nFindings:\n\n{findings}",
                                                   context=run, hooks=hooks, max_turns=4), WRITER_SECONDS)
        text = str(report.final_output).strip()
    except asyncio.TimeoutError:  # the builtin TimeoutError only from Python 3.11
        text = ("*The writer ran out of time, so here are the researchers' findings as they reported them.*\n\n"
                + "\n\n".join("**" + f.split(":", 1)[0] + "**\n\n" + f.split("\n\n", 1)[-1] for f in run.findings))
    session["history"].append((full_question, text))
    return {"response": text + sources(text, run.queries), "kind": "answer"}


# --- FastAPI App ---

sessions: dict[str, dict] = {}  # session_id -> {"pending": question awaiting clarification, "history": [(q, a)]}
contexts: dict[str, AgentContexts] = {}  # run_id -> every agent's context, for the agent timeline page
MAX_SESSIONS, MAX_CONTEXTS = 2000, 300  # oldest are dropped, so memory stays bounded


def trim(store: dict, limit: int):
    while len(store) > limit:
        store.pop(next(iter(store)))  # dicts keep insertion order: this is the oldest


# --- Limits: every run spends Gemini credits, and the URL is public ---

PER_IP_AT_ONCE, PER_IP_PER_HOUR = 2, 20
ALL_AT_ONCE, ALL_PER_HOUR = 6, 150
MAX_MESSAGE = 1000  # characters
running: Counter = Counter()  # ip -> runs in progress
started: dict[str, deque] = defaultdict(deque)  # ip, or "*" for everyone -> start times in the last hour


def client_ip(request: Request) -> str:
    # Cloud Run puts the caller's address in X-Forwarded-For. A caller can prepend fake entries to dodge
    # the per-IP limit, but not the overall limits, which cap total spend either way.
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "?")


def admit(ip: str) -> str | None:
    """Start a run for this caller, or say why not."""
    now = time.monotonic()
    for key in (ip, "*"):
        while started[key] and now - started[key][0] > 3600:
            started[key].popleft()
    if len(started) > 10_000:  # forget callers with nothing in the last hour
        for key in [k for k, q in started.items() if not q and k not in (ip, "*")]:
            del started[key]
    if running[ip] >= PER_IP_AT_ONCE:
        return f"You already have {PER_IP_AT_ONCE} questions running. Wait for one to finish, or stop it."
    if len(started[ip]) >= PER_IP_PER_HOUR:
        minutes = int((3600 - (now - started[ip][0])) / 60) + 1
        return f"That's {PER_IP_PER_HOUR} questions in the last hour, the limit per visitor. Try again in {minutes} min."
    if sum(running.values()) >= ALL_AT_ONCE or len(started["*"]) >= ALL_PER_HOUR:
        return "The site is busy right now. Try again in a few minutes."
    running[ip] += 1
    started[ip].append(now)
    started["*"].append(now)
    return None


def release(ip: str):
    running[ip] -= 1
    if running[ip] <= 0:
        del running[ip]

app = FastAPI()
app.add_middleware(GZipMiddleware, minimum_size=2000)  # the hexagon grid is 2.3 MB, ~0.5 MB gzipped
add_context_panel(app, contexts)


def get_session(session_id: str | None) -> tuple[str, dict]:
    session_id = session_id or str(uuid.uuid4())
    return session_id, sessions.setdefault(session_id, {"pending": None, "history": []})


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]
    map: dict | None = None
    run_id: str | None = None


async def chat_once(message: str, session_id: str | None, emit=lambda event: None) -> ChatResponse:
    session_id, session = get_session(session_id)
    run_id = str(uuid.uuid4())
    contexts[run_id] = AgentContexts()
    trim(contexts, MAX_CONTEXTS)
    trim(sessions, MAX_SESSIONS)
    hooks = Hooks(contexts[run_id], emit)
    run = Research(hooks=hooks)
    hooks.run = run
    emit({"type": "start", "run_id": run_id, "session_id": session_id})
    try:
        out = await answer(message, session, hooks)
        response = out["response"]
    except Exception as e:
        # Auth, quota, a model that won't stop: show it in the chat, not as a 500.
        response = f"Something went wrong while researching: {type(e).__name__}: {str(e)[:400]}"
    return ChatResponse(response=response, session_id=session_id, tool_calls=run.tool_calls, map=run.map, run_id=run_id)


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request):
    ip = client_ip(request)
    if refused := admit(ip):
        raise HTTPException(429, refused)
    try:
        return await chat_once(body.message, body.session_id)
    finally:
        release(ip)


@app.get("/run")
async def run_stream(request: Request, message: str = Query(min_length=1, max_length=MAX_MESSAGE),
                     session_id: str | None = None):
    """The same as /chat, streamed as Server-Sent Events so the page can show progress."""
    ip = client_ip(request)
    if refused := admit(ip):
        # An HTTP error would reach the page only as a dropped connection: send the reason as an event.
        refusal = json.dumps({"type": "error", "message": refused})
        return StreamingResponse(iter([f"data: {refusal}\n\n"]), media_type="text/event-stream")
    queue: asyncio.Queue = asyncio.Queue()

    async def work():
        try:
            response = await chat_once(message, session_id, queue.put_nowait)
            queue.put_nowait({"type": "done", **response.model_dump()})
        finally:
            release(ip)  # also on Stop: cancelling the task lands here

    task = asyncio.create_task(work())

    async def events():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] == "done":
                    break
        finally:
            task.cancel()  # the page closed the stream: stop spending tokens

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/map_layer")
def map_layer(layer: str, period: str | None = None, compare_to: str | None = None):
    """Values per hexagon for one map layer."""
    if layer == "est_overnight_lighting":
        rows = DB.execute("SELECT hex, est_lighting_base_mw * 1000 FROM hex_cells WHERE est_lighting_base_mw > 0").fetchall()
        meta = {"unit": "kW", "label": "Estimated commercial lighting, 1-5 a.m.",
                "caveat": "Modeled: ComStock intensities spread over PLUTO floor area. Not measured."}
    elif layer == "commercial_share":
        rows = DB.execute("SELECT hex, commercial_share * 100 FROM hex_cells WHERE bldgarea_sqft > 0").fetchall()
        meta = {"unit": "%", "label": "Commercial share of floor area", "caveat": "PLUTO 26v2."}
    elif layer == "night_lights" and period in NIGHTLIGHT_PERIODS:
        rows = DB.execute("SELECT hex, radiance_mean FROM hex_nightlights WHERE period = ? AND radiance_mean IS NOT NULL", [period]).fetchall()
        meta = {"unit": "nW/cm²/sr", "label": f"Satellite brightness, {period}",
                "caveat": "Measured by NASA Black Marble at ~1:30 a.m. solar time; includes streets and signs. Pixels are ~460 m, so values between them are interpolated."}
    elif layer == "night_lights_change" and period in NIGHTLIGHT_PERIODS and compare_to in NIGHTLIGHT_PERIODS:
        rows = DB.execute("""SELECT a.hex, (a.radiance_mean - b.radiance_mean) / b.radiance_mean * 100
            FROM hex_nightlights a JOIN hex_nightlights b USING (hex)
            WHERE a.period = ? AND b.period = ? AND b.radiance_mean > 1 AND a.radiance_mean IS NOT NULL""",
                          [period, compare_to]).fetchall()
        meta = {"unit": "%", "label": f"Change in satellite brightness, {compare_to} → {period}", "diverging": True,
                "caveat": "Measured brightness, not energy. Clouds and snow add noise to monthly values."}
    else:
        raise HTTPException(400, f"Unknown layer or period. Layers: {MAP_LAYERS}; periods: {NIGHTLIGHT_PERIODS}")
    return {**meta, "layer": layer, "values": {h: round(v, 2) for h, v in rows}}


@app.get("/hex_info")
def hex_info(hex: str):
    row = DB.execute("""SELECT c.*, n.radiance_mean AS radiance_2025 FROM hex_cells c
        LEFT JOIN hex_nightlights n ON n.hex = c.hex AND n.period = '2025' WHERE c.hex = ?""", [hex]).fetchone()
    if row is None:
        raise HTTPException(404, "No such hexagon")
    columns = [d[0] for d in DB.execute("SELECT c.*, 0 AS radiance_2025 FROM hex_cells c LIMIT 0").description]
    return dict(zip(columns, row))


@app.get("/meta")
def meta():
    return {"periods": NIGHTLIGHT_PERIODS, "layers": MAP_LAYERS, "latest_grid_hour": LATEST_HOUR}


@app.get("/places.geojson")
def places():
    rows = DB.execute("SELECT name, kind, lat, lon, hex FROM places").fetchall()
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": n, "kind": k, "hex": h}, "geometry": {"type": "Point", "coordinates": [lo, la]}}
        for n, k, la, lo, h in rows]}


@app.get("/hex_grid.geojson")
def hex_grid():
    # no-cache: a rebuild can change the grid, and stale hexagon ids match no values
    return FileResponse(HERE / "data" / "hex_grid.geojson", media_type="application/geo+json",
                        headers={"Cache-Control": "no-cache"})


@app.get("/trace")
def trace():
    return FileResponse(HERE / "trace.html", headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    # no-cache: browsers otherwise keep serving an old page after a redeploy
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
