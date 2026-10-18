"""The Control Tower agent: a LangGraph loop around a chat model (Gemini, Groq, Mistral, NVIDIA...).

    question -> agent (LLM chooses tools) -> tools -> agent -> ... -> answer
                              |
                              +-> approval (human) -> action tools

- The model never computes figures itself: it calls the analysis tools.
- Tools that produce a deliverable (e-mail draft, transport plan) pause the
  graph until a human approves or refuses.
- A recursion limit stops runaway loops; token usage is recorded per run.
"""
from __future__ import annotations

import os
import time
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from .agent_tools import ACTION_TOOL_NAMES, ALL_TOOLS

SYSTEM_PROMPT = """Tu es l'assistant du référent transport de la supply chain centrale d'AeroParts, \
un groupe aéronautique (données simulées) : 9 sites de production (TLS Toulouse, BOD Bordeaux, \
EVR Évry, LEH Le Havre, CHT Châtellerault, MEL Melun, NTE Nantes en France ; CAS Casablanca, \
TUN Tunis au Maghreb), des flux intersites et clients, tous les modes de transport.

Ton rôle : analyser les flux pour réduire les coûts et l'empreinte carbone, et préparer les \
décisions (consolidation, changement de mode, choix des transporteurs, plan de transport).

Règles métier connues :
- Une navette consolidée (routier + bateau) part de Toulouse vers Casablanca chaque mercredi, \
5 jours de transit ; les produits optroniques (FAM-OPT) n'y sont pas admis.
- Les flux clients au départ du Maghreb partent en aérien par règle de l'entreprise.
- L'express sert souvent à rattraper un retard de production : distingue toujours l'express \
évitable de l'express subi.

Règles de travail, sans exception :
1. Tous les chiffres viennent des outils. N'invente, n'arrondis à la louche ni ne calcules de tête.
2. Commence par l'outil le plus spécifique ; n'utilise run_sql qu'en dernier recours.
3. Si un outil renvoie une erreur, corrige ton appel et réessaie une fois.
4. Si les données ne permettent pas de répondre (prévision du futur, information absente), \
dis-le clairement et propose ce que tu peux faire à la place.
5. Tu ne modifies et ne supprimes jamais de données. Tu peux seulement préparer des brouillons \
(e-mail, plan), et un humain les valide avant enregistrement.
6. Tes économies sont des estimations sur la grille tarifaire de référence : dis-le.

Format de réponse, en français, concis :
**Réponse** (1 à 3 phrases)
**Chiffres clés** (puces, avec période et unité)
**Méthode** (les outils utilisés, en une ligne chacun)
**Limites** (si utile)
**Action proposée** (si utile, en euros et en CO2)"""


# Provider -> (API key variable, default model, default requests per second on the free tier).
PROVIDERS = {
    "google": ("GOOGLE_API_KEY", "gemini-flash-latest", 0.15),
    "groq": ("GROQ_API_KEY", "llama-3.3-70b-versatile", 0.4),
    "mistral": ("MISTRAL_API_KEY", "mistral-small-latest", 0.5),
    "nvidia": ("NVIDIA_API_KEY", "nvidia/nemotron-3.5-super-vl-120b-a12b", 0.5),
    # Any other service speaking the OpenAI API (OpenRouter, OVHcloud...): set its URL and LLM_MODEL.
    "openai_compatible": ("OPENAI_COMPATIBLE_API_KEY", "", 0.3),
}
BASE_URLS = {"nvidia": "https://integrate.api.nvidia.com/v1"}


def provider() -> str:
    name = os.environ.get("LLM_PROVIDER", "google").strip().lower()
    if name not in PROVIDERS:
        raise RuntimeError(f"LLM_PROVIDER={name!r} is not supported; use one of {', '.join(PROVIDERS)}")
    return name


def default_model() -> BaseChatModel:
    """The chat model chosen in .env (LLM_PROVIDER, LLM_MODEL), spaced to respect free-tier limits."""
    from langchain_core.rate_limiters import InMemoryRateLimiter

    name = provider()
    key_var, default, default_rps = PROVIDERS[name]
    if not os.environ.get(key_var):
        raise RuntimeError(f"{key_var} is not set (put it in a .env file, never in the code)")
    model = os.environ.get("LLM_MODEL") or default
    if not model:
        raise RuntimeError(f"LLM_MODEL must be set for LLM_PROVIDER={name}")
    rps = float(os.environ.get("LLM_REQUESTS_PER_SECOND") or default_rps)
    common = dict(temperature=0, max_retries=2, timeout=60,
                  rate_limiter=InMemoryRateLimiter(requests_per_second=rps, check_every_n_seconds=0.1))
    if name == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, **common)
    if name == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, **common)
    if name in ("nvidia", "openai_compatible"):
        from langchain_openai import ChatOpenAI
        base_url = BASE_URLS.get(name) or os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
        if not base_url:
            raise RuntimeError("OPENAI_COMPATIBLE_BASE_URL is not set (the service's .../v1 address)")
        return ChatOpenAI(model=model, base_url=base_url, api_key=os.environ[key_var], **common)
    from langchain_mistralai import ChatMistralAI
    return ChatMistralAI(model=model, **common)


def is_rate_limit(e: Exception) -> bool:
    """HTTP 429 from any provider (httpx, Google, Groq raise different exception types)."""
    code = getattr(getattr(e, "response", None), "status_code", None) or getattr(e, "status_code", None) \
        or getattr(e, "code", None)
    return code == 429 or "429" in str(e)[:200] or "RESOURCE_EXHAUSTED" in str(e)[:500]


def _invoke_with_backoff(llm, messages, attempts: int = 5):
    """Retry on HTTP 429 (rate limit): wait 5, 10, 20, 40 s."""
    for i in range(attempts):
        try:
            return llm.invoke(messages)
        except Exception as e:  # noqa: BLE001
            if not is_rate_limit(e) or i == attempts - 1:
                raise
            wait = 5 * 2 ** i
            print(f"[limite de débit atteinte, nouvel essai dans {wait} s]", flush=True)
            time.sleep(wait)


def build_graph(model: BaseChatModel | None = None, checkpointer: Any | None = None):
    llm = (model or default_model()).bind_tools(ALL_TOOLS)
    tool_node = ToolNode(ALL_TOOLS)

    def agent(state: MessagesState) -> dict:
        response = _invoke_with_backoff(llm, [SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    def route(state: MessagesState) -> str:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return END
        if any(c["name"] in ACTION_TOOL_NAMES for c in last.tool_calls):
            return "approval"
        return "tools"

    def approval(state: MessagesState) -> Command:
        last = state["messages"][-1]
        actions = [c for c in last.tool_calls if c["name"] in ACTION_TOOL_NAMES]
        decision = interrupt({"actions": [{"tool": c["name"], "args": c["args"]} for c in actions]})
        if isinstance(decision, dict) and decision.get("approved"):
            return Command(goto="tools")
        reason = decision.get("comment", "") if isinstance(decision, dict) else ""
        refused = [ToolMessage(content=f"Refusé par l'utilisateur. {reason}".strip(), tool_call_id=c["id"],
                               name=c["name"]) for c in last.tool_calls]
        return Command(goto="agent", update={"messages": refused})

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", tool_node)
    g.add_node("approval", approval, destinations=("tools", "agent"))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, ["tools", "approval", END])
    g.add_edge("tools", "agent")
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def usage(messages) -> dict:
    """Total tokens over the AI messages of a run."""
    tot = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}
    for m in messages:
        if isinstance(m, AIMessage):
            tot["llm_calls"] += 1
            u = getattr(m, "usage_metadata", None) or {}
            tot["input_tokens"] += u.get("input_tokens", 0)
            tot["output_tokens"] += u.get("output_tokens", 0)
    return tot
